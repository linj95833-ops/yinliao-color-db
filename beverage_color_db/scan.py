"""手动填图「采信模式」。

用户把产品包装图放进 data/images/<key>_<中文名>/（或 data/inbox/ 下的同名目录）后，
一条命令完成：无条件采信入库 -> 提色 -> 重建品牌主色档案。
不跑质检闸门、不择优、不改变已采集图片的授权元数据（用户已声明图片正确）。

数据库里的品牌 key 不变，目录名只是给人看的；旧版纯 key 目录会自动迁移。
"""
from __future__ import annotations

from typing import Any

from PIL import Image, UnidentifiedImageError

from . import catalog, color, config, db, ingest

_DIR_GUIDE = """饮料品牌图片 · 手动填图说明
================================

1. 每个子目录名是「品牌 key_中文名」，例如 data/images/coca_cola_可口可乐/。
   完整对照见 config/brands.yaml；目录名里的 key 才是数据库标识，中文只是便于辨认。
2. 把该品牌的产品包装实物图直接放进对应目录，支持 .jpg/.jpeg/.png/.webp/.bmp。
   推荐「单品正面图、无拼贴、无大幅背景」；本项目假定你放的每张图都是正确的。
3. 放好后执行：
   .venv/Scripts/python.exe -m beverage_color_db.cli scan
   （若图片带明显背景/场景，加 --remove-bg；替换同名文件后加 --force 才会重算）
4. scan 会重新提色并把该品牌档案从「人工占位色」升级为「图片实测色」。
   删除图片后再跑一次 scan，会自动清掉失效记录并回退为占位档案。
5. 目录由你自己管理：手动删掉的品牌目录不会被 scan / init 自动重建；
   目录中文后缀可以自由修改，只要保留前面的 key（例如 powerade_水动乐）。
   需要恢复某个品牌时，手工建一个 key_中文名 目录再放图即可。

旧版纯英文目录（如 coca_cola/）会在 init / scan 时自动改名为 key_中文名，
同时同步更新数据库里的图片路径，不会丢图。

图片由使用者自行提供并确认授权，本项目不对其授权背书；请勿把这些图片提交到 GitHub。
"""


def migrate_dirs(brands: list[dict]) -> dict[str, int]:
    """把旧的 <key>/ 目录迁移到 <key>_<中文名>/，并同步更新 images.path。"""
    renamed, paths = 0, 0
    with db.connect() as con:
        rows = list(con.execute("SELECT id, path FROM images"))
        for b in brands:
            key = str(b["key"])
            old = config.IMAGE_DIR / key
            new = config.IMAGE_DIR / catalog.brand_dir_name(b)
            if old == new or not old.is_dir():
                continue
            if new.is_dir():
                # 两边都存在：把旧目录里的文件搬过去，不覆盖同名文件
                for p in old.iterdir():
                    dest = new / p.name
                    if p.is_file() and not dest.exists():
                        p.rename(dest)
                try:
                    old.rmdir()
                except OSError:
                    pass
            else:
                old.rename(new)
            renamed += 1
            old_prefix = f"data/images/{key}/"
            new_prefix = f"data/images/{new.name}/"
            for r in rows:
                if r["path"].startswith(old_prefix):
                    con.execute(
                        "UPDATE images SET path = ? WHERE id = ?",
                        (new_prefix + r["path"][len(old_prefix):], r["id"]),
                    )
                    paths += 1
        con.commit()
    return {"dirs_renamed": renamed, "db_paths_updated": paths}


def scaffold() -> dict[str, int]:
    """迁移旧目录 + 写放图说明，返回目录状态。

    目录归用户管理：只有在「一个品牌目录都没有」的全新安装时才自动建全套目录。
    用户手动删掉的目录不会被 scan / init 重新创建；改过中文后缀的目录也照常识别。
    """
    brands = catalog.load_brands()
    config.ensure_dirs()
    migrated = migrate_dirs(brands)
    active = [p for p in config.IMAGE_DIR.iterdir()
              if p.is_dir() and catalog.resolve_brand_key(p.name, brands)]
    created = 0
    if not active:
        for b in brands:
            (config.IMAGE_DIR / catalog.brand_dir_name(b)).mkdir(parents=True, exist_ok=True)
            created += 1
        active = [p for p in config.IMAGE_DIR.iterdir()
                  if p.is_dir() and catalog.resolve_brand_key(p.name, brands)]
    (config.IMAGE_DIR / "README.txt").write_text(_DIR_GUIDE, encoding="utf-8")
    return {"dirs_active": len(active), "dirs_created": created, **migrated}


def refresh_profiles() -> dict[str, int]:
    """图片实测档案优先，其余品牌补人工占位档案，保证 60 个品牌都可匹配。"""
    return {
        "image_profiles": ingest.rebuild_profiles(),
        "stale_profiles_removed": db.purge_stale_profiles(),
        "seed_profiles_written": db.seed_profiles(catalog.load_seed_colors()),
    }


def _brand_map() -> dict[str, int]:
    with db.connect() as con:
        return {r["key"]: int(r["id"]) for r in con.execute("SELECT id, key FROM brands")}


def scan(only: str | None = None, remove_bg: bool = False, force: bool = False) -> dict[str, Any]:
    db.init_db()
    brands = catalog.load_brands()
    removed = db.purge_brands_not_in(b["key"] for b in brands)
    dirs = scaffold()  # 幂等：迁移旧目录、尊重用户删/改的目录、刷新放图说明
    brand_ids = _brand_map()
    if not brand_ids:
        raise RuntimeError("品牌表为空，请先执行 init")

    wanted = {x.strip() for x in only.split(",") if x.strip()} if only else None
    if wanted:
        unknown = wanted - set(brand_ids)
        if unknown:
            raise ValueError(f"未知品牌 key: {sorted(unknown)}")

    stats: dict[str, Any] = {
        **dirs, "brands_removed": removed,
        "files_seen": 0, "added": 0, "recolored": 0, "skipped": 0, "failed": 0,
        "stale_records_removed": 0, "auto_bg_removed": 0, "unknown_dirs": [],
    }
    stats["stale_records_removed"] = db.prune_missing_images()

    with db.connect() as con:
        known = {
            r["path"]: {k: r[k] for k in r.keys()}
            for r in con.execute("SELECT * FROM images")
        }

    for root in (config.IMAGE_DIR, config.INBOX_DIR):
        if not root.exists():
            continue
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            key = catalog.resolve_brand_key(sub.name, brands)
            if key is None:
                if sub.name not in stats["unknown_dirs"]:
                    stats["unknown_dirs"].append(sub.name)
                continue
            if wanted and key not in wanted:
                continue
            for path in sorted(
                p for p in sub.iterdir()
                if p.is_file() and p.suffix.lower() in config.SCAN_IMAGE_EXTS
            ):
                stats["files_seen"] += 1
                rel = str(path.relative_to(config.ROOT)).replace("\\", "/")
                existing = known.get(rel)
                if existing and existing["status"] == "ok" and not force:
                    stats["skipped"] += 1
                    continue
                try:
                    with Image.open(path) as im:
                        im.load()
                        rgb = color.to_rgb(im)
                        w, h = rgb.size
                        if w <= 0 or h <= 0:
                            raise ValueError("empty image")
                        digest = color.phash(rgb)
                        # 已有记录沿用原图源的去背景策略，不篡改其授权元数据；
                        # 新增的用户图自动识别白底/影棚背景，--remove-bg 可强制开启。
                        if existing:
                            rb = (existing["source"] == "commons"
                                  or color.suggest_remove_background(rgb))
                        else:
                            rb = remove_bg or color.suggest_remove_background(rgb)
                        stats["auto_bg_removed"] += int(rb)
                        colors = color.extract_colors(rgb, remove_background=rb)
                    if not colors:
                        raise ValueError("no dominant color extracted")
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    stats["failed"] += 1
                    db.log_fetch(key, "scan", rel, False, f"{type(exc).__name__}: {exc}")
                    continue

                if existing:
                    image_id = int(existing["id"])
                    stats["recolored"] += 1
                else:
                    image_id = db.insert_image({
                        "brand_id": brand_ids[key], "path": rel,
                        "source": config.USER_IMAGE_SOURCE,
                        "source_url": None, "source_page": None,
                        "license": config.USER_IMAGE_LICENSE,
                        "attribution": config.USER_IMAGE_ATTRIBUTION,
                        "width": w, "height": h, "bytes": path.stat().st_size,
                        "phash": digest, "bg_score": 1.0, "trust": 1.0,
                        "quality_json": None, "status": "ok", "reject_reason": None,
                    })
                    stats["added"] += 1
                    known[rel] = {"id": image_id, "path": rel, "status": "ok",
                                  "source": config.USER_IMAGE_SOURCE}
                db.save_color_features(image_id, colors)

    stats.update(refresh_profiles())
    with db.connect() as con:
        stats["profiles_total"] = con.execute(
            "SELECT COUNT(*) FROM brand_color_profile").fetchone()[0]
        stats["profiles_from_images"] = con.execute(
            "SELECT COUNT(*) FROM brand_color_profile WHERE origin = 'images'").fetchone()[0]
        stats["profiles_seed_only"] = con.execute(
            "SELECT COUNT(*) FROM brand_color_profile WHERE origin = 'reference'").fetchone()[0]
    return stats
