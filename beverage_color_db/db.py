"""SQLite 元数据层：品牌、图片、颜色特征、品牌主色画像、抓取日志。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS brands (
    id            INTEGER PRIMARY KEY,
    key           TEXT NOT NULL UNIQUE,
    name_cn       TEXT NOT NULL,
    name_en       TEXT,
    region        TEXT NOT NULL,
    category      TEXT NOT NULL,
    aliases       TEXT NOT NULL DEFAULT '[]',
    queries       TEXT NOT NULL DEFAULT '[]',
    off_codes     TEXT NOT NULL DEFAULT '[]',
    reference_hex TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY,
    brand_id      INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    path          TEXT NOT NULL UNIQUE,
    source        TEXT NOT NULL,
    source_url    TEXT,
    source_page   TEXT,
    license       TEXT,
    attribution   TEXT,
    width         INTEGER,
    height        INTEGER,
    bytes         INTEGER,
    phash         TEXT,
    bg_score      REAL NOT NULL DEFAULT 1.0,
    trust         REAL NOT NULL DEFAULT 1.0,
    quality_json  TEXT,
    status        TEXT NOT NULL DEFAULT 'ok',
    reject_reason TEXT,
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_images_brand ON images(brand_id, status);
CREATE INDEX IF NOT EXISTS idx_images_phash ON images(brand_id, phash);

CREATE TABLE IF NOT EXISTS color_features (
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    color_rank  INTEGER NOT NULL,
    hex         TEXT NOT NULL,
    rgb_r       INTEGER NOT NULL,
    rgb_g       INTEGER NOT NULL,
    rgb_b       INTEGER NOT NULL,
    lab_l       REAL NOT NULL,
    lab_a       REAL NOT NULL,
    lab_b       REAL NOT NULL,
    ratio       REAL NOT NULL,
    method      TEXT NOT NULL DEFAULT 'kmeans-lab-v1',
    PRIMARY KEY (image_id, color_rank)
);

CREATE TABLE IF NOT EXISTS brand_color_profile (
    brand_id      INTEGER PRIMARY KEY REFERENCES brands(id) ON DELETE CASCADE,
    dominant_hex  TEXT NOT NULL,
    lab_l         REAL NOT NULL,
    lab_a         REAL NOT NULL,
    lab_b         REAL NOT NULL,
    color_vector  TEXT NOT NULL DEFAULT '[]',
    confidence    REAL NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    origin        TEXT NOT NULL DEFAULT 'images',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY,
    brand_key  TEXT NOT NULL,
    source     TEXT NOT NULL,
    url        TEXT,
    ok         INTEGER NOT NULL,
    message    TEXT,
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        # 兼容早期已建好的库：补列而不重建
        cols = {r["name"] for r in con.execute("PRAGMA table_info(images)")}
        if "bg_score" not in cols:
            con.execute("ALTER TABLE images ADD COLUMN bg_score REAL NOT NULL DEFAULT 1.0")
        p_cols = {r["name"] for r in con.execute("PRAGMA table_info(brand_color_profile)")}
        if "origin" not in p_cols:
            con.execute(
                "ALTER TABLE brand_color_profile ADD COLUMN origin TEXT NOT NULL DEFAULT 'images'"
            )
        con.commit()


def upsert_brands(brands: Iterable[dict[str, Any]]) -> dict[str, int]:
    """写入/更新品牌清单，返回 key -> id 映射。不删除已入库但清单中移除的品牌。"""
    rows = []
    for b in brands:
        ref = b.get("reference_hex")
        ref_value = (json.dumps(list(ref), ensure_ascii=False)
                     if isinstance(ref, (list, tuple)) else ref)
        rows.append((
            b["key"], b["name_cn"], b.get("name_en"), b["region"], b["category"],
            json.dumps(b.get("aliases", []), ensure_ascii=False),
            json.dumps(b.get("queries", []), ensure_ascii=False),
            json.dumps(b.get("off_codes", []), ensure_ascii=False),
            ref_value,
        ))
    with connect() as con:
        con.executemany(
            """INSERT INTO brands
               (key, name_cn, name_en, region, category, aliases, queries, off_codes, reference_hex)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   name_cn=excluded.name_cn, name_en=excluded.name_en,
                   region=excluded.region, category=excluded.category,
                   aliases=excluded.aliases, queries=excluded.queries,
                   off_codes=excluded.off_codes, reference_hex=excluded.reference_hex""",
            rows,
        )
        con.commit()
        return {r["key"]: r["id"] for r in con.execute("SELECT id, key FROM brands")}


def purge_brands_not_in(keys: Iterable[str]) -> list[str]:
    """同步品牌清单：删除已从 brands.yaml 移除的品牌，返回被删 key。

    外键 ON DELETE CASCADE 会一并清掉该品牌的图片记录、颜色特征与主色档案；
    磁盘上的图片目录不动（由使用者自行处理）。fetch_log 用字符串 key，单独清。
    """
    keep = set(keys)
    with connect() as con:
        stale = [r["key"] for r in con.execute("SELECT key FROM brands")
                 if r["key"] not in keep]
        for key in stale:
            con.execute("DELETE FROM brands WHERE key = ?", (key,))
            con.execute("DELETE FROM fetch_log WHERE brand_key = ?", (key,))
        con.commit()
    return stale


def log_fetch(brand_key: str, source: str, url: str | None, ok: bool, message: str = "") -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO fetch_log (brand_key, source, url, ok, message, at) VALUES (?,?,?,?,?,?)",
            (brand_key, source, url, int(ok), message[:500], utcnow()),
        )
        con.commit()


def insert_image(payload: dict[str, Any]) -> int:
    """幂等写入图片元数据；返回图片 id。path 唯一，重复调用不会新增。"""
    cols = ("brand_id", "path", "source", "source_url", "source_page", "license",
            "attribution", "width", "height", "bytes", "phash", "bg_score",
            "trust", "quality_json", "status", "reject_reason")
    values = tuple(payload.get(c, 1.0) if c in ("bg_score", "trust") else payload.get(c)
                   for c in cols)
    with connect() as con:
        con.execute(
            f"INSERT OR IGNORE INTO images ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            values,
        )
        con.commit()
        row = con.execute("SELECT id FROM images WHERE path = ?", (payload["path"],)).fetchone()
        return int(row["id"])


def get_ok_images(brand_id: int | None = None, only_missing_color: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM images WHERE status = 'ok'"
    args: list[Any] = []
    if brand_id is not None:
        sql += " AND brand_id = ?"
        args.append(brand_id)
    if only_missing_color:
        sql += " AND id NOT IN (SELECT DISTINCT image_id FROM color_features)"
    sql += " ORDER BY id"
    with connect() as con:
        return list(con.execute(sql, args))


def get_brand_hashes(brand_id: int) -> list[tuple[int, str]]:
    with connect() as con:
        return [
            (r["id"], r["phash"])
            for r in con.execute(
                "SELECT id, phash FROM images WHERE brand_id = ? AND status = 'ok' AND phash IS NOT NULL",
                (brand_id,),
            )
        ]


def save_color_features(image_id: int, colors: list[dict[str, Any]]) -> None:
    with connect() as con:
        con.execute("DELETE FROM color_features WHERE image_id = ?", (image_id,))
        con.executemany(
            """INSERT INTO color_features
               (image_id, color_rank, hex, rgb_r, rgb_g, rgb_b, lab_l, lab_a, lab_b, ratio, method)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(
                image_id, i, c["hex"], c["rgb"][0], c["rgb"][1], c["rgb"][2],
                c["lab"][0], c["lab"][1], c["lab"][2], c["ratio"], c.get("method", "kmeans-lab-v1"),
            ) for i, c in enumerate(colors)],
        )
        con.commit()


def save_profile(brand_id: int, dominant: dict[str, Any], vector: list[dict[str, Any]],
                 confidence: float, sample_count: int, origin: str = "images") -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO brand_color_profile
               (brand_id, dominant_hex, lab_l, lab_a, lab_b, color_vector,
                confidence, sample_count, origin, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(brand_id) DO UPDATE SET
                   dominant_hex=excluded.dominant_hex, lab_l=excluded.lab_l,
                   lab_a=excluded.lab_a, lab_b=excluded.lab_b,
                   color_vector=excluded.color_vector, confidence=excluded.confidence,
                   sample_count=excluded.sample_count, origin=excluded.origin,
                   updated_at=excluded.updated_at""",
            (brand_id, dominant["hex"], dominant["lab"][0], dominant["lab"][1], dominant["lab"][2],
             json.dumps(vector, ensure_ascii=False), round(confidence, 4), sample_count,
             origin, utcnow()),
        )
        con.commit()


def get_profiles() -> list[sqlite3.Row]:
    """品牌主色档案 + 品牌信息，供匹配器使用。"""
    with connect() as con:
        return list(con.execute(
            """SELECT b.key, b.name_cn, b.name_en, b.region, b.category,
                      p.dominant_hex, p.lab_l, p.lab_a, p.lab_b, p.color_vector,
                      p.confidence, p.sample_count, p.origin
               FROM brand_color_profile p JOIN brands b ON b.id = p.brand_id
               ORDER BY b.key"""
        ))


def seed_profiles(seeds: dict[str, str]) -> int:
    """无实测图的品牌写入人工占位档案；已有图片实测档案的品牌不动。"""
    from . import color

    written = 0
    with connect() as con:
        for brand in con.execute("SELECT id, key FROM brands"):
            row = con.execute(
                "SELECT origin, dominant_hex FROM brand_color_profile WHERE brand_id = ?",
                (brand["id"],),
            ).fetchone()
            hex_value = seeds.get(brand["key"])
            if row and row["origin"] == "images":
                continue
            if not hex_value:
                if row and row["origin"] == "reference":
                    con.execute("DELETE FROM brand_color_profile WHERE brand_id = ?", (brand["id"],))
                continue
            if row and row["origin"] == "reference" and row["dominant_hex"] == hex_value:
                continue
            lab = color.hex_to_lab(hex_value)
            vector = json.dumps([{
                "hex": hex_value,
                "lab": [round(float(v), 2) for v in lab],
                "ratio": 1.0,
            }], ensure_ascii=False)
            con.execute(
                """INSERT INTO brand_color_profile
                   (brand_id, dominant_hex, lab_l, lab_a, lab_b, color_vector,
                    confidence, sample_count, origin, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(brand_id) DO UPDATE SET
                       dominant_hex=excluded.dominant_hex, lab_l=excluded.lab_l,
                       lab_a=excluded.lab_a, lab_b=excluded.lab_b,
                       color_vector=excluded.color_vector, confidence=excluded.confidence,
                       sample_count=excluded.sample_count, origin=excluded.origin,
                       updated_at=excluded.updated_at""",
                (brand["id"], hex_value, lab[0], lab[1], lab[2], vector,
                 config.SEED_PROFILE_CONFIDENCE, 0, "reference", utcnow()),
            )
            written += 1
        con.commit()
    return written


def purge_stale_profiles() -> int:
    """清掉「曾经由图片算出、但图片已被删除」的档案；随后 seed 会补占位档案。"""
    with connect() as con:
        cur = con.execute(
            """DELETE FROM brand_color_profile
               WHERE origin = 'images' AND brand_id NOT IN (
                   SELECT DISTINCT i.brand_id FROM images i
                   JOIN color_features cf ON cf.image_id = i.id
                   WHERE i.status = 'ok'
               )"""
        )
        con.commit()
        return cur.rowcount


def prune_missing_images() -> int:
    """删除指向已不存在文件的图片记录（级联删颜色特征），返回删除的记录数。"""
    with connect() as con:
        stale = [
            r for r in con.execute("SELECT id, brand_id, path FROM images")
            if not (config.ROOT / r["path"]).is_file()
        ]
        for r in stale:
            con.execute("DELETE FROM images WHERE id = ?", (r["id"],))
        con.commit()
    return len(stale)
