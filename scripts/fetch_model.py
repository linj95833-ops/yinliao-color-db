#!/usr/bin/env python3
"""下载并准备「人体衣服解析」ONNX 模型（可复现，无需手动找权重）。

    .venv/Scripts/python.exe scripts/fetch_model.py            # 下载 + 量化
    .venv/Scripts/python.exe scripts/fetch_model.py --fp32     # 只保留原始精度

模型：mattmdjaga/segformer_b2_clothes（SegFormer-B2，在 ATR 人体解析数据集上微调）
标签：0 Background / 1 Hat / 2 Hair / 3 Sunglasses / 4 Upper-clothes /
      5 Skirt / 6 Pants / 7 Dress / 8 Belt / 9-10 Shoes / 11 Face /
      12-15 Limbs / 16 Bag / 17 Scarf
来源：Hugging Face（国内走 hf-mirror 镜像），Apache-2.0 许可，见 models/README.md。

产物：models/segformer_clothes.onnx —— 程序运行时唯一读取的模型文件。
      models/_source/          —— 原始 fp32 权重缓存，不随程序分发。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
SOURCE = MODELS / "_source" / "segformer_b2_clothes_fp32.onnx"
TARGET = MODELS / "segformer_clothes.onnx"

MIRRORS = [
    "https://hf-mirror.com/mattmdjaga/segformer_b2_clothes/resolve/main/onnx/model.onnx",
    "https://huggingface.co/mattmdjaga/segformer_b2_clothes/resolve/main/onnx/model.onnx",
]
EXPECTED_MIN = 100 * 1024 * 1024  # 明显小于此值说明下到了错误页/残缺文件


def _head(url: str) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status in (200, 206)
    except Exception:
        return False


def download() -> Path:
    if SOURCE.is_file() and SOURCE.stat().st_size > EXPECTED_MIN:
        print(f"已有缓存：{SOURCE}  ({SOURCE.stat().st_size / 1048576:.1f} MB)")
        return SOURCE
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    url = next((u for u in MIRRORS if _head(u)), None)
    if not url:
        raise SystemExit("下载源不可达；请检查网络，或手动把 model.onnx 放到 models/_source/")
    print(f"从 {url.split('/')[2]} 下载模型（约 110 MB）...", flush=True)
    t0 = time.time()
    tmp = SOURCE.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r, tmp.open("wb") as f:
        total = int(r.headers.get("content-length") or 0)
        done, last = 0, 0.0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 2:
                last = time.time()
                pct = f"{done / total * 100:.0f}%" if total else f"{done / 1048576:.0f} MB"
                print(f"  {pct}  {done / 1048576:.0f} MB", flush=True)
    tmp.replace(SOURCE)
    size = SOURCE.stat().st_size
    if size < EXPECTED_MIN:
        SOURCE.unlink(missing_ok=True)
        raise SystemExit(f"下载不完整（{size} 字节），已删除，请重试")
    print(f"下载完成：{size / 1048576:.1f} MB / {time.time() - t0:.0f} 秒", flush=True)
    return SOURCE


def quantize(src: Path) -> Path:
    """只量化 MatMul（SegFormer MLP 的大头），Conv 保持 fp32。

    ORT 的 CPU EP 对动态量化 Conv 生成的 ConvInteger 支持不全，全算子量化
    会得到一个「能生成、加载就报 NotImplemented」的模型。
    """
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:
        raise SystemExit("缺少量化依赖，请先 pip install onnx，或使用 --fp32") from exc
    tmp = TARGET.with_suffix(".int8.part")
    print("生成 int8 量化版（仅量化 MatMul，Conv 保持 fp32）...", flush=True)
    quantize_dynamic(str(src), str(tmp), weight_type=QuantType.QInt8,
                     op_types_to_quantize=["MatMul"],
                     per_channel=False, reduce_range=False)
    verify(tmp, quiet=True)  # 必须先能真正跑一次推理，再转正
    tmp.replace(TARGET)
    return TARGET


def verify(path: Path, quiet: bool = False) -> None:
    """加载 + 真实前向一次；失败时删掉坏文件，绝不留下不能用的模型。"""
    import numpy as np
    import onnxruntime as ort

    try:
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp, out = sess.get_inputs()[0], sess.get_outputs()[0]
        # 导出图的 height/width 常是符号名，必须按预处理配置补成 512x512，
        # 不能一律填 1（那样 C 会变成 1，Conv 报 channels 不匹配）。
        defaults = {0: 1, 1: 3, 2: 512, 3: 512}
        dims = []
        for i, d in enumerate(inp.shape):
            dims.append(d if isinstance(d, int) and d > 0 else defaults.get(i, 1))
        y = sess.run(None, {inp.name: np.zeros(dims, dtype=np.float32)})[0]
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"模型自检失败：{type(exc).__name__}: {exc}") from exc
    if quiet:
        return
    print(f"模型可加载：input={inp.name}{inp.shape} output={out.name}{out.shape}")
    print(f"推理自检通过：logits {np.asarray(y).shape}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    print(f"最终模型：{path}  {path.stat().st_size / 1048576:.1f} MB  "
          f"sha256={h.hexdigest()[:16]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fp32", action="store_true", help="不做量化，直接使用原始精度模型")
    ap.add_argument("--force", action="store_true", help="忽略已有产物，重新生成")
    args = ap.parse_args()
    MODELS.mkdir(exist_ok=True)
    if TARGET.is_file() and not args.force:
        print(f"已存在 {TARGET}，跳过（要重建请加 --force）")
        verify(TARGET)
        return
    src = download()
    if args.fp32:
        import shutil
        shutil.copy2(src, TARGET)
    else:
        try:
            quantize(src)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"量化失败（{type(exc).__name__}: {exc}），改用 fp32 原始模型", flush=True)
            import shutil
            shutil.copy2(src, TARGET)
    verify(TARGET)


if __name__ == "__main__":
    main()
