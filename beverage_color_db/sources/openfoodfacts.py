"""Open Food Facts 图源：按品牌检索标准「正面包装图」。

2026-11-10 复测结论（旧版注释里的 503 已变化）：
  /api/v2/search?brands_tags=<slug>  恢复可用，coca-cola 1417 / sprite 205 / red-bull 425 条，
                                     且几乎每条都带 image_front_url（标准 400px 正面包装图）
  /cgi/search.pl、/brand/*.json       仍 503
  /api/v2/product/{barcode}.json      稳定 200，作为显式条码补充
限制：search 限流极严，page_size>5 或高频请求立刻 503，镜像域名同样不稳。
对策：单并发 + 小分页 + 域名 failover + 指数退避 + 检索结果本地缓存（7 天）。

图片授权 CC BY-SA 3.0，数据库授权 ODbL，详见 README / ATTRIBUTION.md。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from .. import config, db
from .base import Candidate


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _brand_tags(brand: dict[str, Any]) -> list[str]:
    tags = [str(t).strip() for t in brand.get("off_brand_tags", []) if str(t).strip()]
    if not tags:
        derived = _slug(brand.get("name_en") or brand["key"])
        if derived:
            tags = [derived]
    return list(dict.fromkeys(tags))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _looks_like_brand(product: dict[str, Any], brand: dict[str, Any], tag: str) -> bool:
    """brands_tags 可能带出同集团的其他品牌，做一次宽松的文本复核。"""
    text = _norm(f"{product.get('brands') or ''} {product.get('product_name') or ''}")
    aliases = [_norm(a) for a in brand.get("aliases", []) if a and a.isascii()]
    if any(a and a in text for a in aliases):
        return True
    return _norm(tag) in text


class OffSource:
    name = "off"
    # 类级全局串行：所有品牌共用，避免 asyncio.gather 并发打爆限流
    _gate = asyncio.Semaphore(1)

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()

        # 1) 显式条码：端点最稳定，优先级最高
        for code in brand.get("off_codes", []):
            code = str(code).strip()
            if not code:
                continue
            url = config.OFF_PRODUCT_API.format(code=code)
            try:
                data = await limiter.run(
                    url,
                    lambda u=url: self._get_json(session, u, {
                        "fields": "code,product_name,brands,image_front_url,image_front_small_url",
                    }),
                )
            except Exception as exc:  # noqa: BLE001
                db.log_fetch(brand["key"], self.name, url, False, f"api: {exc}")
                continue
            for c in self._to_candidates(brand, [(data or {}).get("product") or {}], seen):
                out.append(c)

        # 2) 按品牌标签检索：主力图源
        if config.OFF_SKIP_CN_SEARCH and brand.get("region") == "cn":
            # 实测国内品牌在 OFF 几乎零收录，省下限流额度给海外品牌
            return out
        for tag in _brand_tags(brand):
            for page in range(1, config.OFF_SEARCH_PAGES + 1):
                products = await self._search(tag, page, brand, session)
                if products is None:
                    break  # 该页所有域名都失败，不再继续翻页
                for c in self._to_candidates(brand, products, seen):
                    out.append(c)
                if len(out) >= config.MAX_IMAGES_PER_BRAND * 3:
                    break
            if len(out) >= config.MAX_IMAGES_PER_BRAND * 3:
                break
        return out

    def _to_candidates(self, brand: dict[str, Any],
                       products: list[dict[str, Any]], seen: set[str]) -> list[Candidate]:
        out: list[Candidate] = []
        for p in products:
            if not isinstance(p, dict):
                continue
            img = p.get("image_front_url") or p.get("image_front_small_url")
            code = str(p.get("code") or "").strip()
            if not img or img in seen:
                continue
            seen.add(img)
            out.append(Candidate(
                brand_key=brand["key"], url=img, source=self.name,
                source_page=(f"https://world.openfoodfacts.org/product/{code}" if code
                             else "https://world.openfoodfacts.org/"),
                license=config.OFF_LICENSE,
                attribution=config.OFF_ATTRIBUTION,
                title=p.get("product_name") or code or "OFF product",
                width=None, height=None,
            ))
        return out

    async def _search(self, tag: str, page: int, brand: dict[str, Any],
                      session) -> list[dict[str, Any]] | None:
        """单页检索：优先读缓存，未命中才联网；全部域名失败返回 None。"""
        cache = self._cache_path(tag, page)
        cached = self._read_cache(cache)
        if cached is not None:
            return cached

        params = {
            "brands_tags": tag,
            "fields": "code,product_name,brands,image_front_url,image_front_small_url",
            "page_size": config.OFF_SEARCH_PAGE_SIZE,
            "page": page,
        }
        async with self._gate:
            await asyncio.sleep(config.OFF_SEARCH_DELAY)
            for attempt in range(config.OFF_SEARCH_RETRIES):
                host = config.OFF_SEARCH_HOSTS[attempt % len(config.OFF_SEARCH_HOSTS)]
                url = config.OFF_SEARCH_API.format(host=host)
                try:
                    data = await self._get_json(session, url, params)
                    products = [p for p in (data.get("products") or [])
                                if isinstance(p, dict) and _looks_like_brand(p, brand, tag)]
                    self._write_cache(cache, products)
                    return products
                except Exception as exc:  # noqa: BLE001 - 503 / SSL / 超时统一退避
                    if attempt + 1 < config.OFF_SEARCH_RETRIES:
                        await asyncio.sleep(min(8.0, 1.5 * (2 ** attempt)))
                    else:
                        db.log_fetch(brand["key"], self.name, url, False,
                                     f"off search failed after {config.OFF_SEARCH_RETRIES}: {exc}")
        return None

    @staticmethod
    def _cache_path(tag: str, page: int) -> Any:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]", "_", tag.lower())
        return config.CACHE_DIR / f"off_{safe}_p{page}.json"

    @staticmethod
    def _read_cache(path) -> list[dict[str, Any]] | None:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(obj["fetched_at"])
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > config.OFF_CACHE_TTL_DAYS * 86400:
                return None
            return obj["products"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _write_cache(path, products: list[dict[str, Any]]) -> None:
        try:
            path.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "products": products,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    async def _get_json(session, url: str, params: dict[str, Any]) -> dict[str, Any]:
        async with session.get(url, params=params,
                               headers={"Accept": "application/json"}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} @ {url}")
            return await resp.json(content_type=None)
