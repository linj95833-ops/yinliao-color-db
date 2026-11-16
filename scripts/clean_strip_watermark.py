"""把已导出的衣橱条带再过一遍「账号水印清除」。

为什么需要单独一个脚本：strip.webp 是从基准人像切出来的，原始素材往往导完
就删了，`import_wardrobe.py --only` 没有源图跑不动。这个脚本直接改资产里的
strip.webp，其余文件（mask / profile）不受影响 —— 水印清除只改颜色，不改轮廓。

幂等：清过一遍的条带不会再次命中（检测要求「贴底 + 稀疏白字 + 横贯大半衣宽」）。

用法：
    .venv/Scripts/python.exe scripts/clean_strip_watermark.py --dry-run
    .venv/Scripts/python.exe scripts/clean_strip_watermark.py --only sprite
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beverage_color_db import config, wardrobe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理这些资产 id（逗号分隔）")
    ap.add_argument("--root", default="", help="衣橱目录，默认 data/wardrobe")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--thumb", action="store_true",
                    help="只补缩略图（strip 已清过水印、但当时还没处理 thumb）")
    args = ap.parse_args()

    root = Path(args.root) if args.root else config.WARDROBE_DIR
    want = {s.strip() for s in args.only.split(",") if s.strip()}
    if args.thumb and not want:
        raise SystemExit("--thumb 需要配合 --only 指定资产 id")

    n_hit = n_all = 0
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if want and d.name not in want:
            continue
        sp, mp = d / "strip.webp", d / "mask.png"
        if not (sp.is_file() and mp.is_file()):
            continue
        n_all += 1
        meta_path = d / "meta.json"
        meta = (json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.is_file() else {})

        if args.thumb:
            if meta.get("watermark", {}).get("thumb_shaved"):
                print(f"  ·  {d.name:22s} 缩略图已处理")
                continue
            tp = d / "thumb.jpg"
            if not tp.is_file():
                print(f"  ·  {d.name:22s} 无缩略图")
                continue
            print(f"  ✓  {d.name:22s} 缩略图裁掉底部水印条")
            if args.dry_run:
                continue
            wardrobe._shave_bottom(Image.open(tp).convert("RGB")).save(
                tp, "JPEG", quality=86)
            wm = dict(meta.get("watermark") or {})
            wm["thumb_shaved"] = True
            meta["watermark"] = wm
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            n_hit += 1
            continue

        rgb = np.asarray(Image.open(sp).convert("RGB"), dtype=float)
        mask = np.asarray(Image.open(mp).convert("L"), dtype=float) > 110
        out, info = wardrobe.drop_watermark(rgb, mask)
        if not info["dropped"]:
            print(f"  ·  {d.name:22s} 无水印")
            continue
        n_hit += 1
        box = info["box"]
        print(f"  ✓  {d.name:22s} 抹除 x{box[0]}..{box[2]} y{box[1]}..{box[3]}"
              f"  {info['px']} px")
        if args.dry_run:
            continue
        Image.fromarray(np.rint(np.clip(out, 0, 255)).astype(np.uint8)).save(
            sp, "WEBP", quality=95, method=4)
        # 缩略图是整张人像缩的，水印同样在上面（网页衣橱卡片显示的就是它）。
        tp = d / "thumb.jpg"
        if tp.is_file() and not meta.get("watermark", {}).get("thumb_shaved"):
            wardrobe._shave_bottom(Image.open(tp).convert("RGB")).save(
                tp, "JPEG", quality=86)
            info["thumb_shaved"] = True
        if meta_path.is_file():
            meta["watermark"] = info
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    print(f"\n扫描 {n_all} 件，命中 {n_hit} 件"
          + ("（dry-run，未写盘）" if args.dry_run else "，已更新"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
