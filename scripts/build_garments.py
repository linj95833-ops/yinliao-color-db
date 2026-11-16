"""从 8 张基准成品图离线生成「衣服迁移」资产库。

只在这一步跑语义分割模型；产物是 strip.png（每行填满衣服像素的条带）、
profile.npz（逐行轮廓）与 meta.json，运行时 garment.py 直接加载，
不再需要任何模型，也不需要原始基准图。

用法： .venv/Scripts/python.exe scripts/build_garments.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beverage_color_db import config, garment  # noqa: E402

INDEX = ROOT / "report" / "baseline" / "samples" / "index.json"
SAMPLES = INDEX.parent
OUT = config.GARMENT_DIR


def main() -> int:
    doc = json.loads(INDEX.read_text(encoding="utf-8"))
    samples = doc.get("samples", [])
    if not samples:
        raise SystemExit("基准索引里没有 samples")
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in samples:
        src = SAMPLES / s["file"]
        if not src.is_file():
            print(f"  [SKIP] 缺文件 {src}")
            continue
        gid = Path(s["file"]).stem
        meta = garment.export_asset(
            src, OUT / gid, gid, s.get("brand_cn") or gid,
            s.get("brand_key"), s.get("layout_class") or "full_wrap",
            engine="model")
        rows.append(meta)
        print(f"  [OK] {gid:<26} {meta['layout_class']:<12} "
              f"rows={meta['src_rows']} shrink={meta['shrink']} "
              f"coverage={meta['mask_coverage']} engine={meta['segment_engine']}")
    summary = {
        "count": len(rows),
        "source_index": str(INDEX.relative_to(ROOT)),
        "width_ratio": config.GARMENT_WIDTH_RATIO,
        "method": "语义 mask 精确切割 + 有效行筛选 + 逐行条带填充",
        "note": "运行时只加载 strip.png 与 profile.npz；修改收窄参数后需重跑本脚本",
        "assets": rows,
    }
    (OUT / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n资产库 {OUT} 共 {len(rows)} 件；garment.status() = {garment.status()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
