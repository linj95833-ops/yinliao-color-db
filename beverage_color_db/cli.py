"""命令行入口。

    python -m beverage_color_db.cli init            # 建库 + 品牌清单 + 图片目录 + 种子档案
    python -m beverage_color_db.cli scan            # 手动填图后：采信入库 + 提色 + 重建档案
    python -m beverage_color_db.cli fetch --brands coca_cola,sprite,red_bull --limit 3
    python -m beverage_color_db.cli fetch --source off,official,commons,local,manual
    python -m beverage_color_db.cli ingest
    python -m beverage_color_db.cli reindex-bg
    python -m beverage_color_db.cli reconcile [--prune]
    python -m beverage_color_db.cli report
    python -m beverage_color_db.cli match --color "#E60012"
    python -m beverage_color_db.cli match --photo path/to/portrait.jpg --compose [--brand pepsi]
    python -m beverage_color_db.cli serve --port 8765
    python -m beverage_color_db.cli all --brands coca_cola,pepsi,sprite --limit 3
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import catalog, compose, config, db, ingest, matcher, report, scan
from . import fetch as fetch_mod


def _out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _select_brands(only: str | None) -> list[dict[str, Any]]:
    brands = catalog.load_brands()
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        known = {b["key"] for b in brands}
        unknown = wanted - known
        if unknown:
            raise SystemExit(f"未知品牌 key: {sorted(unknown)}")
        brands = [b for b in brands if b["key"] in wanted]
    return brands


def cmd_init(args) -> None:
    db.init_db()
    brands = catalog.load_brands()
    db.upsert_brands(brands)
    removed = db.purge_brands_not_in(b["key"] for b in brands)
    dirs = scan.scaffold()
    profiles = scan.refresh_profiles()
    _out({"status": "ok", "brands": len(brands), "brands_removed": removed,
          "db": str(config.DB_PATH), "image_root": str(config.IMAGE_DIR),
          **dirs, **profiles})


def cmd_scan(args) -> None:
    _out({"status": "ok", **scan.scan(only=args.brands,
                                      remove_bg=args.remove_bg,
                                      force=args.force)})


def cmd_fetch(args) -> None:
    db.init_db()
    brands = _select_brands(args.brands)
    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    result = fetch_mod.fetch(brands, sources, args.limit)
    _out({"status": "ok", "brands": len(brands), **result})


def cmd_ingest(args) -> None:
    _out({"status": "ok", **ingest.ingest(force=args.force)})


def cmd_reindex_bg(args) -> None:
    """算法升级后就地重算 bg_score，无需重新下载图片。"""
    from PIL import Image

    from .fetch import _background_score

    rows = db.get_ok_images()
    updated = 0
    for r in rows:
        try:
            with Image.open(config.ROOT / r["path"]) as im:
                score = _background_score(im.convert("RGB"))
            with db.connect() as con:
                con.execute("UPDATE images SET bg_score = ? WHERE id = ?", (score, r["id"]))
                con.commit()
            updated += 1
        except Exception as exc:  # noqa: BLE001
            db.log_fetch("reindex", "bg_score", r["path"], False, f"{type(exc).__name__}: {exc}")
    _out({"status": "ok", "updated": updated, "total": len(rows)})


def cmd_reconcile(args) -> None:
    """核对磁盘图片与数据库记录。默认只报告；--prune 才删除孤儿文件。

    孤儿文件 = 已落盘但无 DB 记录（因而无 license/attribution）的图片，
    以及下载中断残留的 .part 临时文件。这类文件不可追溯授权，不应随项目发布。
    """
    root = config.IMAGE_DIR
    db.init_db()  # 库被删/首次运行时也能安全核对
    with db.connect() as con:
        known = {r["path"] for r in con.execute("SELECT path FROM images")}

    all_files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    # 目录里还有放图说明 README.txt，它不是图片，不应被当成无授权孤儿图。
    disk = [p for p in all_files
            if p.suffix.lower() in config.SCAN_IMAGE_EXTS or p.name.endswith(".part")]
    rel = {str(p.relative_to(config.ROOT)).replace("\\", "/"): p for p in disk}
    partials = [p for p in disk if p.name.endswith(".part")]
    orphans = [p for r, p in rel.items() if r not in known and not p.name.endswith(".part")]
    missing = sorted(known - set(rel))

    removed = 0
    if args.prune:
        for p in [*orphans, *partials]:
            p.unlink(missing_ok=True)
            removed += 1
    _out({
        "status": "ok",
        "image_dir": str(root),
        "db_records": len(known),
        "disk_image_files": len(disk),
        "non_image_files_ignored": len(all_files) - len(disk),
        "orphan_files": len(orphans),
        "partial_files": len(partials),
        "db_missing_files": len(missing),
        "orphan_paths": [str(p.relative_to(config.ROOT)).replace("\\", "/") for p in orphans],
        "db_missing_paths": missing,
        "pruned": removed,
        "note": "加 --prune 才会删除；孤儿文件无授权元数据，不建议保留" if not args.prune else "已删除",
    })


def cmd_report(args) -> None:
    r = report.build_report()
    _out({"status": "ok", "summary": r["summary"],
          "missing_brands": r["missing_brands"],
          "report": str(config.REPORT_DIR / "quality.md"),
          "attribution": str(config.ATTRIBUTION_FILE)})


def cmd_match(args) -> None:
    if args.photo:
        payload = matcher.match_photo(
            args.photo, box=args.box, top=args.top,
            exclude_skin=not args.keep_skin, exclude_bg=not args.keep_bg,
            engine=args.engine,
        )
        source = args.photo
    else:
        hexes = [x.strip() for x in args.color.split(",") if x.strip()]
        weights = ([float(x.strip()) for x in args.weights.split(",") if x.strip()]
                   if args.weights else None)
        if len(hexes) == 1 and not weights:
            payload = {"query": {"hex": hexes[0], "mode": "color",
                                 "dominant_hex": hexes[0]},
                       "matches": matcher.match_hex(hexes[0], args.top)}
        else:
            payload = matcher.match_colors(hexes, args.top, weights=weights)
        source = None
    composite = None
    if args.compose:
        if not args.photo:
            raise SystemExit("--compose 需要人像照片：请与 --photo 一起使用（纯颜色没有衣服可换）")
        gid = args.garment
        if args.style == "garment" and not gid:
            from . import wardrobe  # 延迟导入：只有换装档才需要衣橱

            top = wardrobe.match(payload["query"], top=1)
            if top:
                gid = top[0]["id"]
                print(f"[garment] 未指定 --garment，按色板自动选：{gid}")
        composite = compose.compose_from_payload(
            args.photo, payload, brand_key=args.brand, out=args.out,
            style=args.style, garment_id=gid
        )
    paths = {} if args.no_report else matcher.build_report(
        payload, source=source, composite=composite
    )
    q = payload["query"]
    result = {
        "status": "ok",
        "query": {k: q.get(k) for k in
                  ("dominant_hex", "hex", "confidence", "box", "palette",
                   "engine", "method", "semantic_note")
                  if q.get(k) is not None},
        "matches": payload["matches"],
        "report": paths,
    }
    if composite:
        result["composite"] = {
            k: v for k, v in composite.items() if k not in ("image", "preview", "download")
        }
        result["composite"]["path"] = str(composite["path"])
    _out(result)


def cmd_serve(args) -> None:
    from . import web

    host = "0.0.0.0" if getattr(args, "lan", False) and args.host == "127.0.0.1" else args.host
    web.serve(host, args.port)


def cmd_all(args) -> None:
    db.init_db()
    db.upsert_brands(catalog.load_brands())
    brands = _select_brands(args.brands)
    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    fetched = fetch_mod.fetch(brands, sources, args.limit)
    colored = ingest.ingest()
    r = report.build_report()
    _out({"status": "ok", "fetch": fetched, "ingest": colored,
          "summary": r["summary"], "missing_brands": r["missing_brands"]})


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    p = argparse.ArgumentParser(prog="beverage-color-db", description="饮料包装主色数据库（非商用）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化 SQLite、写入品牌清单、生成图片目录与种子档案").set_defaults(func=cmd_init)

    s = sub.add_parser("scan", help="采信模式：扫描手动填图并入库、提色、重建档案")
    s.add_argument("--brands", help="只处理指定 key，逗号分隔")
    s.add_argument("--remove-bg", action="store_true", help="图片带明显背景时启用背景剔除")
    s.add_argument("--force", action="store_true", help="同名文件被替换后强制重算")
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fetch", help="采集并下载产品包装图")
    f.add_argument("--brands", help="只处理指定 key，逗号分隔")
    f.add_argument("--source", help="图源子集，逗号分隔：off,official,commons,local,manual")
    f.add_argument("--limit", type=int, default=config.MAX_IMAGES_PER_BRAND, help="每品牌最多图片数")
    f.set_defaults(func=cmd_fetch)

    i = sub.add_parser("ingest", help="提取主色并重建品牌主色画像")
    i.add_argument("--force", action="store_true", help="重算全部图片")
    i.set_defaults(func=cmd_ingest)

    sub.add_parser("reindex-bg", help="就地重算背景纯净度（图源算法升级后用）").set_defaults(func=cmd_reindex_bg)

    rc = sub.add_parser("reconcile", help="核对磁盘图片与数据库，清理无授权的孤儿文件")
    rc.add_argument("--prune", action="store_true", help="实际删除（默认只报告）")
    rc.set_defaults(func=cmd_reconcile)

    sub.add_parser("report", help="生成质检报告与署名清单").set_defaults(func=cmd_report)

    m = sub.add_parser("match", help="按衣服颜色/人像照片匹配饮料品牌")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--color", help="一个或多个颜色 #RRGGBB，逗号分隔（多色联合匹配）")
    g.add_argument("--photo", help="人像照片路径（自动识别衣服颜色）")
    m.add_argument("--weights", help="多色权重，逗号分隔，与 --color 一一对应（默认等权）")
    m.add_argument("--box", help="取色提示区 x1,y1,x2,y2（0-1 归一化，默认 0.25,0.28,0.75,0.88；"
                                 "只用于找衣服颜色，衣服形状由色调分割识别）")
    m.add_argument("--top", type=int, default=8)
    m.add_argument("--keep-skin", action="store_true", help="不排除肤色区域")
    m.add_argument("--keep-bg", action="store_true", help="不排除背景色")
    m.add_argument("--no-report", action="store_true", help="不生成 HTML/JSON 结果页")
    m.add_argument("--compose", action="store_true",
                   help="生成衣服换色 + 品牌印花的合成图（需 --photo，默认 report/composite.png）")
    m.add_argument("--brand", help="合成图改用指定品牌 key（默认匹配第一名）")
    m.add_argument("--style", choices=("wrap", "print", "garment"),
                   default=config.COMPOSE_STYLE,
                   help="合成风格：wrap=把饮料包装穿上身（默认）/ print=平涂主色+程序印花 / "
                        "garment=把衣橱里那件基准衣服整件切下来变形覆盖（需 --garment）")
    m.add_argument("--garment", help="style=garment 时指定衣橱 id（如 wanglaoji），"
                                    "不传则自动选色板最接近的一件")
    m.add_argument("--engine", choices=("auto", "model", "tone"), default="auto",
                   help="衣服分割引擎：auto=语义模型优先并自动降级（默认）/ "
                        "model=强制模型 / tone=只用旧的色调启发式")
    m.add_argument("--out", help="合成图输出路径（默认 report/composite.png）")
    m.set_defaults(func=cmd_match)

    w = sub.add_parser("serve", help="启动本地上传界面（默认 127.0.0.1:8765，加 --lan 供手机访问）")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--lan", action="store_true",
                   help="监听 0.0.0.0 并打印局域网地址，让同一 Wi-Fi 的手机能打开")
    w.set_defaults(func=cmd_serve)

    a = sub.add_parser("all", help="init + fetch + ingest + report 全链路")
    a.add_argument("--brands", help="只处理指定 key，逗号分隔")
    a.add_argument("--source", help="图源子集，逗号分隔")
    a.add_argument("--limit", type=int, default=config.MAX_IMAGES_PER_BRAND)
    a.set_defaults(func=cmd_all)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
