"""人体语义解析（可选 ONNX 模型）—— 给衣服分割提供「哪里是衣服」的可靠先验。

为什么需要它：
    cloth.py 的色调聚类靠「一个主色种子 + 每行连续横带」长轮廓，遇到多色
    拼接、衣服与背景同色、复杂背景、侧身、袖子分离时，会只认出胸前一块。
    本模块用 SegFormer-B2 / ATR 18 类人体解析模型直接给出 Upper-clothes /
    Dress / Skirt 的语义区域，再交给 cloth.py 做填洞、多色扩张和羽化。

设计约定（很重要）：
- 纯本地 CPU 推理，不联网、不上传照片；
- 模型文件（models/segformer_clothes.onnx）或 onnxruntime 缺失时 available()
  返回 False，cloth.segment 自动回退到原来的色调启发式，程序照常可用；
- 模型输出是 128x128 的低分辨率类别图，按目标尺寸放大即可，因此同一张
  照片被 analyze / compose / debug 以不同分辨率反复调用时只推理一次
  （_last 缓存，用缩略图相关系数判定是否同一张照片）。
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import config

# ATR / mattmdjaga/segformer_b2_clothes 的 id2label
LABELS = (
    "Background", "Hat", "Hair", "Sunglasses", "Upper-clothes", "Skirt",
    "Pants", "Dress", "Belt", "Left-shoe", "Right-shoe", "Face",
    "Left-leg", "Right-leg", "Left-arm", "Right-arm", "Bag", "Scarf",
)
BACKGROUND = 0
UPPER_IDS = (4, 5, 7)          # 上衣 / 裙子 / 连衣裙：都要换装
LOWER_IDS = (6, 9, 10)         # 裤子 / 鞋：明确排除
SKIN_IDS = (11, 12, 13, 14, 15)  # 脸 / 四肢：模型常把袖子也判成 arm，
# 所以这个 mask 只作参考，真正扣皮肤用 cloth.skin_mask 的 RGB+YCbCr 判据
HEAD_IDS = (1, 2, 3)

MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)

_lock = threading.Lock()
_last: dict[str, Any] = {"thumb": None, "labels": None, "probs": None,
                         "input_size": None}
_UPPER_INDEX = np.asarray(UPPER_IDS, dtype=int)


@lru_cache(maxsize=1)
def session():
    """惰性加载 ONNX 会话；任何失败都返回 None（自动降级）。"""
    if not config.HUMAN_SEGMENT_ENABLED:
        return None
    path = config.HUMAN_MODEL_FILE
    if not path.is_file():
        return None
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(4, os.cpu_count() or 2))
        so.log_severity_level = 3
        return ort.InferenceSession(str(path), sess_options=so,
                                    providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 - 可选能力，失败必须静默降级
        print(f"[human] 语义模型不可用，回退色调分割：{type(exc).__name__}: {exc}")
        return None


def available() -> bool:
    return session() is not None


def status() -> dict[str, Any]:
    """给 --check / /api/stats 用的自检信息（不触发 52MB 模型加载）。"""
    import importlib.util

    path = config.HUMAN_MODEL_FILE
    return {
        "enabled": bool(config.HUMAN_SEGMENT_ENABLED),
        "model_file": path.name,
        "model_exists": path.is_file(),
        "model_mb": round(path.stat().st_size / 1048576, 1) if path.is_file() else 0,
        "runtime_ready": importlib.util.find_spec("onnxruntime") is not None,
        "engine": config.HUMAN_SEGMENT_PRIORITY,
    }


def _infer(im: Image.Image) -> tuple[np.ndarray, np.ndarray, list[int]]:
    sess = session()
    assert sess is not None
    inp = sess.get_inputs()[0]
    shape = list(inp.shape)
    ih = shape[2] if isinstance(shape[2], int) else config.HUMAN_INPUT_SIZE
    iw = shape[3] if isinstance(shape[3], int) else config.HUMAN_INPUT_SIZE
    rgb = np.asarray(im.convert("RGB").resize((iw, ih), Image.Resampling.BILINEAR),
                     dtype=np.float32) / 255.0
    x = ((rgb - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    logits = np.asarray(sess.run(None, {inp.name: x})[0], dtype=np.float32)
    if logits.ndim == 4:
        logits = logits[0]
    # 只在类别维做 softmax，用于边缘软融合；不需要逐像素保存全部概率，
    # 但要留一份「属于上衣的概率和」。
    e = np.exp(logits - logits.max(axis=0, keepdims=True))
    probs = e / np.maximum(e.sum(axis=0, keepdims=True), 1e-6)
    upper_prob = probs[_UPPER_INDEX].sum(axis=0)
    labels = logits.argmax(axis=0).astype(np.uint8)
    return labels, upper_prob, [iw, ih]


def _smooth_labels(low: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """类别图 → one-hot → 双线性上采样 → argmax，消除最近邻放大的方块阶梯。

    NEAREST 把 128x128 直接拉到 480x657 时，每个类别块是约 3.7px 的硬方格，
    肩带斜边会出现明显锯齿；逐类概率插值后再取 argmax 可以得到亚像素边界，
    且不会凭空产生第三类（过渡带只在相邻两类之间取值）。
    """
    w, h = size
    n = int(low.max()) + 1
    onehot = np.eye(n, dtype=np.float32)[low].transpose(2, 0, 1)
    big = ndi.zoom(onehot, (1, h / low.shape[0], w / low.shape[1]), order=1)
    return big.argmax(axis=0).astype(np.uint8)


def _same_photo(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    """缩放/锐化过的同一张照片，24x24 缩略图相关系数仍会很高。"""
    if a is None or b is None:
        return False
    # Similar framing is common in portrait libraries. Correlation is not an
    # identity check and can return another person's arm/clothing boundary.
    return a.shape == b.shape and np.array_equal(a, b)


def _resize(m: np.ndarray, size: tuple[int, int], nearest: bool) -> np.ndarray:
    w, h = size
    if m.shape[:2] == (h, w):
        return m
    mode = Image.Resampling.NEAREST if nearest else Image.Resampling.BILINEAR
    pil = Image.fromarray(m)
    try:
        return np.asarray(pil.resize((w, h), mode))
    except (ValueError, OSError):  # 某些 PIL 版本不支持 float32 的 BILINEAR
        if nearest:
            raise
        return np.asarray(pil.resize((w, h), Image.Resampling.NEAREST))


def parse(im: Image.Image) -> dict[str, Any] | None:
    """返回原图尺寸的语义 mask；模型不可用时返回 None。"""
    if not available():
        return None
    thumb = np.asarray(
        im.convert("RGB").resize((24, 24), Image.Resampling.BILINEAR),
        dtype=np.float32)
    with _lock:
        cached = _last["labels"]
        hit = _same_photo(thumb, _last["thumb"])
        if hit:
            low, up_low, input_size = (_last["labels"], _last["probs"],
                                       _last["input_size"])
    if not hit:
        try:
            low, up_low, input_size = _infer(im)
        except Exception as exc:  # noqa: BLE001 - 推理失败也必须能回退
            print(f"[human] 推理失败，回退色调分割：{type(exc).__name__}: {exc}")
            return None
        with _lock:
            _last.update(thumb=thumb, labels=low, probs=up_low,
                         input_size=input_size)
    w, h = im.size
    if getattr(config, "HUMAN_SMOOTH_LABELS", True):
        labels = _smooth_labels(low, (w, h))
    else:
        labels = _resize(low, (w, h), nearest=True)
    upper_prob = _resize(up_low, (w, h), nearest=False).astype(np.float32)
    upper = np.isin(labels, UPPER_IDS)
    return {
        "labels": labels,
        "upper": upper,
        "upper_prob": upper_prob,
        "person": labels != BACKGROUND,
        "background": labels == BACKGROUND,
        "skin": np.isin(labels, SKIN_IDS),
        "hair": np.isin(labels, HEAD_IDS),
        "lower": np.isin(labels, LOWER_IDS),
        "model": "segformer-b2-clothes",
        "input_size": input_size,
        "cached": bool(hit),
    }
