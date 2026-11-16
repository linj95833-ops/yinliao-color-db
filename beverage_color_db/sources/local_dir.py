"""本地素材兜底：把 data/inbox/<key>_<中文名>/ 下用户自备的包装图纳入同一管线。

兼容旧版纯 key 目录；两种目录可同时存在。
"""
from __future__ import annotations

from typing import Any

from .. import catalog, config
from .base import Candidate

EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})


class LocalDirSource:
    name = "local"

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        names = (catalog.brand_dir_name(brand), str(brand["key"]))
        folders = [config.INBOX_DIR / n for n in dict.fromkeys(names)
                   if (config.INBOX_DIR / n).is_dir()]
        out = []
        for folder in folders:
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.suffix.lower() in EXTS:
                    out.append(Candidate(
                        brand_key=brand["key"], url=p.as_uri(), source=self.name,
                        license="USER_PROVIDED", attribution="user-provided asset",
                        local_path=p, title=p.name,
                    ))
        return out
