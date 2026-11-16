"""基准衣服迁移引擎（garment transfer）。

思路：把「已经换好装的基准人像」当成衣服纹理来源，而不是把饮料瓶的
矩形标签贴上去。基准图里自带真实布料褶皱、图案随胸廓的透视变形、
单个居中 logo 与大面积负空间 —— 这些正是工程贴图做不出来的部分。

分两阶段，都只做确定性几何，运行时不加载任何神经网络：

  离线  export_asset()：语义模型分割 → 精确衣服 mask → 逐行轮廓 →
        条带填充，产出 strip.png / profile.npz / meta.json。
        语义模型只在这一步跑一次，之后资产可随程序分发。
  运行  build_texture()：把源衣服的逐行轮廓 (center_s, half_s) 与
        目标衣服 mask 的逐行轮廓 (center_d, half_d) 做归一化映射：
          纵向 v = (y - y0d)/(y1d - y0d) → sy = y0s + v*(y1s-y0s)
          横向 u = (x - center_d(y))/half_d(y) ∈ [-1,1]
                 → sx = center_s(sy) + u*half_s(sy)
        再用三次插值从 strip 采样出整张纹理画布。

v2 的两道切割保护（实证后加入）：
  - 有效行按「行宽 >= 峰值宽度 * GARMENT_WIDTH_RATIO」筛选并取最长连续段，
    排除领口尖角行 / 下摆碎行，否则目标领口会采到源背景；
  - 无有效像素的行不保留背景，直接复制最近的有效条带行；
  - 源端可按版式档向内收窄（local_print 档收窄更多，排除两侧外套/袖子）。

与 wrap.build_texture 保持相同返回结构，compose.py 可以无差别地消费。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import config

_CACHE: dict[str, dict[str, Any]] = {}


def _align_mode() -> str:
    """纹理坐标映射模式：rowwise（逐行贴合，旧观感）/ similarity（整件等比）。"""
    mode = str(getattr(config, "GARMENT_TEXTURE_ALIGN", "rowwise") or "").strip().lower()
    return "similarity" if mode.startswith("sim") else "rowwise"


# --------------------------------------------------------------------------
# 逐行轮廓
# --------------------------------------------------------------------------

def row_profile(mask: np.ndarray, width_ratio: float = 0.0,
                shrink: float = 0.0):
    """逐行轮廓：left/right/center/half + 有效行区间。

    width_ratio>0 时，只有「行宽 >= 峰值宽度 * ratio」的行才算有效，
    并取最长连续段，避免领口尖角行 / 下摆碎行把背景带进采样。
    shrink>0 时按逐行中心向内收窄，排除两侧外套/袖子。
    """
    H, W = mask.shape
    left = np.full(H, np.nan, dtype=float)
    right = np.full(H, np.nan, dtype=float)
    for y in range(H):
        xs = np.flatnonzero(mask[y])
        if xs.size >= 3:
            # 分位数按行内像素算，行越宽内切越多（500px 行会切掉 10px），
            # 这里同样封顶 3px；真正的收窄交给 shrink 参数显式控制。
            lo = min(float(np.percentile(xs, 2)), float(xs[0]) + 3.0)
            hi = max(float(np.percentile(xs, 98)), float(xs[-1]) - 3.0)
            if hi - lo > 2:
                left[y], right[y] = lo, hi
    valid = np.isfinite(left) & np.isfinite(right)
    idx = np.arange(H, dtype=float)
    left = np.interp(idx, idx[valid], left[valid])
    right = np.interp(idx, idx[valid], right[valid])
    sigma = max(2.0, H * 0.02)
    left = ndi.gaussian_filter1d(left, sigma)
    right = ndi.gaussian_filter1d(right, sigma)
    width = np.maximum(2.0, right - left)

    good = valid.copy()
    if width_ratio > 0 and valid.any():
        peak = float(np.percentile(width[valid], 90))
        good &= width >= peak * width_ratio
        runs, cur = [], None
        for y in range(H):
            if good[y] and cur is None:
                cur = [y, y]
            elif good[y]:
                cur[1] = y
            elif cur is not None:
                runs.append(cur)
                cur = None
        if cur is not None:
            runs.append(cur)
        if runs:
            best = max(runs, key=lambda r: r[1] - r[0])
            keep = np.zeros(H, dtype=bool)
            keep[best[0]:best[1] + 1] = True
            good = keep
    if not good.any():
        good = valid
    rows = np.flatnonzero(good)
    y0, y1 = int(rows[0]), int(rows[-1])

    if shrink > 0:
        c = (left + right) * 0.5
        h = (right - left) * 0.5 * (1.0 - shrink)
        left, right = c - h, c + h
    center = (left + right) * 0.5
    half = np.maximum(4.0, (right - left) * 0.5)
    return left, right, center, half, y0, y1, good


def row_fill(rgb: np.ndarray, mask: np.ndarray, left: np.ndarray,
             right: np.ndarray, good: np.ndarray) -> np.ndarray:
    """把源图变成「每行都被衣服像素填满」的条带图。

    逐行只看 [left, right] 区间：区间内被领口/字样挖出的洞用左右有效像素
    水平插值补齐，区间外平推边缘值。无有效像素的行复制最近有效条带行，
    绝不保留源背景。这样任意采样坐标都落在衣服上，不会把源人物的脸、
    皮肤、蓝色背景带进目标人像。
    """
    H, W = mask.shape
    out = np.asarray(rgb, dtype=float).copy()
    xs = np.arange(W, dtype=float)
    first = last = None
    for y in range(H):
        if not good[y]:
            continue
        if first is None:
            first = y
        lo = int(np.clip(round(left[y]), 0, W - 1))
        hi = int(np.clip(round(right[y]), 0, W - 1))
        if hi <= lo:
            continue
        seg = mask[y, lo:hi + 1]
        if seg.any():
            vx = xs[lo:hi + 1][seg]
            for c in range(3):
                out[y, lo:hi + 1, c] = np.interp(
                    xs[lo:hi + 1], vx, out[y, lo:hi + 1, c][seg])
        else:
            out[y, lo:hi + 1, :] = out[y, lo, :]
        out[y, :lo, :] = out[y, lo, :]
        out[y, hi + 1:, :] = out[y, hi, :]
        last = y
    if first is None:
        return out
    for y in range(0, first):
        out[y] = out[first]
    for y in range(last + 1, H):
        out[y] = out[last]
    for y in range(first, last + 1):
        if not good[y]:
            out[y] = out[max(first, y - 1)]
    return out


# --------------------------------------------------------------------------
# 离线资产导出
# --------------------------------------------------------------------------

def export_asset(src_path: str | Path, out_dir: Path, garment_id: str,
                 name_cn: str, brand_key: str | None,
                 layout_class: str, engine: str = "model") -> dict[str, Any]:
    """把一张基准成品图切成可复用的衣服资产。语义模型只在这里跑一次。"""
    from . import cloth  # 延迟导入：运行时 build_texture 不需要分割模型

    src_path = Path(src_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    im = cloth.load_image(src_path)
    arr = np.asarray(im, dtype=float)

    # 源端不套袖子收窄：基准多为短袖/无袖，避免把躯干两侧与主字一起切掉
    saved = config.CLOTH_SLEEVE_TRIM
    config.CLOTH_SLEEVE_TRIM = 0.0
    try:
        seg = cloth.segment(im, engine=engine)
    finally:
        config.CLOTH_SLEEVE_TRIM = saved
    mask = ndi.binary_fill_holes(np.asarray(seg["hard"], dtype=bool))

    shrink = float(getattr(config, "GARMENT_SHRINK_LOCAL", 0.30)
                   if layout_class == "local_print"
                   else getattr(config, "GARMENT_SHRINK_FULL", 0.0))
    ratio = float(getattr(config, "GARMENT_WIDTH_RATIO", 0.45))
    left, right, center, half, y0, y1, good = row_profile(mask, ratio, shrink)
    strip = row_fill(arr, mask, left, right, good)

    H, W = mask.shape
    Image.fromarray(np.rint(np.clip(strip, 0, 255)).astype(np.uint8)).save(
        out_dir / "strip.png")
    Image.fromarray((mask * 255).astype(np.uint8)).save(out_dir / "mask.png")
    np.savez_compressed(out_dir / "profile.npz",
                        center=center.astype(np.float32),
                        half=half.astype(np.float32),
                        good=good, y0=y0, y1=y1, height=H, width=W,
                        width_ratio=ratio, shrink=shrink)
    meta = {
        "id": garment_id,
        "name_cn": name_cn,
        "brand_key": brand_key,
        "layout_class": layout_class,
        "source_file": src_path.name,
        "size": [W, H],
        "src_rows": [y0, y1],
        "width_ratio": ratio,
        "shrink": shrink,
        "mask_coverage": round(float(mask.mean()), 4),
        "mask_bbox": seg["bbox"],
        "segment_engine": seg.get("engine"),
        "half_mean": round(float(half[y0:y1 + 1].mean()), 1),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE.pop(garment_id, None)
    return meta


# --------------------------------------------------------------------------
# 运行时
# --------------------------------------------------------------------------

def asset_roots() -> list[Path]:
    """资产可能在两处：新衣橱（wardrobe，基准人像）与旧基准（garments）。

    两者目录结构一致（strip + profile + meta），运行时不区分来源；
    新衣橱优先，所以同 id 会被 wardrobe 覆盖。
    """
    roots: list[Path] = []
    for p in (getattr(config, "WARDROBE_DIR", None), config.GARMENT_DIR):
        if p is None:
            continue
        p = Path(p)
        if p.is_dir() and p not in roots:
            roots.append(p)
    return roots


def _find_dir(garment_id: str) -> Path | None:
    for root in asset_roots():
        d = root / garment_id
        if (d / "profile.npz").is_file():
            return d
    return None


def has_asset(garment_id: str | None) -> bool:
    """资产是否存在（只看目录，不读像素）。compose 用它决定要不要按换装档
    的分割参数（袖子不收窄 + 轮廓外扩张）来分割目标人像 —— 资产缺失时应该
    完整回退到贴图档的行为，逐像素一致。"""
    if not garment_id:
        return False
    return _find_dir(str(garment_id)) is not None


def _load(garment_id: str) -> dict[str, Any] | None:
    if garment_id in _CACHE:
        return _CACHE[garment_id]
    d = _find_dir(garment_id)
    if d is None:
        return None
    strip_file = next((d / n for n in ("strip.png", "strip.webp", "strip.jpg")
                       if (d / n).is_file()), None)
    if strip_file is None:
        return None
    try:
        with np.load(str(d / "profile.npz")) as z:
            prof = {k: z[k] for k in z.files}
        strip = np.asarray(Image.open(strip_file).convert("RGB"), dtype=float)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    asset = {"strip": strip, "meta": meta, "dir": d, **prof}
    _CACHE[garment_id] = asset
    return asset


def list_garments() -> list[dict[str, Any]]:
    """返回可用的衣服资产（按 id 排序），供网页列表与 /api/garments。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in asset_roots():
        for f in sorted(root.glob("*/meta.json")):
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            gid = str(m.get("id") or f.parent.name)
            if gid in seen:
                continue
            has_strip = any((f.parent / n).is_file()
                            for n in ("strip.png", "strip.webp", "strip.jpg"))
            m["available"] = has_strip and (f.parent / "profile.npz").is_file()
            if m["available"]:
                seen.add(gid)
                out.append(m)
    return out


def status() -> dict[str, Any]:
    items = list_garments()
    return {"dir": str(getattr(config, "WARDROBE_DIR", config.GARMENT_DIR)),
            "count": len(items),
            "available": bool(items), "ids": [x["id"] for x in items]}


def build_texture(garment_id: str | None, shape: tuple[int, int], bbox,
                  cloth_mask=None, depth=None, detail=None) -> dict[str, Any]:
    """生成与人像同尺寸的「基准衣服」纹理画布，返回结构与 wrap 一致。

    reason 非空表示资产不可用，compose 会自动回退到包装贴图链路。
    cloth_mask 是目标衣服硬蒙版（收窄前的 wrap_hard），决定变形轮廓；
    depth/detail 在本引擎里不参与（基准图本身已带透视与褶皱）。

    alpha 是**基准衣服自己的轮廓**经同一坐标场变形后的结果：覆盖范围因此
    跟随基准的领口形状与袖型，而不是目标那件衣服的形状。西装人像实测：
    不传 alpha 时覆盖会一路顶到衬衫领、把领口层次抹平，并把外套袖子留下。
    """
    out: dict[str, Any] = {"texture": None, "source": None, "reason": None,
                           "continuous": True, "depth_warp": False,
                           "garment_id": garment_id, "alpha": None}
    if not garment_id:
        out["reason"] = "未指定基准衣服"
        return out
    asset = _load(garment_id)
    if asset is None:
        out["reason"] = f"基准衣服资产不可用：{garment_id}"
        return out
    if cloth_mask is None:
        out["reason"] = "缺少目标衣服蒙版"
        return out
    dst = np.asarray(cloth_mask, dtype=bool)
    Hd, Wd = int(shape[0]), int(shape[1])
    if dst.shape != (Hd, Wd):
        dst = np.asarray(Image.fromarray((dst * 255).astype(np.uint8))
                         .resize((Wd, Hd), Image.Resampling.NEAREST)) > 127
    if not dst.any():
        out["reason"] = "目标衣服蒙版为空"
        return out

    Hs = int(asset["height"])
    center_s = np.asarray(asset["center"], dtype=float)
    half_s = np.asarray(asset["half"], dtype=float)
    y0s, y1s = int(asset["y0"]), int(asset["y1"])
    ld, rd, cd, hd, y0d, y1d, _ = row_profile(dst, 0.0, 0.0)

    xx = np.arange(Wd, dtype=float)[None, :]
    # 纹理几何：衣服上的字必须是**正**的。早先是「逐行把源 half 映射到目标
    # half」，纵横缩放比能差 34%（0.905 vs 0.593），字被压成瘦高；竖直笔画还
    # 会跟着目标逐行行宽起伏，抖成波浪。现在整件衣服只用一个比例 s：
    #   · 中间主体走等比（相似变换）—— 字形完全不变；
    #   · 纵向填不满（目标比源高）时，上下各留一段「源边距」压进去补满，
    #     而不是重复同一行（重复行会在下摆接出一条硬边）。
    s_y = (y1d - y0d) / max(1.0, y1s - y0s)
    s_x = (float(np.median(hd[y0d:y1d + 1]))
           / max(1e-3, float(np.median(half_s[y0s:y1s + 1]))))
    # 比例取两者的较小值 —— **只缩不放**，图案永远完整落在目标衣服里。
    # 目标若是被画面裁断的近景，s_x 会偏大（把标签放到占满前胸），s_y 会偏小
    # （把字缩得看不清）；取小值最稳：宁可衣服上下留一段纯色，也不裁掉文字。
    s = float(np.clip(min(s_x, s_y), 1e-3, None))
    cy_s = (y0s + y1s) / 2.0
    cy_d = (y0d + y1d) / 2.0
    cx_s = float(np.interp(cy_s, np.arange(Hs), center_s))
    cx_d = float(np.interp(cy_d, np.arange(Hd), cd))
    Hd_span = float(y1d - y0d)
    span_s = float(y1s - y0s)
    yy = np.arange(Hd, dtype=float)[:, None] - y0d        # 0 .. Hd_span
    v_fill = np.zeros((Hd, 1), dtype=float)               # 纵向纯色过渡权重
    if span_s * s >= Hd_span - 1.0:
        # 等比就能铺满纵向：纯相似变换，多出的宽度居中裁掉
        sy = cy_s + (np.arange(Hd, dtype=float)[:, None] - cy_d) / s
    else:
        m_s = max(12.0, 0.06 * span_s)
        inner_s = max(1.0, span_s - 2.0 * m_s)
        inner_d = inner_s * s
        m_d = max(1.0, (Hd_span - inner_d) / 2.0)
        top = y0s + (yy / m_d) * m_s                       # 上边距压缩填满
        bot = y1s - ((Hd_span - yy) / m_d) * m_s           # 下边距压缩填满
        mid = (y0s + m_s) + (yy - m_d) * (inner_s / max(1e-3, inner_d))
        sy = np.where(yy <= m_d, top, np.where(yy >= Hd_span - m_d, bot, mid))
        # 这两段把源边距压了好几倍，采样出来是一条条横向摩尔纹；越靠边越
        # 应该退化成「纯布料色」，所以给一个 1→0 的权重去混主色。
        v_fill = np.where(
            yy <= m_d, 1.0 - yy / m_d,
            np.where(yy >= Hd_span - m_d,
                     1.0 - (Hd_span - yy) / m_d, 0.0))
        v_fill = np.clip(v_fill, 0.0, 1.0)
    sy = np.clip(sy, 0.0, Hs - 1.0)
    sx = cx_s + (xx - cx_d) / s
    # 目标比源宽很多时横向会不足：夹到该行最外侧的有效布料像素，让布料横向
    # 延展，而不是采到条带外的透明/背景像素（会撑出一圈黑边）。
    sy_i = np.clip(np.rint(sy[:, 0]).astype(int), 0, Hs - 1)
    half_row = np.maximum(half_s[sy_i], 1e-3)
    # 横向必须用**全局**中心与半宽：逐行取中心会让竖笔画在行与行之间左右错位，
    # 一个字被切成一截一截，看起来就是「横向拉宽 / 拖尾」。整幅只用一次平移
    # 加一次缩放，竖画才是直的。
    half_s_mid = float(np.median(half_s[y0s:y1s + 1]))
    half_d_mid = float(np.median(hd[y0d:y1d + 1]))
    dx = xx - cx_d
    adx = np.abs(dx)
    inner_half = max(1e-3, min(half_d_mid, half_s_mid * s))
    extra = np.maximum(half_d_mid - inner_half, 0.0)
    t_edge = np.clip((adx - inner_half) / max(extra, 1e-3), 0.0, 1.0)
    if extra > 0.0:
        # 目标比等比区宽：把源两侧那一段（half*(1-s)）压进多出来的部分
        sx_mid = cx_s + dx / s
        sx_edge = cx_s + np.sign(dx) * (half_s_mid * s
                                        + (half_s_mid - half_s_mid * s) * t_edge)
        sx = np.where(adx <= inner_half, sx_mid, sx_edge)
    else:
        t_edge = np.zeros((1, Wd), dtype=float)
        sx = cx_s + dx / s
    coords = np.stack([np.broadcast_to(sy, (Hd, Wd)),
                       np.broadcast_to(sx, (Hd, Wd))])
    if _align_mode() == "rowwise":
        # 旧观感（2026-11-16 之前）：逐行各向异性映射 —— 纵向把源衣服线性拉满
        # 目标高度，横向把**每一行**的源半宽拉到目标该行的半宽，中心也逐行取。
        # 图案因此完全贴合身形（肩宽腰窄都跟得上）；代价是同一根竖笔画在不同
        # 行被横向缩放不同，会轻微抖成波浪，字也随行宽被压窄或拉宽。
        span_d = max(1.0, float(y1d - y0d))
        span_sr = max(1.0, float(y1s - y0s))
        sy_1d = y0s + (np.arange(Hd, dtype=float) - y0d) * (span_sr / span_d)
        sy_1d = np.clip(sy_1d, float(y0s), float(y1s))
        sy_idx = np.clip(np.rint(sy_1d).astype(int), 0, Hs - 1)
        cx_row = center_s[sy_idx][:, None]
        hw_row = np.maximum(half_s[sy_idx], 1e-3)[:, None]
        cx_d_row = cd[:, None]
        hw_d_row = np.maximum(hd, 1e-3)[:, None]
        sx = cx_row + (xx - cx_d_row) * (hw_row / hw_d_row)
        sx = np.clip(sx, 0.0, float(asset["strip"].shape[1] - 1))
        coords = np.stack([np.broadcast_to(sy_1d[:, None], (Hd, Wd)), sx])
        # 逐行映射本身就填满，不需要「两侧/上下退化成主色」的补丁
        extra = 0.0
        t_edge = np.zeros((1, Wd), dtype=float)
        v_fill = np.zeros((Hd, 1), dtype=float)
    strip = asset["strip"]
    tex = np.stack([ndi.map_coordinates(strip[..., c], coords, order=3,
                                        mode="nearest") for c in range(3)],
                   axis=-1)
    if float(extra) > 0.0 or float(v_fill.max()) > 0.0:
        # 横向不足 / 纵向压缩这两段都不该「压缩采样」—— 把源边缘十几个像素
        # 压进两个像素只会得到摩尔纹；也不能只取边缘一个像素（袖口的白边会
        # 被拉成一整条假图案）。统一过渡到「该行布料的主色」：袖子外缘和
        # 上下边距都变成干净的同色渐变。
        ws = strip.shape[1]
        us = np.linspace(-0.8, 0.8, 9)
        cols = np.clip(np.rint(center_s[sy_i][:, None]
                               + half_s[sy_i][:, None] * us[None, :]).astype(int),
                       0, ws - 1)
        row_main = np.median(strip[sy_i[:, None], cols], axis=1)
        # 行主色本身是逐行算的，源里一条粉条/白条会让它逐行跳，直接铺上去
        # 就是一条条水平亮线；沿纵向平滑成渐变再混。
        row_main = ndi.gaussian_filter1d(row_main, max(2.0, Hd * 0.012), axis=0)
        row_main = row_main[:, None, :]
        w = np.maximum((t_edge * (extra > 0.0))[..., None],
                       v_fill[..., None])
        tex = tex * (1.0 - w) + row_main * w
    out["texture"] = np.clip(tex, 0.0, 255.0)
    out["source"] = str(asset["dir"] / "strip.png")
    out["garment_meta"] = asset["meta"]
    out["warp_rows"] = {"source": [y0s, y1s], "target": [y0d, y1d]}

    src_mask = _load_mask(asset)
    if src_mask is not None:
        alpha = ndi.map_coordinates(src_mask, coords, order=1, mode="nearest")
        out["alpha"] = np.clip(alpha, 0.0, 1.0)
    return out


def _load_mask(asset: dict[str, Any]) -> np.ndarray | None:
    """基准衣服的硬蒙版（0..1），与 strip 同尺寸；缺失则返回 None。

    返回的是**逐行填满的剪影**：领口那个洞会被补上。领口该开多大由目标人像
    自己的皮肤边界决定 —— 如果照搬源领口洞，源领口比目标领口宽时，中间那条
    缝不会被覆盖，成图上就是一圈原色描边（雪碧→王老吉实测）。
    """
    cached = asset.get("_mask")
    if cached is not None:
        return cached
    d = Path(asset["dir"])
    f = next((d / n for n in ("mask.png", "mask.webp", "mask.jpg")
              if (d / n).is_file()), None)
    if f is None:
        asset["_mask"] = None
        return None
    m = np.asarray(Image.open(f).convert("L"), dtype=float) / 255.0
    if m.shape[:2] != asset["strip"].shape[:2]:
        m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize(
            (asset["strip"].shape[1], asset["strip"].shape[0]),
            Image.Resampling.BILINEAR), dtype=float) / 255.0
    sil = _silhouette(m > 0.5)
    asset["_mask"] = sil
    return sil


def _silhouette(mask: np.ndarray) -> np.ndarray:
    """逐行 left..right 填满的剪影（填掉领口洞，保留肩/袖/下摆外形）。"""
    H, W = mask.shape
    out = np.zeros((H, W), dtype=float)
    left = np.full(H, np.nan)
    right = np.full(H, np.nan)
    for y in range(H):
        xs = np.flatnonzero(mask[y])
        if xs.size:
            left[y], right[y] = xs[0], xs[-1]
    rows = np.flatnonzero(np.isfinite(left))
    if rows.size == 0:
        return out
    idx = np.arange(H, dtype=float)
    left = np.interp(idx, idx[rows], left[rows])
    right = np.interp(idx, idx[rows], right[rows])
    out[idx >= rows[0]] = 1.0
    out[rows[-1] + 1:] = 0.0
    xs = np.arange(W, dtype=float)[None, :]
    out *= ((xs >= left[:, None]) & (xs <= right[:, None])).astype(float)
    return out
