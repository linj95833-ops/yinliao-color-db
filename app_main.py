"""饮料换装 · Windows 单文件 exe 入口（PyInstaller）。

与源码版的区别只有一件事：PyInstaller onefile 会把资源解压到临时目录
（sys._MEIPASS），程序退出就删除，数据库和生成的结果图不能写在那儿。
所以首次运行把内置资源**只补不覆盖**地释放到 exe 旁边的
「饮料换装_数据」目录，再把 config 里所有路径常量指向它；之后所有业务代码
（web / matcher / compose / wrap / db）都按原样工作，零改动。

命令行参数（可给快捷方式加）：
    --port 9000        指定端口（被占用时自动往上找空闲端口）
    --lan              监听 0.0.0.0，供同一 Wi-Fi 的手机访问
    --no-browser       不自动打开浏览器
    --data-dir PATH    自定义数据目录
    --check            只自检数据库并退出

源码模式（python app_main.py）也能跑，直接用项目根目录，方便调试。
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

APP = "饮料换装"
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
RESOURCE_DIRS = ("config", "data", "web", "examples", "models")
RESOURCE_FILES = ("README.md", "ATTRIBUTION.md")

EXE_DOC = """饮料换装 · exe 版使用说明
============================================================
1. 双击 exe，会先解压内置资源（含 52MB 语义模型 + 26MB 深度模型，
   首次约 10-40 秒），随后自动打开浏览器。这个黑色窗口就是服务本身，
   最小化即可，用完关掉窗口就停止服务。
2. 窗口里会打印访问地址，默认 http://127.0.0.1:8765/ ；
   若 8765 被占用会自动改用 8766、8767……以窗口显示的地址为准。
3. exe 同目录会自动生成「饮料换装_数据」文件夹，里面是：
     data/catalog.db      数据库（55 品牌 / 实测多色档案）
     data/images/         55 张包装图，可直接替换；换图后需重新运行 scan
     data/images_hd/      55 张离线超分高清素材（只注入高频细节，字形不变）
     data/wardrobe/       36 件基准衣服资产（已换装人像切出的条带 + 蒙版 + 轮廓）
     config/              品牌清单与 55 条贴图调参
     models/              segformer_clothes.onnx（人体解析，52MB）
                          depth_anything_v2_small.onnx（深度视差，26MB）
     report/              生成的合成图、匹配结果页、手机访问二维码页
   请保留这个文件夹，删掉会退回初始状态（exe 会重新释放一份）。
4. 手机上打开：用快捷方式加参数 --lan 启动，或命令行执行
     DrinkWrap.exe --lan
   手机与电脑连同一 Wi-Fi，打开窗口里打印的局域网地址；Windows 首次会弹
   防火墙提示，请点「允许访问」。也可打开 report/phone_access.html 扫码。
5. 衣服识别默认用内置的 SegFormer 人体解析模型（本地 CPU 推理，不联网）。
   网页「识别参数 → 衣服分割引擎」可切换 自动 / 语义模型 / 色调启发式；
   插画、纯色示意图或远景会自动降级到色调算法，结果里会写明原因。
   包装花纹由同一张标签连续铺满整件衣服，并按衣服轮廓逐行收放，
   不再有「模糊底 + 胸口清晰矩形」的拼接线；原球衣号码、字母与描边
   会被完全覆盖，白色包装字里不会再混入原字样淡影；轮廓做了亚像素
   抗锯齿，不再有一格一格的阶梯。
   渲染层还用深度模型预测 2D 形变坐标场，让花纹随胸廓弯曲（AI 只算
   坐标、不画像素，品牌字样仍逐像素取自原包装，绝不会被改写）；
   55 张包装图另做了离线超分，笔画更清晰。删掉 data/images_hd/ 目录
   即可回到原始素材，模型不可用时也会自动降级、不会崩。
   语义模型的 18 类里没有「袖子」，长袖外套会被整件判成上衣，因此分割
   末尾会按人像逐行轮廓向内收窄，让图案只覆盖躯干、不延伸到两只袖子；
   收窄只影响覆盖区域，贴图取景仍用收窄前的衣服框，所以领口不会出现
   罐口银白边被拉进来形成的白斑。想回到「连袖子一起换装」，把 config.py
   的 CLOTH_SLEEVE_TRIM 设为 0。
6. 换装用「基准衣橱」：首页就是衣橱网格（36 件已换装的人像），上传一张
   人像后按衣服颜色自动排最像的几件、第一名自动选中，点「开始换装」即可。
   衣服是从基准人像上精确切出来的条带（真实褶皱、透视、袖型与领口形状都在
   图里），整件按**等比**变换「穿」到你照片的衣服范围内再覆盖 —— 纵横同一个
   比例，所以衣服上的字是正的，不会被拉扁或抖成波浪。运行时不需要任何模型。
   资产在 data/wardrobe/，删掉某件或损坏会自动回退到包装贴图。
   建库时会自动裁掉压在衣服上的广告横幅条（红牛那张底部有一条
   「红牛维生素功能饮料」蓝带，早先版本会把它印到目标人像胯部）。
   衣橱卡片上的缩略图是整张基准人像，为了避免分发时带出他人肖像，交付物里
   出现的脸一律打了马赛克（库里的缩略图、report 里的证据图与演示图都打；
   唯一不打码的是你自己上传的照片，换装链路不经过打码模块）。
   想重建/新增资产：.venv/Scripts/python.exe scripts/import_wardrobe.py
   （--only 品牌 只重建指定那一件）。
7. 换端口 / 不自动开浏览器 / 自检：
     DrinkWrap.exe --port 9000 --no-browser
     DrinkWrap.exe --check
8. 所有照片只在本机处理，不上传公网；模型推理也完全在本地。
"""


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_probe"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        return True
    except OSError:
        return False


def data_root(arg: str | None) -> Path:
    if arg:
        p = Path(arg).expanduser().resolve()
        if not _writable(p):
            raise SystemExit(f"数据目录不可写：{p}")
        return p
    if not FROZEN:
        return Path(__file__).resolve().parent
    cand = Path(sys.executable).resolve().parent / (APP + "_数据")
    if _writable(cand):
        return cand
    fb = Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "DrinkWrapData"
    if _writable(fb):
        print(f"提示：exe 所在目录不可写，数据改存到 {fb}")
        return fb
    raise SystemExit("找不到可写目录来保存数据库和结果图")


def _copy_missing(src: Path, dst: Path) -> int:
    """只补缺失文件，绝不覆盖使用者替换过的图片、数据库或调参。"""
    n = 0
    for p in src.rglob("*"):
        out = dst / p.relative_to(src)
        if p.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        elif p.is_file() and not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
            n += 1
    return n


def _refresh_unmasked_thumbs(root: Path) -> int:
    """旧版本留下的缩略图没打码，升级时单独补一次。

    `_copy_missing` 只补缺失文件（怕覆盖使用者换过的图），于是「老数据目录 +
    新 exe」会把上一版没打码的 thumb.jpg 一直留着。这里只针对一种情况动手：
    包里的 meta.json 带 privacy.masked，而本地那份没有 —— 说明这是打码功能
    之前建的资产，把 meta 和 thumb 换掉即可，条带/蒙版/色板一概不碰。
    """
    if not FROZEN:
        return 0
    n = 0
    src_root = BUNDLE / "data" / "wardrobe"
    if not src_root.is_dir():
        return 0
    for src_meta in src_root.glob("*/meta.json"):
        dst_meta = root / src_meta.relative_to(BUNDLE)
        if not dst_meta.is_file():
            continue                      # 缺文件的情况由 _copy_missing 处理
        try:
            new = json.loads(src_meta.read_text(encoding="utf-8"))
            old = json.loads(dst_meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not new.get("privacy", {}).get("masked"):
            continue
        if old.get("privacy", {}).get("masked"):
            continue
        thumb = src_meta.parent / "thumb.jpg"
        if thumb.is_file():
            shutil.copy2(thumb, dst_meta.parent / "thumb.jpg")
            shutil.copy2(src_meta, dst_meta)
            n += 1
    return n


def prepare(root: Path) -> int:
    if not FROZEN:
        return 0
    n = 0
    for d in RESOURCE_DIRS:
        src = BUNDLE / d
        if src.is_dir():
            n += _copy_missing(src, root / d)
    n += _refresh_unmasked_thumbs(root)
    docs = BUNDLE / "docs"
    for f in RESOURCE_FILES:
        src = docs / f
        if src.is_file() and not (root / f).exists():
            shutil.copy2(src, root / f)
            n += 1
    (root / "report").mkdir(parents=True, exist_ok=True)
    doc = root / "使用说明_EXE.txt"
    if not doc.exists():
        doc.write_text(EXE_DOC, encoding="utf-8")
    return n


def redirect_config(root: Path) -> None:
    """在导入任何业务模块之前，把 config 里所有 Path 常量整体搬迁到可写目录。

    web.INDEX_FILE / wrap._OVERRIDE_FILE 等模块级常量都在 import 时基于
    config.ROOT 计算，所以这一步必须发生在 from beverage_color_db import web 之前。
    """
    from beverage_color_db import config as cfg

    orig = cfg.ROOT
    if orig == root:
        return
    for name in dir(cfg):
        val = getattr(cfg, name)
        if isinstance(val, Path) and (val == orig or orig in val.parents):
            setattr(cfg, name, root / val.relative_to(orig))


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，只让系统选出默认出口网卡
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def pick_port(host: str, preferred: int) -> int:
    for p in range(preferred, preferred + 30):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit(f"{preferred}~{preferred + 29} 端口都被占用，请用 --port 指定其它端口")


def make_phone_page(root: Path, port: int) -> None:
    ip = lan_ip()
    url = f"http://{ip}:{port}/"
    u = html.escape(url, quote=True)
    img = ""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=10, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        im = qr.make_image(fill_color=(28, 32, 38), back_color="white").convert("RGB")
        out = root / "report"
        out.mkdir(parents=True, exist_ok=True)
        im.save(out / "phone_qr.png")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        img = ('<img style="width:250px;max-width:100%;display:block;margin:0 auto 16px" '
               'src="data:image/png;base64,'
               + base64.b64encode(buf.getvalue()).decode() + '" alt="二维码">')
    except Exception as exc:  # noqa: BLE001 - 二维码是可选增强
        print(f"（二维码生成跳过：{exc}；直接用下面的地址即可）")
    page = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>手机打开 · 饮料换装</title>'
            f'<style>body{{margin:0;background:#f5f7fa;color:#1c2026;'
            f'font:16px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}'
            f'.w{{max-width:620px;margin:0 auto;padding:32px 20px}}'
            f'.c{{background:#fff;border:1px solid #e3e6eb;border-radius:12px;'
            f'padding:24px;text-align:center}}'
            f'.a{{display:inline-block;background:#eef3fa;color:#12459b;'
            f'font:600 20px/1.4 Consolas,monospace;padding:10px 16px;border-radius:8px;'
            f'word-break:break-all}}</style></head><body><div class="w">'
            f'<h1>在手机上打开「饮料换装」</h1>'
            f'<div class="c">{img}<div class="a">{u}</div>'
            f'<p><a href="{u}">直接点这个链接打开</a></p></div>'
            f'<p>手机与电脑需连同一个 Wi-Fi；换网络后重新用 --lan 启动一次即可。</p>'
            f'</div></body></html>')
    (root / "report/phone_access.html").write_text(page, encoding="utf-8")


def db_counts(root: Path) -> dict:
    import sqlite3

    con = sqlite3.connect(str(root / "data/catalog.db"))
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("brands", "images", "brand_color_profile")}
    finally:
        con.close()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="饮料换装 · 本地网页服务（exe 版）")
    ap.add_argument("--lan", action="store_true", help="允许同一 Wi-Fi 的手机访问")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--data-dir", help="自定义数据目录")
    ap.add_argument("--check", action="store_true", help="自检后退出")
    args = ap.parse_args()

    print("=" * 60)
    print(f"  {APP}  ·  本地启动中，首次运行需解压资源，请稍候")
    print("=" * 60)
    root = data_root(args.data_dir)
    n = prepare(root)
    redirect_config(root)

    from beverage_color_db import db, web

    db.init_db()
    counts = db_counts(root)
    print(f"数据目录 : {root}")
    print(f"数据库   : 品牌 {counts['brands']} / 图片 {counts['images']} "
          f"/ 实测档案 {counts['brand_color_profile']}")
    if n:
        print(f"首次运行：已释放 {n} 个资源文件（之后不会再覆盖你的数据）")
    if args.check:
        try:
            from beverage_color_db import human

            s = human.status()
            print(f"语义模型 : {s['model_file']} ({s['model_mb']} MB) "
                  f"存在={s['model_exists']} 推理库={s['runtime_ready']} "
                  f"可加载={human.available()}")
        except Exception as exc:  # noqa: BLE001 - 可选能力，自检不应中断
            print(f"语义模型 : 不可用（{type(exc).__name__}: {exc}），将使用色调启发式")
        print("自检完成。")
        return 0

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    port = pick_port(host, args.port)
    if port != args.port:
        print(f"端口 {args.port} 已被占用，自动改用 {port}")
    if args.lan:
        make_phone_page(root, port)
    url = f"http://127.0.0.1:{port}/"
    srv = web.create_server(host, port)

    print("-" * 60)
    print(f"  已就绪！浏览器打开： {url}")
    if args.lan:
        print(f"  手机（同一 Wi-Fi）： http://{lan_ip()}:{port}/")
        print(f"  二维码页： {root / 'report' / 'phone_access.html'}")
        print("  若手机连不上，请在 Windows 防火墙弹窗里点「允许访问」")
    print(f"  生成的图片保存在： {root / 'report'}")
    print("  用完直接关闭本窗口即可停止服务（或按 Ctrl+C）")
    print("-" * 60, flush=True)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 双击运行时别让黑窗口一闪而过
        import traceback

        traceback.print_exc()
        # 无控制台（被其它程序调用 / 输出重定向）时不要卡在等待回车
        if FROZEN and getattr(sys.stdin, "isatty", lambda: False)():
            try:
                input(f"\n启动失败：{exc}\n按回车键退出...")
            except (EOFError, OSError):
                pass
        raise SystemExit(1)
