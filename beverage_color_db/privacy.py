"""人头打码 —— 交付物里出现的脸一律打上马赛克。

只管「我们的素材」，绝不碰用户上传的照片：
    本模块只被 `wardrobe.build_item()`（离线建库）、`report/_probe_masks.py`
    和 `scripts/mask_*.py` 调用。换装链路（`compose.py` / `web.py`）一次都
    没有 import 它 —— 用户传进来的人像要原样换装，打了码就没法用了。

为什么要做：
    衣橱每件资产都来自一张「已经换好该品牌球衣的人像」，缩略图（thumb.jpg）
    是整张人像，网页衣橱卡片、`/api/thumb` 直接显示它；`report/` 里的证据图、
    演示合成图又把这些脸复制了一遍。这些都随包分发，把脸原样放进去不合适。
    换装只用衣服那一条（strip.webp），脸对功能没有任何用处。

两级检测：
    主用 YuNet（`models/face_detect_yunet.onnx`，230KB，libfacedetection 的
    轻量人脸检测）。它给的是**紧贴五官**的框，而且能在一张拼版图里同时找出
    十几张小脸 —— 人体解析模型做不到：三张并排的人像它只认出中间那张，还把
    脖子和胸口一起圈进去（打码会糊掉球衣图案）。
    备用是 `human.py` 的 SegFormer（ATR 第 11 类 Face）—— YuNet 文件缺失时
    顶上，保证老环境也能打码。

打多大的框：
    只有「脸」和「耳朵/下巴」需要盖，头发不泄露身份（美年达的粉色假发占掉
    半幅，整颗头打码会把缩略图糊掉一半）。框按脸外扩 18%，下半边再多给一点。
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage as ndi

from . import config, human

FACE_ID = 11        # SegFormer/ATR: Face
SUNGLASSES_ID = 3   # 墨镜也是脸的一部分，模型有时把它从 Face 里划出去
MIN_SIDE = 24       # 比这还小的图不处理
DETECT_SIZE = 640   # YuNet 的固定输入边长
SCORE_MIN = 0.60    # 低于这个分数当背景噪声
NMS_IOU = 0.35

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _yunet():
    """惰性加载 YuNet；文件缺失 / onnxruntime 不可用时返回 None。"""
    path = config.MODELS_DIR / "face_detect_yunet.onnx"
    if not path.is_file():
        return None
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(4, os.cpu_count() or 2))
        so.log_severity_level = 3
        return ort.InferenceSession(str(path), sess_options=so,
                                    providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 - 打码是附加能力，失败就降级
        print(f"[privacy] YuNet 不可用，回退语义分割：{type(exc).__name__}: {exc}")
        return None


def available() -> bool:
    return _yunet() is not None or human.available()


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float = NMS_IOU) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        union = np.maximum(area_i + area_r - inter, 1e-6)
        order = rest[(inter / union) <= iou]
    return keep


def _yunet_boxes(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """YuNet 检测：返回原图坐标下的紧贴脸框。"""
    sess = _yunet()
    if sess is None:
        return []
    W, H = im.size
    s = min(DETECT_SIZE / W, DETECT_SIZE / H)
    nw, nh = max(1, int(round(W * s))), max(1, int(round(H * s)))
    canvas = np.zeros((DETECT_SIZE, DETECT_SIZE, 3), dtype=np.float32)
    # YuNet 训练时吃 BGR、0~255、不归一化（与 OpenCV FaceDetectorYN 一致）。
    canvas[:nh, :nw] = np.asarray(
        im.convert("RGB").resize((nw, nh), Image.Resampling.BILINEAR),
        dtype=np.float32)[:, :, ::-1]
    with _lock:
        outs = sess.run(None, {"input": canvas.transpose(2, 0, 1)[None]})
    names = [o.name for o in sess.get_outputs()]
    d = dict(zip(names, outs))

    boxes: list[list[float]] = []
    scores: list[float] = []
    for stride in (8, 16, 32):
        cls = d[f"cls_{stride}"].reshape(-1)
        obj = d[f"obj_{stride}"].reshape(-1)
        bbox = d[f"bbox_{stride}"].reshape(-1, 4)
        cols = DETECT_SIZE // stride
        # YuNet 的分数是分类头与目标性头的几何平均
        score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        for i in np.flatnonzero(score >= SCORE_MIN):
            r, c = divmod(int(i), cols)
            cx = c * stride + bbox[i, 0] * stride
            cy = r * stride + bbox[i, 1] * stride
            bw = float(np.exp(bbox[i, 2]) * stride)
            bh = float(np.exp(bbox[i, 3]) * stride)
            boxes.append([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2])
            scores.append(float(score[i]))
    if not boxes:
        return []

    arr = np.asarray(boxes, dtype=float) / s
    keep = _nms(arr, np.asarray(scores))
    out = []
    for i in keep:
        x0, y0, x1, y1 = arr[i]
        out.append((max(0, int(round(x0))), max(0, int(round(y0))),
                    min(W, int(round(x1))), min(H, int(round(y1)))))
    return out


def _segformer_boxes(im: Image.Image, all_faces: bool) -> list[tuple[int, int, int, int]]:
    """备用检测：语义模型的 Face 类。框往往偏大（脖子、胸口一起圈进来）。"""
    parsed = human.parse(im)
    if parsed is None:
        return []
    labels = parsed["labels"]
    face = (labels == FACE_ID) | (labels == SUNGLASSES_ID)
    if int(face.sum()) < 12:
        return []
    cc, n = ndi.label(face)
    if n == 0:
        return []
    sizes = np.bincount(cc.ravel())
    sizes[0] = 0
    floor = 0.06 if all_faces else 0.2
    keep = [int(i) for i in np.flatnonzero(sizes >= floor * sizes.max()) if i]
    if not keep:
        return []
    if not all_faces:
        tops = [int(np.nonzero(cc == i)[0].min()) for i in keep]
        keep = [keep[int(np.argmin(tops))]]
    out = []
    for i in keep:
        ys, xs = np.nonzero(cc == i)
        if ys.size:
            out.append((int(xs.min()), int(ys.min()),
                        int(xs.max()) + 1, int(ys.max()) + 1))
    return out


def _face_boxes(im: Image.Image, pad: float,
                all_faces: bool = False) -> tuple[list[tuple[int, int, int, int]], str]:
    """返回（已外扩、已裁到画面内的脸框列表, 检测器名）。"""
    W, H = im.size
    raw = _yunet_boxes(im)
    engine = "yunet"
    if not raw:
        raw = _segformer_boxes(im, all_faces)
        engine = "segformer"
    if not raw:
        return [], "none"
    if not all_faces:
        # 单脸场景（一件资产 = 一张脸）：取面积最大的那块。
        raw = [max(raw, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))]

    out = []
    for x0, y0, x1, y1 in raw:
        w, h = x1 - x0, y1 - y0
        side = max(3, int(round(pad * max(w, h))))
        down = max(2, int(round(0.5 * side)))
        out.append((max(0, x0 - side), max(0, y0 - side),
                    min(W, x1 + side), min(H, y1 + down)))
    return out, engine


def _pixelate(im: Image.Image, box: tuple[int, int, int, int],
              block: int) -> Image.Image:
    """把 box 这块降到 block 像素一格，再逐格放大。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    small = im.crop(box).resize((max(1, w // block), max(1, h // block)),
                                Image.Resampling.BILINEAR)
    return small.resize((w, h), Image.Resampling.NEAREST)


def mask_head(im: Image.Image, *, pad: float = 0.18,
              block: int | None = None,
              all_faces: bool = False) -> tuple[Image.Image, dict[str, Any]]:
    """返回 (打码后的图, 说明)。找不到脸时原样返回并说明原因。

    block 为马赛克格子边长，默认按脸的大小取（约脸宽的 1/6，最小 5px）——
    固定格子数改不了「脸越大、格子越多、越能看出五官」这个事实。
    all_faces=True 把图里所有检测到的脸都打掉（演示图、拼版图）。
    """
    info: dict[str, Any] = {"masked": False}
    rgb = im.convert("RGB")
    if min(rgb.size) < MIN_SIDE:
        info["reason"] = "too_small"
        return im, info
    if not available():
        info["reason"] = "model_unavailable"
        return im, info

    boxes, engine = _face_boxes(rgb, pad, all_faces=all_faces)
    if not boxes:
        info["reason"] = "no_face"
        return im, info

    out = rgb.copy()
    done: list[list[int]] = []
    blocks: list[int] = []
    for box in boxes:
        x0, y0, x1, y1 = box
        blk = block or max(5, int(round(min(x1 - x0, y1 - y0) / 6.0)))
        # 硬边接缝在纯色背景上很扎眼；把蒙版边界羽化一格，观感像「刻意打码」
        # 而不是「贴了块补丁」。框已经比脸外扩 18%，这 1px 落在背景/头发上。
        alpha = Image.new("L", (x1 - x0, y1 - y0), 255).filter(
            ImageFilter.GaussianBlur(0.6))
        out.paste(_pixelate(out, box, blk), (x0, y0), alpha)
        done.append([x0, y0, x1, y1])
        blocks.append(int(blk))

    info.update({"masked": True, "engine": engine, "box": done[0],
                 "boxes": done, "block": blocks[0], "blocks": blocks})
    return out, info


def mask_bytes(data: bytes) -> bytes:
    """字节进、字节出；出错原样返回（打码不能挡住出图）。"""
    import io

    try:
        im = Image.open(io.BytesIO(data))
        out, info = mask_head(im)
        if not info["masked"]:
            return data
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=86)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return data
