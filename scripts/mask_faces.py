"""给任意目录下的图片打码（人脸马赛克）—— 交付物里的图都要过这一遍。

唯一不动的是**用户上传的照片**：换装链路上（`compose.py` / `web.py`）从不
import `privacy`，用户传什么就渲染什么。

用在对的地方：
    report/    证据图、对比图、演示合成图（随 samples 包分发）
    examples/  随程序发的示例人像
    data/      衣橱缩略图（建库时已经打过，这里再跑一遍只会报 no_face）

用法：
    python scripts/mask_faces.py                       # 默认 report + examples + data/wardrobe
    python scripts/mask_faces.py report examples       # 指定目录
    python scripts/mask_faces.py report/_wd_real_sheet.png
    python scripts/mask_faces.py report --dry-run      # 只看会动哪些

多脸：默认把一张图里所有「够大」的脸都打掉（演示图、拼版图常有好几个人）。
幂等：处理过的文件按 mtime+size 记进 `.facemask_state.json`，重跑直接跳过；
     真改了图（重新生成的证据图）mtime 变了才会再打一次。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from beverage_color_db import privacy  # noqa: E402

EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_ROOTS = ("report", "examples", "data/wardrobe")
# 已经处理过的文件记在这里（路径 → mtime+size）。人脸检测器对「马赛克过的脸」
# 有时还能再框一次，重复打码会叠出两层格子，所以靠台账跳过，而不是靠重新检测。
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".facemask_state.json"


def load_state() -> dict[str, dict]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, dict]) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=0,
                                sort_keys=True), encoding="utf-8")


def iter_images(target: Path):
    if target.is_file():
        if target.suffix.lower() in EXTS:
            yield target
        return
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() in EXTS:
            # 打码本身不需要 _thumb_orig 之外的备份目录，但备份里的是原图，
            # 打掉就没法再做前后对比了，跳过。
            if "_thumb_orig" in p.parts:
                continue
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", default=list(DEFAULT_ROOTS),
                    help="目录或图片路径，默认 report examples data/wardrobe")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--single", action="store_true",
                    help="每张图只打最靠上的那张脸（默认全打）")
    ap.add_argument("--force", action="store_true",
                    help="忽略台账，已处理过的也重新检测")
    args = ap.parse_args()

    state = load_state()
    n_img = n_hit = n_skip = n_fail = 0
    for t in args.targets:
        target = Path(t)
        if not target.exists():
            print(f"  !  {t} 不存在")
            continue
        for p in iter_images(target):
            n_img += 1
            try:
                key = p.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                key = p.resolve().as_posix()
            st = p.stat()
            rec = state.get(key)
            if not args.force and rec and rec.get("mtime") == st.st_mtime_ns \
                    and rec.get("size") == st.st_size:
                n_skip += 1
                continue
            try:
                im = Image.open(p).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                n_fail += 1
                print(f"  !  {p} 打不开：{type(exc).__name__}")
                continue
            out, info = privacy.mask_head(im, all_faces=not args.single)
            try:
                shown = p.resolve().relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                shown = p.as_posix()
            if not info["masked"]:
                if not args.dry_run:
                    state[key] = {"mtime": st.st_mtime_ns, "size": st.st_size,
                                  "boxes": 0}
                continue
            n_hit += 1
            print(f"  ✓  {shown}  {len(info.get('boxes', []))} 张脸"
                  f"（{info.get('engine', '?')}）")
            if args.dry_run:
                continue
            if p.suffix.lower() == ".png":
                out.save(p, "PNG", optimize=True)
            elif p.suffix.lower() == ".webp":
                out.save(p, "WEBP", quality=95, method=4)
            else:
                out.save(p, "JPEG", quality=88)
            st2 = p.stat()
            state[key] = {"mtime": st2.st_mtime_ns, "size": st2.st_size,
                          "boxes": len(info.get("boxes", []))}

    if not args.dry_run:
        save_state(state)
    print(f"\n扫描 {n_img} 张图：打码 {n_hit} 张，已处理过 {n_skip} 张"
          + ("（dry-run，未写盘）" if args.dry_run else "")
          + (f"，打不开 {n_fail}" if n_fail else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
