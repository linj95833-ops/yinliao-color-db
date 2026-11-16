"""颜色与图像指纹算法。

- pHash：DCT 感知哈希，同品牌内去重
- 提色：边缘背景剔除 + 中心加权 + Lab 空间加权 KMeans，输出 Top-N 主色及占比
- CIEDE2000：第二阶段「衣服颜色 -> 品牌」的色差度量
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image

from . import config

_RGB2XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_XYZ2RGB = np.linalg.inv(_RGB2XYZ)
_WHITE = np.array([0.95047, 1.00000, 1.08883])
_DELTA = 6.0 / 29.0
_DCT = np.cos(
    (2 * np.arange(config.PHASH_MATRIX, dtype=float)[:, None] + 1)
    * np.pi * np.arange(config.PHASH_MATRIX, dtype=float)[None, :]
    / (2 * config.PHASH_MATRIX)
)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB(0-255, shape Nx3) -> CIE L*a*b* (D65)。"""
    c = np.asarray(rgb, dtype=float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _RGB2XYZ.T
    t = xyz / _WHITE
    f = np.where(t > _DELTA ** 3, np.cbrt(t), t / (3 * _DELTA ** 2) + 4.0 / 29.0)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> tuple[int, int, int]:
    lab = np.asarray(lab, dtype=float)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    finv = np.where(f > _DELTA, f ** 3, 3 * _DELTA ** 2 * (f - 4.0 / 29.0))
    xyz = finv * _WHITE
    lin = xyz @ _XYZ2RGB.T
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055)
    return tuple(int(v) for v in np.round(np.clip(srgb, 0, 1) * 255))


def lab_to_rgb_array(lab: np.ndarray) -> np.ndarray:
    """Lab 数组 (...,3) -> uint8 RGB 数组 (...,3)，供整图换色使用。

    与 lab_to_rgb 使用同一套 D65 转换；区别只在返回值形状（后者返回单色 tuple）。
    """
    a = np.asarray(lab, dtype=float)
    fy = (a[..., 0] + 16) / 116
    fx = fy + a[..., 1] / 500
    fz = fy - a[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    finv = np.where(f > _DELTA, f ** 3, 3 * _DELTA ** 2 * (f - 4.0 / 29.0))
    xyz = finv * _WHITE
    lin = xyz @ _XYZ2RGB.T
    srgb = np.where(lin <= 0.0031308, 12.92 * lin,
                    1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055)
    return np.rint(np.clip(srgb, 0, 1) * 255).astype(np.uint8)


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


def hex_to_lab(value: str) -> tuple[float, float, float]:
    s = value.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"颜色必须是 #RRGGBB: {value}")
    rgb = np.array([[int(s[i:i + 2], 16) for i in (0, 2, 4)]], dtype=float)
    return tuple(float(v) for v in rgb_to_lab(rgb)[0])


def ciede2000(lab1, lab2) -> float:
    """CIEDE2000 色差，越小越接近。"""
    L1, a1, b1 = (float(x) for x in lab1)
    L2, a2, b2 = (float(x) for x in lab2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cbar = (C1 + C2) / 2
    g = 0.5 * (1 - math.sqrt(cbar ** 7 / (cbar ** 7 + 25 ** 7))) if cbar > 0 else 0.5
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p > 0 else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p > 0 else 0.0

    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))

    Lbarp = (L1 + L2) / 2
    Cbarp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbarp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbarp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbarp = (h1p + h2p + 360) / 2
    else:
        hbarp = (h1p + h2p - 360) / 2

    T = (1 - 0.17 * math.cos(math.radians(hbarp - 30))
         + 0.24 * math.cos(math.radians(2 * hbarp))
         + 0.32 * math.cos(math.radians(3 * hbarp + 6))
         - 0.20 * math.cos(math.radians(4 * hbarp - 63)))
    dtheta = 30 * math.exp(-((hbarp - 275) / 25) ** 2)
    Rc = 2 * math.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25 ** 7)) if Cbarp > 0 else 0
    Sl = 1 + 0.015 * (Lbarp - 50) ** 2 / math.sqrt(20 + (Lbarp - 50) ** 2)
    Sc = 1 + 0.045 * Cbarp
    Sh = 1 + 0.015 * Cbarp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    dL, dC, dH = dLp / Sl, dCp / Sc, dHp / Sh
    return math.sqrt(max(0.0, dL ** 2 + dC ** 2 + dH ** 2 + Rt * dC * dH))


def to_rgb(img: Image.Image) -> Image.Image:
    """透明 PNG 合成到白底，避免 alpha 区域被当成黑色像素。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def suggest_remove_background(img: Image.Image) -> bool:
    """判断是否为「带独立纯色背景」的图（白底电商图 / 深色影棚图）。

    判据：四角近白或近黑、且与画面中心有明显色差；或四角色彩高度一致、
    与中心差异明显。包装占满画面的标准正面图（四角就是包装）返回 False，
    否则背景剔除会把品牌主色一起抹掉。纯白包装整图则不会被误判。
    """
    arr = np.asarray(
        img.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR), dtype=float
    )
    k = 6
    corner = np.concatenate([
        arr[:k, :k].reshape(-1, 3), arr[:k, -k:].reshape(-1, 3),
        arr[-k:, :k].reshape(-1, 3), arr[-k:, -k:].reshape(-1, 3),
    ])
    bg = np.median(corner, axis=0)
    center = np.median(arr[16:48, 16:48].reshape(-1, 3), axis=0)
    dist = float(np.linalg.norm(bg - center))
    spread = float(np.mean(np.std(corner, axis=0)))
    neutral = float(bg.min()) > 225 or float(bg.max()) < 35

    # 电商白底图里产品常只占中间一小块，32x32 的「中心」仍大半是白，
    # 中心色差 dist 很小，只靠四角判据会漏判，白底就会凭 80%+ 占比变成主色。
    # 因此补一条整图统计：大片近白/近黑 + 仍存在彩色包装像素 => 独立背景。
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sat = mx - mn
    blank = max(float(((mn > 225) & (sat < 25)).mean()),
                float(((mx < 45) & (sat < 25)).mean()))
    colored = float((sat > 40).mean())
    if blank >= 0.30 and colored >= 0.02:
        return True
    return bool((neutral and dist > 25) or (spread < 20 and dist > 30))


def phash(img: Image.Image) -> str:
    """64bit 感知哈希，返回 16 位 hex 字符串。"""
    g = np.asarray(img.convert("L").resize(
        (config.PHASH_MATRIX, config.PHASH_MATRIX), Image.Resampling.LANCZOS
    ), dtype=float)
    low = (_DCT @ g @ _DCT.T)[:config.PHASH_LOW, :config.PHASH_LOW]
    med = np.median(low[1:])
    bits = (low > med).ravel()
    return format(int("".join("1" if b else "0" for b in bits), 2), "016x")


def hamming(h1: str, h2: str) -> int:
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def extract_colors(img: Image.Image, remove_background: bool = True) -> list[dict[str, Any]]:
    """返回按「品牌识别度」降序的 Top-N 主色。

    remove_background=True 适用于有独立背景的图（Commons 白底/场景照）：
      用四边中位色估计背景并降权，避免背景色压过包装色。
    remove_background=False 适用于 OFF / 官网的「标准产品正面图」：
      包装主体占满画面，四边本身就是包装（如红牛罐的蓝白边缘），
      此时再做背景剔除会把品牌主色直接抹掉。只保留中心/饱和度加权。
    """
    from sklearn.cluster import KMeans

    im = img.convert("RGB")
    w, h = im.size
    # 中心裁剪：先去掉外圈背景（产品主体通常在中央）
    cw, ch = max(1, int(w * 0.78)), max(1, int(h * 0.78))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    im = im.crop((x0, y0, x0 + cw, y0 + ch))
    scale = min(1.0, math.sqrt(config.COLOR_SAMPLES / max(1, cw * ch)))
    if scale < 1:
        im = im.resize((max(1, int(cw * scale)), max(1, int(ch * scale))), Image.Resampling.BILINEAR)
    arr = np.asarray(im, dtype=float)
    H, W, _ = arr.shape

    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - (W - 1) / 2) / (W / 2)) ** 2
                + ((yy - (H - 1) / 2) / (H / 2)) ** 2) / math.sqrt(2)
    center_w = np.clip(1.0 - 0.35 * r, 0.5, 1.0)

    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sat = mx - mn
    sat_w = 0.18 + 0.82 * np.clip(sat / 75.0, 0, 1)
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    dark_w = np.clip(lum / 95.0, 0.15, 1.0)

    # 背景剔除：四边像素中位色，接近者降权。
    # 仅对「有独立背景」的图启用；正面图里四边就是包装本身。
    k = max(1, min(H, W) // 16)
    edge = np.concatenate([
        arr[:k].reshape(-1, 3), arr[-k:].reshape(-1, 3),
        arr[:, :k].reshape(-1, 3), arr[:, -k:].reshape(-1, 3),
    ])
    bg = np.median(edge, axis=0)
    bg_like = np.linalg.norm(arr - bg, axis=2) < 42 if remove_background else np.zeros((H, W), dtype=bool)
    extreme = ((mx > 247) | (mn < 8)) & (sat < 12)
    if remove_background:
        # 有独立背景（Commons）：压低背景、深色液体、灰白区域
        weight = (center_w * sat_w * dark_w
                  * np.where(bg_like, 0.06, 1.0) * np.where(extreme, 0.1, 1.0))
    else:
        # 标准正面图（OFF/官网）：包装占满画面，深蓝、白、银都是品牌配色的一部分。
        # 不做强饱和度/暗度加权（会把红牛深蓝压成棕灰），但需温和压低近黑像素，
        # 否则暗背景/黑色文字区会凭像素占比盖过品牌色。
        dark_w_front = np.clip(lum / 70.0, 0.35, 1.0)
        # 白底商品图 / 深色背景里，大片近白或近黑会凭占比盖过包装色。
        # 只做温和降权（不做整片剔除），白色包装本身仍能被识别。
        near_white = (mn > 225) & (sat < 25)
        near_black = (mx < 45) & (sat < 25)
        weight = (center_w * dark_w_front
                  * np.where(extreme, 0.45, 1.0)
                  * np.where(near_white, 0.40, 1.0)
                  * np.where(near_black, 0.55, 1.0))

    pixels, pw = arr.reshape(-1, 3), weight.ravel()
    keep = pw > 0.005
    pixels, pw = pixels[keep], pw[keep]
    if len(pixels) < 50:  # 极端暗色/纯色图兜底
        pixels, pw = arr.reshape(-1, 3), np.ones(H * W)
    if len(pixels) > config.COLOR_SAMPLES:
        prob = pw / pw.sum()
        idx = np.random.default_rng(config.COLOR_SEED).choice(
            len(pixels), config.COLOR_SAMPLES, replace=False, p=prob
        )
        pixels, pw = pixels[idx], pw[idx]

    lab = rgb_to_lab(pixels)
    kmeans = KMeans(n_clusters=min(config.N_COLORS, max(1, len(pixels) // 200)),
                    n_init=3, random_state=config.COLOR_SEED)
    kmeans.fit(lab, sample_weight=pw)
    counts = np.bincount(kmeans.labels_, weights=pw, minlength=kmeans.n_clusters)
    ratios = counts / counts.sum()

    scores = []
    for i in range(kmeans.n_clusters):
        rgb = lab_to_rgb(kmeans.cluster_centers_[i])
        csat = max(rgb) - min(rgb)
        # 正面图按真实占比主导，只给彩色一点轻微偏好；场景图才需要强力拉高饱和度
        sat_factor = ((0.30 + 0.70 * min(1.0, csat / 90.0)) if remove_background
                      else (0.75 + 0.25 * min(1.0, csat / 90.0)))
        scores.append(ratios[i] * sat_factor)

    out: list[dict[str, Any]] = []
    for i in np.argsort(-np.array(scores)):
        ratio = float(ratios[i])
        if ratio < config.MIN_COLOR_RATIO:
            continue
        center = kmeans.cluster_centers_[i]
        rgb = lab_to_rgb(center)
        out.append({
            "hex": rgb_to_hex(rgb),
            "rgb": list(rgb),
            "lab": [round(float(v), 3) for v in center],
            "ratio": round(ratio, 4),
            "score": round(float(scores[i]), 4),
            "method": "kmeans-lab-v2-bg" if remove_background else "kmeans-lab-v2-front",
        })
    return out
