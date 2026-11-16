#!/usr/bin/env python3
"""下载渲染层 AI 化所需的两个 ONNX 模型（可复现，国内走 hf-mirror）。

    .venv/Scripts/python.exe scripts/fetch_render_models.py

产物：
  models/depth_anything_v2_small.onnx   26MB int8，随 exe 分发；推理时生成
                                        衣服表面的 2D warp 坐标场（R3）。
  models/upscale_realesr_general_x4.onnx  4.8MB fp32，只在开发机上离线超分
                                        55 张包装源图用（R2），不随 exe 分发。

许可：Depth Anything V2 Small 为 Apache-2.0；Real-ESRGAN 系列权重沿用上游
许可（见 ATTRIBUTION.md）。超分模型只做离线素材预处理，产物图片保留原图形状，
不把生成式内容带进程序运行时。
"""
from __future__ import annotations

import hashlib
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
SOURCE = MODELS / "_source"

D_MIRROR = "https://hf-mirror.com/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_quantized.onnx"
D_DIRECT = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_quantized.onnx"
S_MIRROR = "https://hf-mirror.com/baker76/upscale-models/resolve/main/RealESR_General_x4.onnx"
S_DIRECT = "https://huggingface.co/baker76/upscale-models/resolve/main/RealESR_General_x4.onnx"

ITEMS = (
    (MODELS / "depth_anything_v2_small.onnx", (D_MIRROR, D_DIRECT), 20 * 1024 * 1024,
     "Depth Anything V2 Small int8"),
    (MODELS / "upscale_realesr_general_x4.onnx", (S_MIRROR, S_DIRECT), 3 * 1024 * 1024,
     "RealESR General x4"),
)


def _reachable(url: str) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status in (200, 206)
    except Exception:
        return False


def download(dst: Path, urls: tuple[str, ...], min_size: int, label: str) -> None:
    if dst.is_file() and dst.stat().st_size > min_size:
        print(f"已有缓存：{dst.relative_to(ROOT)}  {dst.stat().st_size / 1048576:.1f} MB")
        return
    url = next((u for u in urls if _reachable(u)), None)
    if not url:
        raise SystemExit(f"{label} 下载源不可达；请检查网络或手动放置权重到 {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    print(f"下载 {label} <- {url.split('/')[2]} ...", flush=True)
    t0, done, last = time.time(), 0, 0.0
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r, tmp.open("wb") as f:
        total = int(r.headers.get("content-length") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 2:
                last = time.time()
                pct = f"{done / total * 100:.0f}%" if total else f"{done / 1048576:.0f} MB"
                print(f"  {pct}", flush=True)
    if done < min_size:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"{label} 下载不完整（{done} B），疑似错误页")
    tmp.replace(dst)
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()[:16]
    print(f"完成 {dst.relative_to(ROOT)}  {done / 1048576:.1f} MB  sha256:{sha}  "
          f"{time.time() - t0:.1f}s")


def main() -> None:
    for dst, urls, min_size, label in ITEMS:
        download(dst, urls, min_size, label)
    print("\n两个模型就绪。接下来：")
    print("  .venv/Scripts/python.exe scripts/upscale_brands.py")


if __name__ == "__main__":
    main()
