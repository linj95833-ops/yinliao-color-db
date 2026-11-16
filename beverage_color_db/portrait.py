"""人像照片 -> 衣服主色（按色调分割，不再用矩形假设人体）。

流程：
1. cloth.segment() 做全图 Lab 色调聚类，区分 衣服 / 皮肤 / 背景 / 印花，
   长出贴合身体轮廓的衣服蒙版（V 领、无袖、号码与描边都处理）；
2. 只在这个蒙版内做 Lab 加权 KMeans，得到衣服 Top-N 色板；
3. debug_image 画出分割轮廓，而不是画一个矩形 ROI。

ROI (--box) 现在只是「去哪里找衣服颜色」的提示区，不再限制衣服形状。
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import cloth, color, config


def _font(size: int):
    """调试图也要用中文字体，默认字体会把中文画成方框。"""
    for path in config.COMPOSE_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_image(source) -> Image.Image:
    return cloth.load_image(source)


def parse_box(value: str | tuple | list | None) -> tuple[float, float, float, float]:
    """解析归一化取色提示区：'0.2,0.3,0.8,0.9' 或四元序列。"""
    if value is None:
        return config.PORTRAIT_DEFAULT_BOX
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 4:
            raise ValueError("box 需要 4 个数字：x1,y1,x2,y2")
        value = tuple(float(p) for p in parts)
    if len(value) != 4:
        raise ValueError("box 需要 4 个数字：x1,y1,x2,y2")
    x1, y1, x2, y2 = (float(v) for v in value)
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError("box 必须满足 0 <= x1 < x2 <= 1 且 0 <= y1 < y2 <= 1")
    return x1, y1, x2, y2


def _skin_mask(arr: np.ndarray) -> np.ndarray:
    """兼容旧调用点的肤色判据。"""
    return cloth.skin_mask(arr)


def _background_color(arr: np.ndarray) -> tuple[np.ndarray, bool]:
    """兼容旧调用点的背景估计。"""
    return cloth.background_color(arr)


def analyze(source, box=None, n_colors: int | None = None,
            exclude_skin: bool = True, exclude_bg: bool = True,
            engine: str | None = None) -> dict[str, Any]:
    """返回衣服主色、Top-N 色板、分割信息与置信度。

    engine: auto / model / tone，见 config.HUMAN_SEGMENT_PRIORITY。
    结果里的 engine 是**实际生效**的引擎（模型降级时会变成 tone）。
    """
    roi = parse_box(box)
    im = load_image(source)
    w, h = im.size
    arr = np.asarray(im, dtype=float)
    seg = cloth.segment(im, box=roi, exclude_skin=exclude_skin,
                        exclude_bg=exclude_bg, engine=engine)
    palette = cloth.palette_from_mask(arr, seg["mask"], n_colors)
    if not palette:
        raise ValueError("无法从人像照片中提取衣服颜色")

    x1, y1, x2, y2 = roi
    box_px = (int(x1 * w), int(y1 * h), max(int(x1 * w) + 1, int(x2 * w)),
              max(int(y1 * h) + 1, int(y2 * h)))
    coverage = seg["coverage"]
    top_ratio = palette[0]["ratio"]
    confidence = round(min(1.0, 0.45 * min(1.0, top_ratio / 0.6)
                           + 0.35 * min(1.0, coverage / 0.30)
                           + 0.20 * (1.0 - len(palette) / 6.0)), 4)
    return {
        "dominant_hex": palette[0]["hex"],
        "dominant_lab": palette[0]["lab"],
        "palette": palette,
        "box": list(roi),
        "box_px": list(box_px),
        "confidence": confidence,
        "coverage": coverage,
        "image_size": [w, h],
        "exclude_skin": bool(exclude_skin),
        "exclude_bg": bool(exclude_bg),
        "cloth": {
            "bbox": seg["bbox"],
            "safe_box": seg["safe_box"],
            "coverage": seg["coverage"],
            "cloth_hex": seg["cloth_hex"],
            "background_hex": seg["background_hex"],
            "neckline_y": seg["neckline_y"],
            "neckline_guessed": seg["neckline_guessed"],
            "fallback": seg["fallback"],
            "method": seg["method"],
            "clusters": seg["clusters"],
            "engine": seg.get("engine"),
            "model": seg.get("model"),
            "semantic_note": seg.get("semantic_note"),
        },
        "method": seg["method"],
        "engine": seg.get("engine"),
        "semantic_note": seg.get("semantic_note"),
    }


def debug_image(source, result: dict[str, Any], width: int = 620) -> Image.Image:
    """在原图上叠加衣服分割轮廓 + 底部色板，直观确认到底换的是哪块。"""
    im = load_image(source)
    target = result.get("image_size")
    if target and len(target) == 2 and list(im.size) != [int(target[0]), int(target[1])]:
        im = im.resize((int(target[0]), int(target[1])), Image.Resampling.LANCZOS)
    width = max(width, 420)  # 小图放大后再标注，色板文字才不会挤成豆腐块
    s = width / im.width
    im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                   Image.Resampling.LANCZOS)

    seg = cloth.segment(im, box=parse_box(result.get("box")),
                        exclude_skin=True, exclude_bg=True,
                        engine=result.get("engine"))
    hard = seg["hard"]
    if hard.shape[:2] != (im.height, im.width):
        hard = np.asarray(Image.fromarray((hard * 255).astype(np.uint8))
                          .resize(im.size, Image.Resampling.NEAREST)) > 127
    edge = cloth.outline(hard)
    tint = np.zeros((im.height, im.width, 4), dtype=np.uint8)
    tint[hard] = (255, 45, 95, 82)
    tint[edge] = (255, 255, 255, 235)
    annotated = Image.alpha_composite(
        im.convert("RGBA"), Image.fromarray(tint, "RGBA")).convert("RGB")

    band = 66
    canvas = Image.new("RGB", (im.width, im.height + band), "#ffffff")
    canvas.paste(annotated, (0, 0))
    d = ImageDraw.Draw(canvas)
    label_font = _font(max(12, int(im.width * 0.035)))
    cell_font = _font(max(9, int(im.width * 0.022)))
    items = result["palette"][:5]
    cell = im.width // max(1, len(items))
    for i, c in enumerate(items):
        x0 = i * cell
        x3 = im.width if i == len(items) - 1 else (i + 1) * cell
        d.rectangle([x0, im.height + 2, x3, im.height + band - 14], fill=c["hex"])
        d.text((x0 + 4, im.height + band - 11),
               f"{c['hex']} {c['ratio']:.0%}", fill="#1c2026", font=cell_font)
    if seg.get("engine") == "model":
        label = "白线 = 语义解析出的衣服轮廓"
    elif seg["fallback"]:
        label = "回退：矩形取色蒙版"
    else:
        label = "白线 = 色调分割出的衣服轮廓"
    if seg.get("semantic_note"):
        label = "模型未采用（已降级）· " + label
    d.text((8, 8), label, fill="#ffffff", font=label_font,
           stroke_width=2, stroke_fill="#b02a4a")
    return canvas
