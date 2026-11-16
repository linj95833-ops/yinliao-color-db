"""单目深度估计 → 衣服表面视差场（R3）。

模型：Depth Anything V2 Small，int8 ONNX，Apache-2.0，约 26MB。
输出是「相对逆深度」(disparity，越大越靠近相机)，只用来生成采样坐标场，
**不产生任何图像像素** —— 包装上的品牌字样仍从原标签逐像素采样。

任何环节失败（模型缺失 / onnxruntime 不可用 / 推理异常）都返回 None，
调用方自动退回旧的 1D 逐行包络，程序不会崩、也不会静默改变构图。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

if __package__:
    from . import config
else:  # 允许被 report/ 下的诊断脚本直接导入
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from beverage_color_db import config

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_last: dict[str, Any] = {"thumb": None, "field": None}


def _m14(v: float) -> int:
    """模型输出尺寸是 14 的倍数，输入边长也按 14 对齐避免符号维度截断。"""
    return max(14, int(round(v / 14.0)) * 14)


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort
    path = Path(config.DEPTH_MODEL_FILE)
    if not path.is_file():
        raise FileNotFoundError(f"缺少深度模型：{path}")
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return False
    x, y = a.ravel() - a.mean(), b.ravel() - b.mean()
    den = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return den > 1e-6 and float((x * y).sum() / den) > 0.995


def _preprocess(im: Image.Image) -> tuple[np.ndarray, tuple[int, int]]:
    W, H = im.size
    s = float(config.WRAP_DEPTH_INPUT_SHORT) / min(W, H)
    iw, ih = _m14(W * s), _m14(H * s)
    small = np.asarray(im.resize((iw, ih), Image.Resampling.BILINEAR),
                       dtype=np.float32) / 255.0
    x = np.transpose(small, (2, 0, 1))[None]
    x = (x - MEAN[None, :, None, None]) / STD[None, :, None, None]
    return np.ascontiguousarray(x, dtype=np.float32), (iw, ih)


def field(im: Image.Image) -> np.ndarray | None:
    """返回与 im 同尺寸 (H, W) 的 0..1 视差场；失败返回 None。"""
    W, H = im.size
    thumb = np.asarray(im.convert("L").resize((24, 24), Image.Resampling.BILINEAR),
                       dtype=np.float32)
    if _same(thumb, _last["thumb"]) and _last["field"] is not None:
        if _last["field"].shape == (H, W):
            return _last["field"]
    try:
        x, _ = _preprocess(im)
        d = np.asarray(_session().run(None, {"pixel_values": x})[0][0],
                       dtype=np.float32)
        # 模型输出 (ih, iw)，双线性放回原图尺寸
        d = ndi.zoom(d, (H / d.shape[0], W / d.shape[1]), order=1)
        lo, hi = float(np.percentile(d, 1)), float(np.percentile(d, 99))
        d = np.clip((d - lo) / max(1e-6, hi - lo), 0.0, 1.0).astype(np.float32)
        _last.update(thumb=thumb.copy(), field=d)
        return d
    except Exception as exc:  # noqa: BLE001 - 模型不可用时必须静默降级
        print(f"[depth] 视差场不可用，回退 1D 包络：{type(exc).__name__}: {exc}")
        return None


def status() -> dict[str, Any]:
    """给 /api/stats 用的自检信息。"""
    path = Path(config.DEPTH_MODEL_FILE)
    info: dict[str, Any] = {"enabled": bool(getattr(config, "WRAP_DEPTH_WARP", True)),
                            "model": path.name, "exists": path.is_file(),
                            "size_mb": round(path.stat().st_size / 1048576, 1)
                            if path.is_file() else None}
    try:
        sess = _session()
        info["loadable"] = True
        info["inputs"] = [i.name for i in sess.get_inputs()]
    except Exception as exc:  # noqa: BLE001
        info["loadable"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def clear_cache() -> None:
    _last.update(thumb=None, field=None)
