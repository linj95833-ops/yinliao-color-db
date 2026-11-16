"""本地上传界面：标准库 http.server，无新增依赖。

默认只监听 127.0.0.1；用 `--lan`（或 --host 0.0.0.0）开放局域网供手机访问，
启动时会打印手机可直接打开的地址。
前端把图片读成 data URL 后 POST JSON，避免 Python 3.13 已移除的 cgi 模块。
上传的照片只在本地内存/磁盘报告中使用，不对外传输。
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image

from . import compose, config, db, matcher, portrait

INDEX_FILE = config.WEB_DIR / "index.html"
MAX_UPLOAD = 24 * 1024 * 1024  # 手机原图 base64 会膨胀约 1/3，前端已自动压缩，这里留足兜底
_DATA_URL = re.compile(r"^data:image/(png|jpe?g|webp|gif|bmp);base64,", re.I)


def _decode_image(data_url: str) -> Image.Image:
    if not isinstance(data_url, str) or not _DATA_URL.match(data_url):
        raise ValueError("请上传 PNG/JPEG/WebP/GIF/BMP 图片")
    payload = data_url.partition(",")[2]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片 base64 数据无效") from exc
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError("图片为空或超过 12MB")
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        return portrait.load_image(im)


def _compose_public(info: dict | None) -> dict | None:
    """compose 结果里的 PIL.Image 不能 JSON 序列化，去掉它。"""
    if info is None:
        return None
    return {k: v for k, v in info.items() if k != "image"}


_CATEGORY_CN = {
    "carbonated": "碳酸", "tea": "茶", "water": "水", "sports": "运动",
    "dairy": "乳饮", "juice": "果汁", "energy": "能量", "coffee": "咖啡",
    "herbal": "草本", "soy": "豆饮",
}
_REGION_CN = {"cn": "国内", "intl": "国际"}


def _stats() -> dict:
    from . import depth, garment, human, wardrobe  # 延迟导入：不触发大模型加载

    with db.connect() as con:
        origins = {r["origin"]: r["n"] for r in con.execute(
            "SELECT origin, COUNT(*) AS n FROM brand_color_profile GROUP BY origin"
        )}
        return {
            "wardrobe": wardrobe.status(),
            "brands": con.execute("SELECT COUNT(*) FROM brands").fetchone()[0],
            "images": con.execute(
                "SELECT COUNT(*) FROM images WHERE status = 'ok'").fetchone()[0],
            "profiles": sum(origins.values()),
            "profiles_from_images": origins.get("images", 0),
            "profiles_seed_only": origins.get("reference", 0),
            "human": human.status(),
            "depth": depth.status(),
            "garments": garment.status(),
            "hd_sources": (len(list(config.HD_IMAGE_DIR.rglob("*.webp")))
                           if config.HD_IMAGE_DIR.is_dir() else 0),
        }


def _brands() -> dict:
    """全部可选饮料：给前端的下拉列表用，附主色、微型色板与中文品类/地区。"""
    items = []
    for r in db.get_profiles():
        try:
            vec = json.loads(r["color_vector"] or "[]")
        except (ValueError, TypeError):
            vec = []
        main = str(r["dominant_hex"]).upper()
        palette = [main]
        for c in vec[1:4]:
            hx = str(c.get("hex") or "").upper()
            if hx and hx not in palette:
                palette.append(hx)
        items.append({
            "key": r["key"],
            "name_cn": r["name_cn"],
            "name_en": r["name_en"] or "",
            "region": r["region"],
            "region_label": _REGION_CN.get(r["region"], r["region"]),
            "category": r["category"],
            "category_label": _CATEGORY_CN.get(r["category"], r["category"]),
            "brand_hex": main,
            "palette": palette,
            "origin": r["origin"],
        })
    items.sort(key=lambda x: (x["region"] != "cn", x["category_label"], x["name_cn"]))
    return {"brands": items, "count": len(items)}


def _brand_keys() -> set[str]:
    return {r["key"] for r in db.get_profiles()}


def _wardrobe_items() -> dict:
    """衣橱清单：每个品牌 = 一张已经换好装的人像。"""
    from . import wardrobe  # 延迟导入

    items = []
    for m in wardrobe.list_items():
        items.append({
            "id": m["id"],
            "name_cn": m.get("name_cn") or m["id"],
            "hex": m.get("hex"),
            "palette": m.get("palette") or [],
            "size": m.get("size"),
            "thumb_url": f"/api/thumb/{m['id']}",
        })
    return {"items": items, "count": len(items)}


def _wardrobe_compose(im: Image.Image, gid: str, engine: str = "auto") -> dict:
    """输入人像 + 指定基准衣服 → 换装图。"""
    from . import garment, wardrobe

    item = wardrobe.get(gid)
    if item is None:
        raise ValueError(f"衣橱里没有 {gid}")
    query = portrait.analyze(im, engine=engine)
    query["mode"] = "photo"
    info = compose.compose(im, query, wardrobe.as_brand(item),
                           style="garment", garment_id=gid,
                           with_download=True, save=True)
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = "BeverageColorDB/0.2"

    def log_message(self, fmt, *args) -> None:  # 静默，避免刷屏
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            # 默认首页：衣橱换装（基准人像是唯一的库）。旧的品牌匹配页仍在 /legacy。
            page = config.WEB_DIR / "wardrobe.html"
            if not page.is_file():
                page = INDEX_FILE
            if not page.is_file():
                self._send(404, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif path in ("/legacy", "/legacy.html"):
            if not INDEX_FILE.is_file():
                self._send(404, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/stats":
            try:
                self._json(200, _stats())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
        elif path == "/api/brands":
            try:
                self._json(200, _brands())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
        elif path == "/api/garments":
            try:
                from . import garment  # 延迟导入

                self._json(200, {"items": garment.list_garments()})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
        elif path == "/api/wardrobe":
            try:
                self._json(200, _wardrobe_items())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
        elif path.startswith("/api/thumb/"):
            from urllib.parse import unquote

            from . import wardrobe  # 延迟导入

            gid = unquote(path[len("/api/thumb/"):]).strip("/")
            body = wardrobe.thumb_bytes(gid) if gid else None
            if body is None:
                self._send(404, b"thumb not found", "text/plain; charset=utf-8")
                return
            ctype = "image/jpeg" if body[:3] == b"\xff\xd8\xff" else "image/webp"
            self._send(200, body, ctype)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/api/match", "/api/compose",
                        "/api/wd/match", "/api/wd/compose"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD * 2:
            self._json(413, {"error": "请求体为空或过大"})
            return
        try:
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise ValueError("请求体必须是 JSON 对象")
            top = min(20, max(1, int(req.get("top", 8))))
            # 手动多色查询：不上传照片，直接联合匹配（没有衣服，不生成合成图）
            if path == "/api/match" and req.get("colors"):
                payload = matcher.match_colors(req["colors"], top,
                                               weights=req.get("weights"))
                self._json(200, {**payload,
                                 "report": matcher.build_report(payload)})
                return

            # 前端「选择饮料」可指定任意品牌（不限于 Top-N）；两个接口都支持。
            chosen = req.get("key") or None
            if chosen and chosen not in _brand_keys():
                raise ValueError(f"品牌清单里没有 {chosen}")
            style = req.get("style") or config.COMPOSE_STYLE
            if style not in ("wrap", "print", "garment"):
                raise ValueError("合成风格只能是 wrap（包装贴图）/ garment（基准衣服迁移）/ print（平涂印花）")
            garment_id = req.get("garment_id") or None
            if style == "garment" and not garment_id:
                from . import garment  # 延迟导入

                items = garment.list_garments()
                if not items:
                    raise ValueError("基准衣服资产库为空，请先运行 scripts/build_garments.py")
                garment_id = items[0]["id"]
            engine = req.get("engine") or "auto"
            if engine not in ("auto", "model", "tone"):
                raise ValueError("分割引擎只能是 auto / model / tone")

            im = _decode_image(req.get("image", ""))

            # 衣橱换装：输入人像 → 衣服色板 → 匹配基准人像 → 把基准衣服穿上去
            if path.startswith("/api/wd/"):
                from . import wardrobe  # 延迟导入

                if path == "/api/wd/match":
                    query = portrait.analyze(im, engine=engine)
                    query["mode"] = "photo"
                    self._json(200, {
                        "query": query,
                        "matches": wardrobe.match(query, top=top),
                        "photo": matcher._b64(im),
                        "debug_image": matcher._b64(
                            portrait.debug_image(im, query)),
                    })
                    return
                gid = str(req.get("garment") or "").strip()
                if not gid:
                    raise ValueError("请指定要换的基准衣服（garment）")
                info = _wardrobe_compose(im, gid, engine)
                self._json(200, {"composite": _compose_public(info),
                                 "garment": gid})
                return

            payload = matcher.match_photo(
                im, box=req.get("box") or None, top=top,
                exclude_skin=bool(req.get("exclude_skin", True)),
                exclude_bg=bool(req.get("exclude_bg", True)),
                engine=engine,
            )
            # 照片模式默认生成自动第一名的合成图；带 key 时改用指定饮料。
            composite, compose_error = None, None
            try:
                composite = compose.compose_from_payload(
                    im, payload, brand_key=chosen, with_download=True,
                    style=style, garment_id=garment_id,
                )
            except Exception as exc:  # noqa: BLE001 - 合成失败不应拖垮匹配结果
                compose_error = f"{type(exc).__name__}: {exc}"
            report_info = matcher.build_report(payload, source=im, composite=composite)
            public = _compose_public(composite)

            if path == "/api/compose":
                if public is None:
                    self._json(400, {"error": compose_error or "合成图生成失败"})
                    return
                self._json(200, {"composite": public, "report": report_info,
                                 "selected_key": chosen})
                return

            result = {
                **payload,
                "photo": matcher._b64(im),
                "debug_image": matcher._b64(portrait.debug_image(im, payload["query"])),
                "report": report_info,
                "composite": public,
                "selected_key": chosen,
            }
            if compose_error:
                result["compose_error"] = compose_error
            self._json(200, result)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    db.init_db()
    return ThreadingHTTPServer((host, port), Handler)


def _lan_ips() -> list[str]:
    """取默认路由网卡的 IPv4 —— 手机连同一 Wi-Fi 时要用的是这个地址。"""
    import socket
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，只让系统选出默认出口网卡
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        try:
            ips = [v for v in socket.gethostbyname_ex(socket.gethostname())[2]
                   if not v.startswith("127.")]
        except OSError:
            pass
    return ips


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    srv = create_server(host, port)
    print(f"饮料颜色匹配已启动： http://{host}:{port}/  （Ctrl+C 退出）", flush=True)
    if host in ("0.0.0.0", "", "::"):
        for ip in _lan_ips():
            print(f"  手机（同一 Wi-Fi）打开： http://{ip}:{port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
