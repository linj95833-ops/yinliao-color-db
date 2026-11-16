"""给衣橱里已有的 thumb.jpg 打码（人脸马赛克）。

为什么单独一个脚本：
    缩略图是 build_item() 生成时顺手打的码，但存量资产是打码功能之前导入的，
    原始素材导完就删了 —— 不能指望「重新导一遍」，只能原地改 thumb.jpg。
    条带（strip.webp）只有衣服，不含人脸，不用动。

用法：
    python scripts/mask_thumb_faces.py                  # 全部
    python scripts/mask_thumb_faces.py --only sprite    # 指定资产
    python scripts/mask_thumb_faces.py --dry-run        # 只看会动哪些
    python scripts/mask_thumb_faces.py --force          # 重打（默认跳过已打码）

幂等：meta.json 里有 privacy.masked 的直接跳过；就算 meta 丢了，打过码的脸
模型也认不出来，第二次跑会报 no_face 而不是再糊一层。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from beverage_color_db import config, privacy  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理这些资产 id（逗号分隔）")
    ap.add_argument("--root", default="", help="衣橱目录，默认 data/wardrobe")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--force", action="store_true", help="已打码的也重打")
    args = ap.parse_args()

    root = Path(args.root) if args.root else config.WARDROBE_DIR
    want = {s.strip() for s in args.only.split(",") if s.strip()}

    n_done = n_skip = n_fail = n_all = 0
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if want and d.name not in want:
            continue
        tp = d / "thumb.jpg"
        if not tp.is_file():
            continue
        n_all += 1
        meta_path = d / "meta.json"
        meta = {}
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("privacy", {}).get("masked") and not args.force:
            n_skip += 1
            print(f"  ·  {d.name:22s} 已打码，跳过")
            continue

        im = Image.open(tp).convert("RGB")
        out, info = privacy.mask_head(im)
        if not info["masked"]:
            n_fail += 1
            print(f"  !  {d.name:22s} 未找到人脸（{info.get('reason', '?')}）")
            continue

        n_done += 1
        box = info["box"]
        print(f"  ✓  {d.name:22s} 打码 x{box[0]}..{box[2]} y{box[1]}..{box[3]}"
              f"  格子 {info['block']}px")
        if args.dry_run:
            continue
        out.save(tp, "JPEG", quality=86)
        if meta:
            meta["privacy"] = info
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    print(f"\n扫描 {n_all} 件：打码 {n_done}，已打过 {n_skip}，未识别人脸 {n_fail}"
          + ("（dry-run，未写盘）" if args.dry_run else ""))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
