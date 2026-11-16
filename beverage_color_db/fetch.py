"""下载器：多源候选 -> 限速并发下载 -> 质量校验 -> pHash 去重 -> 元数据落库。

支持断点续传（已入库的 source_url / 已存在的文件直接跳过），失败逐条记 fetch_log。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import numpy as np
from PIL import Image, UnidentifiedImageError

from . import catalog, color, config, db, quality
from .catalog import normalize_title
from .sources import Candidate, RateLimiter, build_sources

PRIORITY = {"off": 0, "official": 1, "commons": 2, "local": 3, "manual": 4}
EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _extension(cand: Candidate, ctype: str) -> str:
    if ctype in EXT_BY_MIME:
        return EXT_BY_MIME[ctype]
    suffix = Path(urlparse(cand.url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"


def _background_score(im: Image.Image) -> float:
    """四角像素颜色标准差 -> 纯色背景接近 1，杂乱场景照接近 0。

    必须用四角而不是四边：产品正面图常常占满画面宽度，四边中部就是彩色包装，
    用整条边会把「产品占满画面」误判成「背景杂乱」。
    """
    a = np.asarray(im.resize((64, 64), Image.Resampling.BILINEAR), dtype=float)
    # k=3 经标定：白底产品正面图 ~0.96、杂乱场景照 0；k>=6 会把占满画面的
    # 产品边缘算进角块，把标准产品图误判为脏图。
    k = 3
    corners = np.concatenate([
        a[:k, :k].reshape(-1, 3), a[:k, -k:].reshape(-1, 3),
        a[-k:, :k].reshape(-1, 3), a[-k:, -k:].reshape(-1, 3),
    ])
    std = float(np.std(corners, axis=0).mean())
    return round(max(0.0, min(1.0, 1.0 - std / 55.0)), 3)


def _open_checked(path: Path, data: bytes, source: str) -> tuple[int, int, str, float, bool, str, dict, float]:
    """返回 (w, h, phash, bg_score, 质检通过, 原因, 指标, 信任权重)。"""
    if len(data) < config.MIN_IMAGE_BYTES:
        raise ValueError(f"too small: {len(data)}B")
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        w, h = im.size
        if not config.size_ok(w, h):
            raise ValueError(f"bad size: {w}x{h}")
        rgb = im.convert("RGB")
        ok, reason, metrics, trust = quality.evaluate(rgb, source)
        return w, h, color.phash(rgb), _background_score(rgb), ok, reason, metrics, trust


class Fetcher:
    def __init__(self, brands: list[dict[str, Any]], source_names: list[str] | None = None,
                 limit: int = config.MAX_IMAGES_PER_BRAND) -> None:
        self.brands = brands
        self.sources = build_sources(source_names)
        self.limit = limit
        self.candidate_limit = limit * config.CANDIDATE_FACTOR
        self.limiter = RateLimiter()
        self.sem = asyncio.Semaphore(config.CONCURRENCY)
        self.brand_ids = db.upsert_brands(brands)
        self.stats: dict[str, int] = {
            "candidates": 0, "downloaded": 0, "skipped_existing": 0,
            "rejected": 0, "failed": 0,
        }
        self.by_source: dict[str, int] = {}

    async def _get(self, url: str, referer: str | None = None) -> tuple[bytes, str]:
        headers = {"Referer": referer} if referer else None
        async with self.session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype and ctype not in config.ALLOWED_MIME:
                raise ValueError(f"unexpected content-type: {ctype}")
            return await resp.read(), ctype

    async def _obtain(self, cand: Candidate) -> tuple[bytes, str]:
        if cand.local_path is not None:
            return cand.local_path.read_bytes(), ""
        # 官网 CDN 常有防盗链，带上来源页 Referer
        ref = cand.source_page if cand.source == "official" else None
        return await self.limiter.run(cand.url, lambda: self._get(cand.url, ref))

    async def _handle(self, brand: dict[str, Any], cand: Candidate, state: dict) -> None:
        if state["count"] >= self.candidate_limit:
            return
        key = brand["key"]
        digest = hashlib.sha1(cand.url.encode("utf-8")).hexdigest()[:16]
        folder = config.IMAGE_DIR / catalog.resolve_brand_dir(brand, self.brands)
        folder.mkdir(parents=True, exist_ok=True)

        # 断点：同 URL 已入库则跳过
        if cand.source != "local":
            with db.connect() as con:
                if con.execute("SELECT 1 FROM images WHERE source_url = ?", (cand.url,)).fetchone():
                    self.stats["skipped_existing"] += 1
                    return

        try:
            async with self.sem:
                data, ctype = await self._obtain(cand)
            ext = _extension(cand, ctype)
            target = folder / f"{digest}{ext}"
            tmp = folder / f"{digest}{ext}.part"
            if target.exists() and tmp.exists():
                tmp.unlink(missing_ok=True)
            tmp.write_bytes(data)
            try:
                (w, h, hash_, bg_score, q_ok, q_reason, q_metrics, trust
                 ) = await asyncio.to_thread(_open_checked, tmp, data, cand.source)
            except (UnidentifiedImageError, ValueError, OSError) as exc:
                tmp.unlink(missing_ok=True)
                self.stats["rejected"] += 1
                db.log_fetch(key, cand.source, cand.url, False, f"quality: {exc}")
                return
            # 宁缺毋滥：不合格直接拒收，绝不入库
            if not q_ok:
                tmp.unlink(missing_ok=True)
                self.stats["rejected"] += 1
                db.log_fetch(key, cand.source, cand.url, False, f"quality gate: {q_reason}")
                return

            async with state["lock"]:
                if state["count"] >= self.candidate_limit:
                    tmp.unlink(missing_ok=True)
                    return
                if any(color.hamming(hash_, old) <= config.PHASH_HAMMING_MAX
                       for old in state["hashes"]):
                    tmp.unlink(missing_ok=True)
                    self.stats["rejected"] += 1
                    db.log_fetch(key, cand.source, cand.url, False, "duplicate phash")
                    return
                tmp.replace(target)
                try:
                    # 入库与落盘必须在同一个临界区内完成：否则并发写 SQLite 失败时
                    # 会留下无元数据、无授权信息的孤儿图片，且名额已被占用。
                    db.insert_image({
                        "brand_id": self.brand_ids[key],
                        "path": str(target.relative_to(config.ROOT)).replace("\\", "/"),
                        "source": cand.source,
                        "source_url": cand.url,
                        "source_page": cand.source_page,
                        "license": cand.license,
                        "attribution": cand.attribution,
                        "width": w, "height": h, "bytes": len(data),
                        "phash": hash_, "bg_score": bg_score,
                        "trust": trust, "quality_json": json.dumps(q_metrics, ensure_ascii=False),
                        "status": "ok",
                    })
                    db.log_fetch(key, cand.source, cand.url, True, "ok")
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                state["hashes"].append(hash_)
                state["count"] += 1

            self.stats["downloaded"] += 1
            self.by_source[cand.source] = self.by_source.get(cand.source, 0) + 1
        except Exception as exc:  # noqa: BLE001 - 单张失败不影响整批
            self.stats["failed"] += 1
            db.log_fetch(key, cand.source, cand.url, False, f"{type(exc).__name__}: {exc}")

    async def _run_brand(self, brand: dict[str, Any]) -> None:
        results = await asyncio.gather(
            *[s.candidates(brand, self.session, self.limiter) for s in self.sources],
            return_exceptions=True,
        )
        seen: set[str] = set()
        cands: list[Candidate] = []
        for src, res in zip(self.sources, results):
            if isinstance(res, BaseException):
                db.log_fetch(brand["key"], src.name, None, False, f"source: {res}")
                continue
            for c in res:
                if c.url not in seen:
                    seen.add(c.url)
                    cands.append(c)
        # 图源优先级优先，其次让标题含「瓶/罐/包装」的单品图排在场景照前面
        cands.sort(key=lambda c: (
            PRIORITY.get(c.source, 9),
            0 if any(w in normalize_title(c.title or "") for w in
                     ("bottle", "can", "carton", "pack", "packaging", "drink", "soda", "product", "box", "ml"))
            else 1,
        ))
        self.stats["candidates"] += len(cands)
        if not cands:
            return
        state = {
            "count": 0,
            "hashes": [h for _, h in db.get_brand_hashes(self.brand_ids[brand["key"]])],
            "lock": asyncio.Lock(),
        }
        # 按图源优先级分批：高优先级（OFF 标准正面图）先填满名额。
        # 若一次性并发全部候选，「先下载完」的低质量图会抢走名额，优先级形同虚设。
        groups: dict[int, list[Candidate]] = {}
        for c in cands[:max(self.candidate_limit * 2, 16)]:
            groups.setdefault(PRIORITY.get(c.source, 9), []).append(c)
        for prio in sorted(groups):
            if state["count"] >= self.candidate_limit:
                break
            await asyncio.gather(*[self._handle(brand, c, state) for c in groups[prio]])

    async def run(self) -> dict[str, Any]:
        config.ensure_dirs()
        timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT, connect=10)
        headers = {"User-Agent": config.USER_AGENT, "Accept": "image/*, application/json;q=0.9, */*;q=0.5"}
        connector = aiohttp.TCPConnector(limit=config.CONCURRENCY, limit_per_host=config.CONCURRENCY)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout,
                                         connector=connector, trust_env=True) as session:
            self.session = session
            # 品牌间串行、品牌内并发：对公共服务更友好
            for brand in self.brands:
                await self._run_brand(brand)
        return {**self.stats, "by_source": self.by_source,
                **select_best(self.limit)}


def select_best(limit: int) -> dict[str, int]:
    """每品牌按包装图质量分择优保留 limit 张，其余删文件与 DB 行。

    下载阶段按候选倍数多收，是为了避免「先下载完」的随手拍/宣传图占掉名额；
    纯统计指标无法 100% 判断语义，但按主色集中度/饱和度排序能稳定把
    干净单品正面图排在前面。
    """
    removed = 0
    with db.connect() as con:
        brands = list(con.execute("SELECT id, key FROM brands"))
        for b in brands:
            rows = list(con.execute(
                "SELECT id, path, quality_json, trust FROM images "
                "WHERE brand_id = ? AND status = 'ok'", (b["id"],)
            ))
            if len(rows) <= limit:
                continue
            scored = []
            for r in rows:
                try:
                    metrics = json.loads(r["quality_json"] or "{}")
                except (ValueError, TypeError):
                    metrics = {}
                scored.append((quality.priority_score(metrics, r["trust"] or 1.0), r))
            scored.sort(key=lambda x: -x[0])
            for _, r in scored[limit:]:
                (config.ROOT / r["path"]).unlink(missing_ok=True)
                # color_features 通过外键 ON DELETE CASCADE 一并删除
                con.execute("DELETE FROM images WHERE id = ?", (r["id"],))
                removed += 1
        con.commit()
    return {"pruned_by_quality": removed}


def fetch(brands: list[dict[str, Any]], source_names: list[str] | None = None,
          limit: int = config.MAX_IMAGES_PER_BRAND) -> dict[str, Any]:
    return asyncio.run(Fetcher(brands, source_names, limit).run())
