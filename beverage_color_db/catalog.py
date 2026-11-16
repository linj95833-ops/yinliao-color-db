"""品牌清单 / 手工 URL 清单的加载与校验。"""
from __future__ import annotations

import re
from typing import Any

import yaml

from . import config

REQUIRED = ("key", "name_cn", "name_en", "region", "category", "queries", "aliases")
KEY_RE = re.compile(r"^[a-z0-9_]+$")


def _read_yaml(path):
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_brands(path=None) -> list[dict[str, Any]]:
    data = _read_yaml(path or config.BRANDS_FILE)
    brands = data.get("brands") or []
    if not isinstance(brands, list) or not brands:
        raise ValueError("brands.yaml 中没有品牌数据")

    seen: set[str] = set()
    for b in brands:
        for field in REQUIRED:
            if field not in b:
                raise ValueError(f"品牌 {b.get('key', '?')} 缺少字段 {field}")
        key = str(b["key"])
        if not KEY_RE.match(key):
            raise ValueError(f"品牌 key 必须是小写 ASCII/下划线: {key}")
        if key in seen:
            raise ValueError(f"品牌 key 重复: {key}")
        if b["region"] not in ("cn", "intl"):
            raise ValueError(f"品牌 {key} 的 region 必须是 cn 或 intl")
        seen.add(key)
        b["key"] = key
        b.setdefault("off_codes", [])
        b.setdefault("reference_hex", None)
    return brands


_DIR_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def brand_dir_name(brand: dict[str, Any]) -> str:
    """图片目录名：key_中文名。key 仍是数据库标识，目录名只是方便人工辨认。"""
    key = str(brand["key"])
    cn = _DIR_ILLEGAL.sub("", str(brand.get("name_cn") or "").strip())
    return f"{key}_{cn}" if cn else key


def resolve_brand_dir(brand: dict[str, Any], brands: list[dict[str, Any]]) -> str:
    """磁盘上该品牌实际使用的目录名。

    优先标准名 key_中文名；若用户把目录改成了别的中文后缀（例如 key_水动乐），
    就沿用用户起的名字，不再额外创建一个标准名目录造成重复。
    """
    standard = brand_dir_name(brand)
    root = config.IMAGE_DIR
    if (root / standard).is_dir() or not root.exists():
        return standard
    key = str(brand["key"])
    aliases = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name != standard
        and resolve_brand_key(p.name, brands) == key
    )
    return aliases[0] if aliases else standard


def resolve_brand_key(dirname: str, brands: list[dict[str, Any]]) -> str | None:
    """目录名 -> 品牌 key；兼容新名 key_中文名 与旧名 key，以及自定义中文后缀。"""
    keys = [str(b["key"]) for b in brands]
    if dirname in keys:
        return dirname
    for b in brands:
        if dirname == brand_dir_name(b):
            return str(b["key"])
    for key in sorted(keys, key=len, reverse=True):
        if dirname.startswith(key + "_"):
            return key
    return None


HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def load_seed_colors(path=None) -> dict[str, str]:
    """人工占位主色。无图品牌用它建种子档案；有实测图的品牌会被 scan 覆盖。"""
    p = path or config.SEED_COLORS_FILE
    if not p.exists():
        return {}
    colors = (_read_yaml(p) or {}).get("colors") or {}
    return {
        str(k): str(v).strip().upper()
        for k, v in colors.items()
        if HEX_RE.match(str(v).strip())
    }


def load_manual_urls(path=None) -> dict[str, Any]:
    p = path or config.MANUAL_URLS_FILE
    if not p.exists():
        return {"enabled": False, "respect_robots": True, "urls": {}}
    data = _read_yaml(p)
    data.setdefault("enabled", False)
    data.setdefault("respect_robots", True)
    data.setdefault("urls", {})
    return data


def normalize_title(text: str) -> str:
    """归一化文件标题：小写、连字符/下划线转空格、压缩空白。"""
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", text.lower())).strip()
