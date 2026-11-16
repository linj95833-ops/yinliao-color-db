"""图源注册表。"""
from __future__ import annotations

from .base import Candidate, RateLimiter, Source
from .commons import CommonsSource
from .local_dir import LocalDirSource
from .manual import ManualUrlsSource
from .official_site import OfficialSiteSource
from .openfoodfacts import OffSource

REGISTRY: dict[str, type] = {
    "off": OffSource,
    "official": OfficialSiteSource,
    "commons": CommonsSource,
    "local": LocalDirSource,
    "manual": ManualUrlsSource,
}
DEFAULT_ORDER = ("off", "official", "commons", "local", "manual")


def build_sources(names: list[str] | None = None) -> list[Source]:
    chosen = names or list(DEFAULT_ORDER)
    unknown = [n for n in chosen if n not in REGISTRY]
    if unknown:
        raise ValueError(f"未知图源: {unknown}，可选: {list(REGISTRY)}")
    return [REGISTRY[n]() for n in chosen]


__all__ = ["Candidate", "RateLimiter", "Source", "build_sources", "REGISTRY", "DEFAULT_ORDER"]
