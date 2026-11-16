#!/usr/bin/env python3
"""R2：用轻量超分模型把 55 张包装源图离线处理成高清素材（不改原图）。

    .venv/Scripts/python.exe scripts/fetch_render_models.py   # 先下载模型
    .venv/Scripts/python.exe scripts/upscale_brands.py

保真策略（关键）：**只注入超分模型学到的高频残差，低频结构严格来自原图**。

    feed      = 源图缩到最长边 <= 512
    sr        = 模型(feed)                       # 4x
    sr_low    = resize(downsample(sr), sr.size) # 把 sr 降采样再放大 = 去掉新增高频
    detail    = sr - sr_low                     # 模型新增的高频笔画细节
    base      = resize(原图, sr.size)            # 原图直接放大，字形结构不变
    out       = clip(base + alpha * detail)     # 只加高频，不改字形

这样字形、logo、色块的位置与形状完全由原图决定，模型不可能把它改成别的字。
每张图还会把结果下采样回原尺寸算 PSNR，低于阈值就自动降 alpha 重试。

产物：data/images_hd/<与 data/images 相同的目录结构>/<stem>.webp + MANIFEST.json。
程序通过 wrap.brand_image_path(..., prefer_hd=True) 优先读取，找不到就回退原图，
所以删掉 images_hd 目录即可完全回到旧行为。
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beverage_color_db import config, db, wrap  # noqa: E402

MODEL = config.MODELS_DIR / "upscale_realesr_general_x4.onnx"
OUT_DIR = config.DATA_DIR / "images_hd"
MAX_FEED = 512        # 超分输入最长边，控制内存与耗时
MAX_OUT = 1600        # 存盘最长边，控制交付包体积
ALPHA = 0.70          # 高频残差注入强度
PSNR_MIN = 34.0       # 下采样回原尺寸后的保真下限(dB)
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _resize(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """size = (width, height)，走 PIL LANCZOS，避免引入 cv2 依赖。"""
    return np.asarray(
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        .resize(size, Image.Resampling.LANCZOS), dtype=np.float32)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse < 1e-9 else 10.0 * math.log10(255.0 ** 2 / mse)


def source_paths() -> list[tuple[str, Path]]:
    """直接查库拿原图路径，不走 HD 优先逻辑（保证脚本可重复运行）。"""
    with db.connect() as con:
        rows = con.execute("SELECT key FROM brands ORDER BY key").fetchall()
    out = []
    for r in rows:
        p = wrap.brand_image_path(r["key"], prefer_hd=False)
        if p and p.is_file() and p.suffix.lower() in EXTS:
            out.append((r["key"], p))
    return out


def upscale(sess: ort.InferenceSession, src: Path) -> tuple[np.ndarray, dict]:
    with Image.open(src) as im:
        orig = np.asarray(im.convert("RGB"), dtype=np.float32)
    H, W = orig.shape[:2]
    scale = min(1.0, MAX_FEED / max(H, W))
    fw, fh = max(8, round(W * scale)), max(8, round(H * scale))
    feed = _resize(orig, (fw, fh))

    x = feed.transpose(2, 0, 1)[None] / 255.0
    sr = sess.run(None, {"input": x})[0][0]
    sr = np.clip(np.transpose(sr, (1, 2, 0)) * 255.0, 0, 255).astype(np.float32)
    oh, ow = sr.shape[:2]

    # 原图直接放大 = 低频结构基准；超分降采样再放大 = 去掉模型新增高频
    base = _resize(orig, (ow, oh))
    sr_low = _resize(_resize(sr, (fw, fh)), (ow, oh))
    detail = sr - sr_low

    alpha, info = ALPHA, {}
    for attempt in range(3):
        out = np.clip(base + alpha * detail, 0, 255)
        back = _resize(out, (W, H))
        psnr = _psnr(back, orig)
        if psnr >= PSNR_MIN:
            break
        alpha *= 0.5
    info.update(src_size=[W, H], out_size=[ow, oh], alpha=round(alpha, 3),
                psnr=round(psnr, 2), feed=[fw, fh])
    return out, info


def main() -> None:
    if not MODEL.is_file():
        raise SystemExit("缺少超分模型，请先运行 scripts/fetch_render_models.py")
    pairs = source_paths()
    print(f"待处理 {len(pairs)} 张包装源图，模型 {MODEL.name}")
    sess = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest, t0 = {}, time.time()
    for i, (key, src) in enumerate(pairs, 1):
        out, info = upscale(sess, src)
        # 限制存盘尺寸，避免 4x 结果让交付包膨胀
        ow, oh = info["out_size"]
        if max(ow, oh) > MAX_OUT:
            s = MAX_OUT / max(ow, oh)
            out = _resize(out, (max(8, round(ow * s)), max(8, round(oh * s))))
            info["saved_size"] = [out.shape[1], out.shape[0]]
        rel = src.relative_to(config.IMAGE_DIR).with_suffix(".webp")
        dst = OUT_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out.astype(np.uint8)).save(dst, "WEBP", quality=92, method=5)
        info.update(dst=str(dst.relative_to(ROOT)).replace("\\", "/"),
                    bytes=dst.stat().st_size,
                    source=str(src.relative_to(ROOT)).replace("\\", "/"))
        manifest[key] = info
        if i % 10 == 0 or i == len(pairs):
            print(f"  {i}/{len(pairs)}  psnr均值 "
                  f"{np.mean([v['psnr'] for v in manifest.values()]):.1f} dB", flush=True)

    meta = {
        "model": "baker76/upscale-models / RealESR_General_x4.onnx",
        "model_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        "alpha": ALPHA, "psnr_min": PSNR_MIN,
        "max_feed": MAX_FEED, "max_out": MAX_OUT,
        "note": "只注入超分高频残差，低频结构来自原图；删除本目录即回到原始素材",
        "brands": manifest,
    }
    (OUT_DIR / "MANIFEST.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(v["bytes"] for v in manifest.values()) / 1048576
    print(f"完成 {len(manifest)} 张，{total:.1f} MB，耗时 {time.time() - t0:.1f}s")
    print(f"PSNR min/mean: {min(v['psnr'] for v in manifest.values()):.2f} / "
          f"{np.mean([v['psnr'] for v in manifest.values()]):.2f} dB")
    print(f"清单 {OUT_DIR / 'MANIFEST.json'}")


if __name__ == "__main__":
    main()
