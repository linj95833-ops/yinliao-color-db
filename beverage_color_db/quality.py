"""图像质检闸门（宁缺毋滥）。

关键设计：**质量前移到图源，而不是靠背景算法事后补救**。

标定依据（对旧库 163 张 + OFF 正面图样本逐张计算，见 report/quality.md）：
  Commons 干净图：corner_std 中位 5.9 / dominant 0.465 / entropy 0.375
  Commons 场景照：corner_std 中位 61.3 / dominant 0.145 / entropy 0.545
  OFF 红牛正面罐：corner_std 53.3 / dominant 0.175 / entropy 0.532
  OFF 雪碧正面瓶：corner_std 89.2 / dominant 0.068 / entropy 0.671

OFF 的 `image_front_url` 是人工标注的「产品正面照」，包装主体占满画面，
四角本身就是包装边缘，用「背景一致性」判据会把它误杀掉（旧版踩过这个坑）。
因此按图源分层：
  off / local / manual → 语义可信，只排除空白图与噪声图
  official            → 中等：排除官网 banner、UI 图标、拼贴页
  commons             → 严格：必须纯色/白底背景且颜色集中（拒场景照、货架、混装）
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

# 图源 → 质检策略、参与品牌主色画像的信任权重
SOURCE_POLICY: dict[str, tuple[str, float]] = {
    "off": ("trust", 1.0),
    "local": ("trust", 1.0),
    "manual": ("trust", 1.0),
    "official": ("medium", 0.9),
    "commons": ("strict", 0.8),
}

# 阈值（由真实图片分布标定，不是拍脑袋）
TRUST_MIN_DOMINANT = 0.07
TRUST_MAX_ENTROPY = 0.80
MEDIUM_MIN_DOMINANT = 0.09
MEDIUM_MAX_ENTROPY = 0.74
MEDIUM_MIN_SUBJECT = 0.20
MEDIUM_MAX_ASPECT = 1.8
STRICT_MAX_CORNER_STD = 25.0
STRICT_MIN_WHITE = 0.25
STRICT_MIN_DOMINANT = 0.20
STRICT_MAX_ENTROPY = 0.58
# 官网 banner / 宣传片截图几乎都是超宽或超大图，直接排除
MAX_IMAGE_WIDTH = 1400
MAX_IMAGE_HEIGHT = 1400
MAX_IMAGE_PIXELS = 1_300_000


def _flatten(im: Image.Image) -> Image.Image:
    """透明 PNG 合成到白底，否则 alpha 区域会被当成黑色噪声。"""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return bg.convert("RGB")
    return im.convert("RGB")


def image_metrics(im: Image.Image) -> dict[str, float]:
    """计算一组与「是否干净单品包装图」相关的可解释指标。"""
    a = np.asarray(_flatten(im).resize((96, 96), Image.Resampling.BILINEAR), dtype=np.float32)
    k = 6  # 四角 6x6 小块：避免把占满画面的产品边缘当成杂乱背景
    corners = np.concatenate([
        a[:k, :k].reshape(-1, 3), a[:k, -k:].reshape(-1, 3),
        a[-k:, :k].reshape(-1, 3), a[-k:, -k:].reshape(-1, 3),
    ])
    corner_std = float(np.std(corners, axis=0).mean())
    white_ratio = float(((a > 235).all(axis=2)).mean())

    # 8x8x8 粗量化颜色直方图：颜色集中度主色占比 / 归一化熵
    q = (a // 32).astype(int)
    keys = q[:, :, 0] * 64 + q[:, :, 1] * 8 + q[:, :, 2]
    cnt = np.bincount(keys.ravel(), minlength=512) / (96 * 96)
    nz = cnt[cnt > 0]
    entropy = float(-(nz * np.log(nz)).sum() / np.log(512))
    dominant = float(cnt.max())

    bg = np.median(corners, axis=0)
    subject = float((np.linalg.norm(a - bg, axis=2) > 60).mean())
    # 外圈 3px 与四角主色一致的比例：标准正面图外圈是包装/白底，
    # 随手拍/宣传场景照外圈混着桌面、显示器、水面等，一致性明显更低。
    band = np.concatenate([
        a[:3].reshape(-1, 3), a[-3:].reshape(-1, 3),
        a[:, :3].reshape(-1, 3), a[:, -3:].reshape(-1, 3),
    ])
    edge_uniform = float((np.linalg.norm(band - bg, axis=1) < 45).mean())
    flat_std = float(np.std(a.reshape(-1, 3), axis=0).mean())
    # 最大颜色簇的饱和度：品牌包装主色通常鲜艳，桌面/宣传场景的主色偏灰棕
    top = int(cnt.argmax())
    top_rgb = np.array([top // 64, (top // 8) % 8, top % 8], dtype=float) * 32 + 16
    top_sat = float((top_rgb.max() - top_rgb.min()) / 255.0)
    return {
        "corner_std": round(corner_std, 2),
        "white_ratio": round(white_ratio, 4),
        "entropy": round(entropy, 4),
        "dominant": round(dominant, 4),
        "subject": round(subject, 4),
        "edge_uniform": round(edge_uniform, 4),
        "top_sat": round(top_sat, 4),
        "flat_std": round(flat_std, 2),
    }


def priority_score(m: dict[str, Any], trust: float = 1.0) -> float:
    """包装图质量分，用于「多下载、择优保留」。

    颜色集中（单品正面图）、主色鲜艳（品牌包装色）、颜色不过散、图源可信。
    只用于排序，不作为拒收条件 —— 统计指标做不到 100% 语义判别。
    """
    dom = min(1.0, float(m.get("dominant", 0.0)) / 0.45)
    sat = min(1.0, float(m.get("top_sat", 0.0)) / 0.6)
    ent = float(m.get("entropy", 1.0))
    return round(trust * (0.35 + 0.40 * dom + 0.25 * sat) * (1.0 - 0.25 * ent), 4)


def evaluate(im: Image.Image, source: str) -> tuple[bool, str, dict[str, Any], float]:
    """返回 (是否入库, 原因, 指标, 信任权重)。不合格一律拒收。"""
    policy, trust = SOURCE_POLICY.get(source, ("strict", 0.7))
    w, h = im.size
    aspect = max(w, h) / max(1, min(w, h))
    m = image_metrics(im)

    # 全局：空白图 / 纯色占位图 / 几乎全白
    if m["flat_std"] < 6.0:
        return False, "blank or flat placeholder", m, trust
    if m["white_ratio"] > 0.97:
        return False, "almost blank white image", m, trust
    # 全局：官网 banner / 宣传片截图 / 超大海报
    if w > MAX_IMAGE_WIDTH or h > MAX_IMAGE_HEIGHT or w * h > MAX_IMAGE_PIXELS:
        return False, f"banner/hero image {w}x{h}", m, trust

    if policy == "trust":
        if m["dominant"] < TRUST_MIN_DOMINANT or m["entropy"] > TRUST_MAX_ENTROPY:
            return False, "front image looks like noise/collage", m, trust
        return True, "trusted product front image", m, trust

    if policy == "medium":
        if aspect > MEDIUM_MAX_ASPECT:
            return False, f"banner-like aspect {aspect:.2f}", m, trust
        if m["dominant"] < MEDIUM_MIN_DOMINANT or m["entropy"] > MEDIUM_MAX_ENTROPY:
            return False, "not a single-product shot", m, trust
        if m["subject"] < MEDIUM_MIN_SUBJECT:
            return False, "no clear subject", m, trust
        return True, "official site product shot", m, trust

    # strict：Commons 兜底，必须背景干净且颜色集中
    clean_bg = m["corner_std"] <= STRICT_MAX_CORNER_STD or m["white_ratio"] >= STRICT_MIN_WHITE
    if not clean_bg:
        return False, f"scene/background too noisy (corner_std={m['corner_std']})", m, trust
    if m["dominant"] < STRICT_MIN_DOMINANT or m["entropy"] > STRICT_MAX_ENTROPY:
        return False, "multi-object or cluttered color", m, trust
    return True, "clean single-product shot", m, trust
