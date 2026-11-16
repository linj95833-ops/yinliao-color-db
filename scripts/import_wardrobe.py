"""把「基准人像库」一次性导成衣橱资产。

来源目录结构（每个品牌一个文件夹，里面放 1~N 张已经换好装的人像）：

    <你的目录>/饮料/
        康师傅冰红茶/xxx.jpg
        王老吉/yyy.png
        ...

产物：

    data/wardrobe/<id>/strip.webp    每行都被衣服像素填满的条带图
    data/wardrobe/<id>/profile.npz   逐行轮廓（center/half/有效行）
    data/wardrobe/<id>/meta.json     中文名、衣服色板、超分信息
    data/wardrobe/<id>/thumb.jpg     网页列表缩略图

用法：
    .venv/Scripts/python.exe scripts/import_wardrobe.py
    .venv/Scripts/python.exe scripts/import_wardrobe.py --src "D:/别的目录" --keep-old
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beverage_color_db import config, garment, wardrobe  # noqa: E402

DEFAULT_SRC = Path.home() / "Desktop" / "饮料"   # 可用 --src 覆盖


def pick_image(folder: Path) -> Path | None:
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in wardrobe.IMAGE_EXTS]
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    from PIL import Image

    best, best_area = None, -1
    for p in files:
        try:
            with Image.open(p) as im:
                area = im.width * im.height
        except Exception:  # noqa: BLE001 - 坏图直接跳过
            continue
        if area > best_area:
            best, best_area = p, area
    return best or files[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--keep-old", action="store_true",
                    help="保留旧 data/garments 与旧基准资产")
    ap.add_argument("--no-upscale", action="store_true")
    ap.add_argument("--only", default="",
                    help="只重建这些品牌文件夹（逗号分隔），其余资产原样保留")
    args = ap.parse_args()

    src_root = Path(args.src)
    if not src_root.is_dir():
        raise SystemExit(f"找不到来源目录：{src_root}")

    out = config.WARDROBE_DIR
    out.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if not args.keep_old and not only:
        for old in out.iterdir():
            if old.is_dir():
                shutil.rmtree(old)
        # 旧库（包装图 + 8 张基准衣服）整体退役，避免和新库混在一起被前端读到
        legacy = config.GARMENT_DIR
        if legacy.is_dir() and legacy.resolve() != out.resolve():
            shutil.rmtree(legacy, ignore_errors=True)

    rows, failed = [], []
    t0 = time.time()
    for folder in sorted(src_root.iterdir()):
        if not folder.is_dir():
            continue
        name = folder.name.strip()
        if only and name not in only:
            continue
        src = pick_image(folder)
        if src is None:
            failed.append((name, "空文件夹"))
            print(f"  [SKIP] {name}: 没有图片")
            continue
        try:
            meta = wardrobe.build_item(src, name, do_upscale=not args.no_upscale)
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            continue
        rows.append(meta)
        up = meta["upscale"]
        up_s = (f"{up['out_size'][0]}x{up['out_size'][1]}" if up.get("upscaled")
                else "no-sr")
        print(f"  [OK] {meta['id']:<20} {meta['name_cn']:<8} "
              f"源 {meta['source_size'][0]}x{meta['source_size'][1]} → {up_s} "
              f"→ 条带 {meta['size'][0]}x{meta['size'][1]} "
              f"cov={meta['mask_coverage']:.2f} 主色 {meta['hex']}")

    # 清单以磁盘为准：--only 只重建几件时，index.json 仍要覆盖整个衣橱
    on_disk = wardrobe.list_items()
    summary = {
        "count": len(on_disk),
        "failed": [{"name": n, "reason": r} for n, r in failed],
        "source": str(src_root),
        "elapsed_s": round(time.time() - t0, 1),
        "method": "语义概率场定范围 + 主色聚类扩张 + 4x 高频超分 + 逐行条带",
        "note": "运行时只加载 strip/profile/meta，不需要模型与原图",
        "items": [{k: m.get(k) for k in ("id", "name_cn", "size", "source_size",
                                         "mask_coverage", "hex", "palette")}
                  for m in on_disk],
    }
    (out / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n衣橱 {out}：{len(on_disk)} 件（本次重建 {len(rows)} 件，"
          f"失败 {len(failed)}）；garment.status() = {garment.status()['count']} 件可读")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
