"""Wikimedia Commons 图源：CC / 公有领域授权的产品包装实物图。

实测：commons.wikimedia.org 与 upload.wikimedia.org 均可达，API 返回 extmetadata
含 LicenseShortName / Artist，可逐张落库署名。全文检索噪声较大，因此做标题白名单
+ 噪声词黑名单 + 尺寸/比例过滤。
"""
from __future__ import annotations

import html
import re
from typing import Any

from .. import config, db
from ..catalog import normalize_title
from .base import Candidate

DENY_TITLE_WORDS = (
    "poster", "protest", "mural", "graffiti", "truck", "billboard", "advertisement",
    "advert", "tattoo", "cake", "costume", "drawing", "cartoon", "flag", "map ",
    "chart", "diagram", "screenshot", "logo", "fridge", "refrigerator", "shelf",
    "supermarket", "vending", "statue", "painting", "stamp", "coin", "ticket",
    "magazine", "newspaper", "halloween", "christmas", "birthday", "death", "war ",
    "museum", "t-shirt", "tshirt", "keychain", "sticker", "badge", "toy", "model ",
    "miniature",     "sign ", "store ", "shop ", "crate", "pallet", "collage", "panorama",
)

# 多品牌 / 多产品合集图不适合作为单一品牌的颜色样本
MIX_TITLE_WORDS = (
    " and ", " vs ", " versus ", "assortment", "various ", "mixed ",
    "collection", "selection", "range of", "different ", "family of",
)


def _clean(text: str | None, limit: int = 180) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _license_ok(name: str | None) -> bool:
    n = (name or "").lower()
    if not n:
        return False  # 授权不明一律不要
    return any(k in n for k in ("cc by", "cc-by", "cc0", "public domain", "pd-", "attribution"))


def _title_ok(title: str, brand: dict[str, Any]) -> bool:
    t = normalize_title(title)
    padded = f" {t} "
    if any(w in padded for w in DENY_TITLE_WORDS):
        return False
    if any(w in padded for w in MIX_TITLE_WORDS):
        return False
    return any(normalize_title(a) in t for a in brand.get("aliases", []) if normalize_title(a))


class CommonsSource:
    name = "commons"

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        out: list[Candidate] = []
        for query in brand.get("queries", [])[:3]:
            params = {
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
                "gsrlimit": 20, "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 800,
            }
            try:
                data = await limiter.run(
                    config.COMMONS_API,
                    lambda: self._get_json(session, params),
                )
            except Exception as exc:  # noqa: BLE001 - 单源失败不能中断全库
                db.log_fetch(brand["key"], self.name, config.COMMONS_API, False, f"api: {exc}")
                continue

            pages = (data or {}).get("query", {}).get("pages", {}) or {}
            for page in pages.values():
                info = (page.get("imageinfo") or [{}])[0]
                title = page.get("title", "")
                url = info.get("thumburl") or info.get("url")
                if not url or not _title_ok(title, brand):
                    continue
                meta = info.get("extmetadata") or {}
                lic = meta.get("LicenseShortName", {}).get("value")
                if not _license_ok(lic):
                    continue
                if info.get("mime") and info["mime"] not in config.ALLOWED_MIME:
                    continue
                w, h = info.get("width") or 0, info.get("height") or 0
                if (w and h) and not config.size_ok(w, h):
                    continue
                out.append(Candidate(
                    brand_key=brand["key"], url=url, source=self.name,
                    source_page=info.get("descriptionurl"),
                    license=lic,
                    attribution=_clean(meta.get("Artist", {}).get("value")) or "Wikimedia Commons contributor",
                    title=title, width=w or None, height=h or None,
                ))
        # 同一 URL 只保留一次
        return list({c.url: c for c in out}.values())

    @staticmethod
    async def _get_json(session, params: dict[str, Any]) -> dict[str, Any]:
        async with session.get(config.COMMONS_API, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)
