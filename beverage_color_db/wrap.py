"""包装贴图：把品牌产品实物图变成可以「穿」在衣服上的纹理。

和旧的「平涂主色 + 程序画字」不同，本模块直接使用品牌目录里那张包装图上的
真实视觉（渐变、图案、中文/英文艺术字、logo），把它们延展、轻微曲面化之后
交给 compose.py 按衣服软边 mask 覆盖，并在原图光照场里做明暗调制。
衣服上不会出现任何由程序另外绘制/打印的品牌字，只有包装图自带的文字与图案。

处理步骤（只用 numpy / Pillow / scipy，不新增依赖）：
1. 找品牌图片，转成 RGB；
2. 按 config/brand_wrap.yaml 里**该品牌自己的参数**先裁掉多余白底 / 旋转；
3. 找出与画面边框连通的近白 / 近黑背景，标签内部的白字黑字保留；
4. 用「同一行瓶身主色」或品牌主色把背景补成自然渐变；
5. 按彩色主体的行列投影裁出「标签主视觉」，可调亮度 / 对比 / 饱和度；
6. 整张画布从**同一张等比放大的标签**连续采样（超出边界把边缘行/列平推
   延展），并按衣服逐行包络轻度形变 —— 没有「模糊渐变底 + 胸口清晰矩形」
   的双层拼接，衣服内部不存在任何硬接缝；
7. 放大后做「笔画重绘」（局部对比 + 颜色归位 + 边缘自适应锐化），把多级
   Lanczos 低通掉的软边重新描实，再轻微的胸廓曲面形变。

低分辨率源图（55 张里 47 张短边 < 300px）被放大到球衣尺寸时，只靠插值
必然发虚；这里不生成任何新的文字，只是把包装图上**已有**的笔画边缘还原。

每个品牌的构图差别很大（白底小瓶 / 竖长罐 / 横版多品 / 纯 logo），
统一参数必然顾此失彼，所以 config/brand_wrap.yaml 为 55 个品牌逐个记录
裁剪框与微调值；没有配置或字段缺失时回落到 config.py 的 WRAP_* 默认值。

图片太小 / 没有可用前景 / 文件缺失时返回 reason，由 compose 自动回退到旧风格。
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image
from scipy import ndimage as ndi

from . import config, db

_OVERRIDE_FILE = config.ROOT / "config" / "brand_wrap.yaml"


@functools.lru_cache(maxsize=1)
def _overrides() -> dict[str, dict[str, Any]]:
    """读取逐品牌参数表；文件不存在/格式错误时安全退化为全默认。"""
    try:
        data = yaml.safe_load(_OVERRIDE_FILE.read_text(encoding="utf-8")) or {}
        raw = data.get("brands") or {}
        defaults = data.get("defaults") or {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): {**defaults, **(v or {})} for k, v in raw.items()
                if isinstance(v, (dict, type(None)))}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _v(p: dict[str, Any], name: str):
    """取参数：逐品牌值优先，其次 config.WRAP_* 默认值。

    name 形如 WRAP_SIDE_PAD，对应 yaml 里的 side_pad。
    """
    return p.get(name[5:].lower(), getattr(config, name))


def _hex_rgb(value: str) -> np.ndarray:
    s = str(value).lstrip("#")
    if len(s) != 6:
        raise ValueError(f"颜色必须是 #RRGGBB：{value}")
    return np.asarray([int(s[i:i + 2], 16) for i in (0, 2, 4)], dtype=float)


def brand_image_path(key: str, prefer_hd: bool = True) -> Path | None:
    """优先取使用者手动填的图（USER_PROVIDED），其次取其它已入库图。

    prefer_hd=True 时，若 `data/images_hd/` 下有对应的离线超分副本则优先用它；
    HD 目录与原图库目录结构一一对应、文件后缀统一为 .webp。删掉 HD 目录即可
    逐像素回到旧的渲染结果，所以超分是可完全回退的素材层升级。
    """
    try:
        with db.connect() as con:
            row = con.execute(
                """SELECT i.path FROM images i JOIN brands b ON b.id = i.brand_id
                   WHERE b.key = ? AND i.status = 'ok'
                   ORDER BY CASE WHEN i.source = ? THEN 0 ELSE 1 END, i.id LIMIT 1""",
                (key, config.USER_IMAGE_SOURCE),
            ).fetchone()
        if row:
            p = config.ROOT / row["path"]
            if p.is_file():
                if prefer_hd and getattr(config, "WRAP_USE_HD_SOURCE", True):
                    try:
                        rel = p.relative_to(config.IMAGE_DIR).with_suffix(".webp")
                        hd = config.HD_IMAGE_DIR / rel
                        if hd.is_file():
                            return hd
                    except (ValueError, AttributeError):
                        pass
                return p
    except Exception:  # noqa: BLE001 - 没有图 / 库未初始化都不应让合成失败
        pass
    return None


def _brand_hex(key: str) -> str | None:
    try:
        with db.connect() as con:
            row = con.execute("SELECT dominant_hex FROM profiles WHERE key = ?",
                              (key,)).fetchone()
        return row["dominant_hex"] if row else None
    except Exception:  # noqa: BLE001
        return None


def _crop_rotate(arr: np.ndarray, p: dict[str, Any]) -> np.ndarray:
    """先按归一化框裁源图，再按需旋转；白底小瓶图靠这一步把主体放大。"""
    box = p.get("crop")
    if box:
        if len(box) != 4:
            raise ValueError("crop 需要 [x1,y1,x2,y2] 四个归一化坐标")
        h, w = arr.shape[:2]
        x1, y1, x2, y2 = [float(v) for v in box]
        xa, xb = int(round(x1 * w)), int(round(x2 * w))
        ya, yb = int(round(y1 * h)), int(round(y2 * h))
        xa, ya = max(0, xa), max(0, ya)
        xb, yb = min(w, xb), min(h, yb)
        if xb - xa >= 8 and yb - ya >= 8:
            arr = arr[ya:yb, xa:xb]
    k = int(p.get("rotate", 0) or 0) % 4
    return np.rot90(arr, k).copy() if k else arr


def _background(arr: np.ndarray, p: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """返回 (背景 mask, 前景 mask)：只把与边框连通的近白/近黑区域当背景。"""
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    near_white = ((mn > _v(p, "WRAP_WHITE_MIN"))
                  & ((mx - mn) < _v(p, "WRAP_WHITE_SPAN")))
    near_black = mx < _v(p, "WRAP_BLACK_MAX")
    plain = near_white | near_black
    labels, _ = ndi.label(plain)
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    border_ids = [int(v) for v in np.unique(edge) if v != 0]
    bg = np.isin(labels, border_ids) if border_ids else np.zeros_like(plain)
    return bg, ~bg


def _body_white_mask(arr: np.ndarray, bg: np.ndarray,
                     p: dict[str, Any]) -> np.ndarray:
    """找出「属于瓶体自身的白色」，这些白不能当背景补掉。

    白色罐体（六个核桃）/ 透明瓶身（娃哈哈）在白底商品图里和背景连成一片，
    只看「是否与边框连通」会把瓶身白一起判成背景。这里用彩色像素（蓝标、
    核桃图案、红色 logo 等）的行列投影求出瓶体外接框：框内的近白像素属于
    瓶体，保持白色；框外与边框连通的白才是真正要补掉的背景。
    仅对配置了 keep_body_white 的品牌启用，其余品牌像素完全不变。
    """
    if not float(p.get("keep_body_white", 0) or 0):
        return np.zeros_like(bg)
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    colored = (sat > 0.18) & (mx > 60)
    ys = np.flatnonzero(colored.mean(axis=1) > 0.02)
    xs = np.flatnonzero(colored.mean(axis=0) > 0.02)
    if ys.size < 8 or xs.size < 8:
        return np.zeros_like(bg)
    h, w = bg.shape
    py, px = max(2, int(h * 0.02)), max(2, int(w * 0.03))
    box = np.zeros_like(bg)
    box[max(0, int(ys[0]) - py):min(h, int(ys[-1]) + 1 + py),
        max(0, int(xs[0]) - px):min(w, int(xs[-1]) + 1 + px)] = True
    return box & bg


def _fill_background(arr: np.ndarray, bg: np.ndarray, fg: np.ndarray,
                     p: dict[str, Any], brand_hex: str | None) -> np.ndarray:
    """白底补成包装色渐变；白底占大部分时可换成品牌主色，避免整件衣服发白。

    keep_body_white 开启时，瓶体外接框内的白色保持为瓶体本色，
    只有框外的摄影棚白底才参与补色。
    """
    H, W = arr.shape[:2]
    row = np.zeros((H, 3), dtype=float)
    fallback = np.median(arr[fg], axis=0) if fg.any() else np.full(3, 220.0)
    last = fallback.copy()
    for y in range(H):
        line = arr[y][fg[y]]
        if line.shape[0] >= 3:
            last = np.median(line, axis=0)
        row[y] = last
    sigma = max(2.0, H * _v(p, "WRAP_BG_BLUR_FRAC"))
    row = ndi.gaussian_filter(row, (sigma, 0))
    fill = np.broadcast_to(row[:, None, :], (H, W, 3))
    mix = float(p.get("bg_mix", 0) or 0)
    if mix > 0:
        hex_value = p.get("bg_hex") or brand_hex
        if hex_value:
            try:
                target = _hex_rgb(str(hex_value))
                # 品牌色提亮一点，作为球衣底色不至于压掉包装上的深色文字
                light = target * 0.78 + 255.0 * 0.22
                fill = fill * (1.0 - mix) + light * mix
            except ValueError:
                pass
    out = np.where(bg[..., None], fill, arr)
    keep = _body_white_mask(arr, bg, p)
    if keep.any():
        # 瓶体白：只提亮到干净的白，不染成品牌色，也不被行主色覆盖
        body = np.clip(arr * 0.30 + 255.0 * 0.70, 0.0, 255.0)
        out = np.where(keep[..., None], body, out)
    return out


def _label_crop(arr: np.ndarray, fg: np.ndarray,
                p: dict[str, Any]) -> np.ndarray | None:
    """按彩色主体的行列投影裁出标签带，去掉瓶盖与瓶底收缩段。"""
    threshold = _v(p, "WRAP_ROW_MIN")
    rows, cols = fg.mean(axis=1), fg.mean(axis=0)
    ys = np.flatnonzero(rows > threshold)
    xs = np.flatnonzero(cols > threshold)
    if ys.size < 8 or xs.size < 8:
        return None
    y1, y2, x1, x2 = int(ys[0]), int(ys[-1]) + 1, int(xs[0]), int(xs[-1]) + 1
    h = y2 - y1
    y1 += int(h * _v(p, "WRAP_TRIM_TOP"))
    y2 -= int(h * _v(p, "WRAP_TRIM_BOTTOM"))
    if y2 - y1 < 8 or x2 - x1 < 8:
        return None
    return arr[y1:y2, x1:x2]


def _trim_light_edges(label: np.ndarray, p: dict[str, Any]) -> np.ndarray:
    """切掉瓶身两侧的浅色反光/透明边，放大后不会变成两条浅色竖带。"""
    H, W = label.shape[:2]
    if W < 24 or H < W * 1.1 or p.get("keep_body_white"):
        # 横版包装 / 方图不做这个假设；白色瓶身的品牌也保留两侧白边
        return label
    mx, mn = label.max(axis=2), label.min(axis=2)
    bright = label.mean(axis=2)
    saturation = (mx - mn) / np.maximum(mx, 1.0)
    light = ((bright > _v(p, "WRAP_EDGE_LIGHT_MIN"))
             & (saturation < _v(p, "WRAP_EDGE_LIGHT_SAT")))
    col = light.mean(axis=0)
    k = max(1, int(W * _v(p, "WRAP_EDGE_TRIM_FRAC")))
    th = _v(p, "WRAP_EDGE_LIGHT_RATIO")
    left = k if float(np.median(col[:3])) > th else 0
    right = W - k if float(np.median(col[-3:])) > th else W
    if right - left < W * 0.55:
        return label
    return label[:, left:right]


def _focus(label: np.ndarray, p: dict[str, Any]) -> np.ndarray:
    """把标签再裁到「主字 / 主图案」所在的窗口。

    白底瓶装图的品牌主字常常偏在上半部（元气森林的「气」）或下半部
    （农夫山泉红标、七喜的 7），统一取中段就只剩一片白。focus 用相对
    标签的归一化坐标 [y1,y2] 或 [x1,y1,x2,y2] 明确指定主视觉位置。
    """
    f = p.get("focus")
    if not f or len(f) not in (2, 4):
        return label
    h, w = label.shape[:2]
    box = [0.0, float(f[0]), 1.0, float(f[1])] if len(f) == 2 else [float(v) for v in f]
    xa, ya = max(0, int(box[0] * w)), max(0, int(box[1] * h))
    xb, yb = min(w, int(box[2] * w)), min(h, int(box[3] * h))
    if xb - xa < 8 or yb - ya < 8:
        return label
    return label[ya:yb, xa:xb]


def _enhance(label: np.ndarray, p: dict[str, Any],
             brand_hex: str | None) -> np.ndarray:
    """淡色/暗色包装的亮度、对比、饱和度补偿；tint 把浅白部分染成品牌色。

    tint 只作用于「亮且低饱和」的像素：白瓶身 / 透明瓶 / 白底会变成该品牌
    的浅色调，包装上的深色文字与彩色图案原样保留，白花花一片的衣服就有了
    品牌识别度。默认全部关闭，不改变已有观感。
    """
    bright = float(p.get("bright", 1) or 1)
    contrast = float(p.get("contrast", 1) or 1)
    boost = float(p.get("boost", 1) or 1)
    tint = float(p.get("tint", 0) or 0)
    if bright == 1 and contrast == 1 and boost == 1 and tint <= 0:
        return label
    a = np.clip(label * bright, 0, 255)
    if contrast != 1:
        a = np.clip((a - 128.0) * contrast + 128.0, 0, 255)
    if boost != 1:
        gray = a.mean(axis=2, keepdims=True)
        a = np.clip(gray + (a - gray) * boost, 0, 255)
    # 白色瓶身的品牌不染色：白是包装的一部分，不是需要补掉的底色
    keep_white = bool(float(p.get("keep_body_white", 0) or 0))
    if tint > 0 and not keep_white:
        hex_value = p.get("tint_hex") or brand_hex
        if hex_value:
            try:
                strength = float(p.get("tint_mix", 0.55) or 0.55)
                target = (_hex_rgb(str(hex_value)) * strength
                          + 255.0 * (1.0 - strength))
                mx, mn = a.max(axis=2), a.min(axis=2)
                sat = (mx - mn) / np.maximum(mx, 1.0)
                light = a.mean(axis=2)
                w = (np.clip((light - 165.0) / 75.0, 0.0, 1.0)
                     * np.clip((0.28 - sat) / 0.28, 0.0, 1.0)
                     * min(1.0, tint))
                a = a * (1.0 - w[..., None]) + target * w[..., None]
            except ValueError:
                pass
    return np.clip(a, 0, 255)


def _upscale(a: np.ndarray, size: tuple[int, int], p: dict[str, Any],
             pre: bool = True) -> np.ndarray:
    """Lanczos 放大；放大前先做细半径预锐化，抵消插值核的低通效应。

    源标签常常只有 70~160px 宽，直接 Lanczos 会把笔画边缘抹成渐变；
    先在原始分辨率上补一点高频，再放大，边缘明显更实且不会过冲。
    """
    strength = float(_v(p, "WRAP_DETAIL") or 0)
    x = a
    if pre and strength > 0:
        amount = float(_v(p, "WRAP_DETAIL_PRE") or 0) * strength
        if amount > 0:
            x = np.clip(x + amount * (x - ndi.gaussian_filter(x, (0.6, 0.6, 0))),
                        0.0, 255.0)
    u8 = Image.fromarray(np.rint(np.clip(x, 0, 255)).astype(np.uint8))
    return np.asarray(u8.resize(size, Image.Resampling.LANCZOS), dtype=float)


def _restore_detail(a: np.ndarray, p: dict[str, Any],
                    factor: float = 1.0) -> np.ndarray:
    """放大后把包装笔画「重新描实」，不产生任何程序绘制的文字。

    三步都只针对包装图已有的像素：
    1. 以较大半径高斯为局部均值，拉回局部对比（白底更白、彩字更实）；
    2. 高饱和区域颜色归位，把插值产生的灰蒙蒙过渡推回纯色端点；
    3. 边缘掩码引导的细半径 USM，只锐化笔画，不放大平坦区域的噪点。
    """
    strength = float(_v(p, "WRAP_DETAIL") or 0) * factor
    if strength <= 0:
        return a
    x = np.clip(a, 0.0, 255.0)
    # 局部均值半径随目标分辨率等比缩放（6.0 对应 318px 短边的实验基准）
    sig_big = (float(_v(p, "WRAP_DETAIL_SIG_BIG") or 6.0)
               * max(0.35, min(x.shape[:2]) / 318.0))
    local = ndi.gaussian_filter(x, (sig_big, sig_big, 0))
    gain = 1.0 + (float(_v(p, "WRAP_DETAIL_GAIN") or 1.0) - 1.0) * strength
    x = local + (x - local) * gain
    mx, mn = x.max(axis=2), x.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    push = float(_v(p, "WRAP_DETAIL_PUSH") or 0) * strength
    w = np.clip((sat - 0.12) / 0.35, 0.0, 1.0) * push
    # 只在笔画/边缘附近归位：平坦白底上的 JPEG 压缩噪点饱和度也低，
    # 若无条件放大就会变成一颗颗彩色斑点（重绘强度高时尤其明显）。
    fine = np.abs(x - ndi.gaussian_filter(x, (1.0, 1.0, 0))).mean(axis=2)
    near_edge = ndi.gaussian_filter(np.clip(fine / 10.0, 0.0, 1.0), 1.2)
    w = w * np.clip(near_edge * 1.6, 0.0, 1.0)
    gray = x.mean(axis=2, keepdims=True)
    pure = np.clip(gray + (x - gray) * (1.0 + 0.5 * strength), 0.0, 255.0)
    x = x * (1.0 - w[..., None]) + pure * w[..., None]
    sig_small = float(_v(p, "WRAP_DETAIL_SIG_SMALL") or 0.75)
    hp = x - ndi.gaussian_filter(x, (sig_small, sig_small, 0))
    # 阈值抬高到 20：只压笔画边缘，平坦区域的噪点不再被 USM 放大
    edge = np.clip(np.abs(hp).mean(axis=2) / 20.0, 0.0, 1.0)[..., None]
    amount = float(_v(p, "WRAP_DETAIL_USM") or 0) * strength
    return np.clip(x + amount * hp * edge, 0.0, 255.0)


def _label_window(label: np.ndarray, bw: int, bh: int,
                  p: dict[str, Any]) -> tuple[np.ndarray, float]:
    """裁出与衣服宽高比匹配的标签窗口，再等比放大 —— 文字不会被裁掉。"""
    lh0, lw0 = label.shape[:2]
    pad = max(2, int(round(lw0 * _v(p, "WRAP_SIDE_PAD"))))
    row_color = np.median(label, axis=1, keepdims=True)
    label = np.concatenate([np.repeat(row_color, pad, axis=1), label,
                            np.repeat(row_color, pad, axis=1)], axis=1)
    lh, lw = label.shape[:2]
    zoom = float(p.get("zoom", 1) or 1)
    tw = max(8, int(round(bw * _v(p, "WRAP_FILL_W") * zoom)))
    th = max(8, int(round(bh * _v(p, "WRAP_FILL_H") * zoom)))
    win_h = int(np.clip(round(lw * th / tw), 16, lh))
    anchor = _v(p, "WRAP_TEXT_ANCHOR")
    text_pos = _v(p, "WRAP_TEXT_POS")
    y0 = int(np.clip(round(lh * anchor - win_h * text_pos),
                     0, max(0, lh - win_h)))
    window = label[y0:y0 + win_h]
    big = _restore_detail(_upscale(window, (tw, th), p), p)
    # 下摆渐入的整张标签是非等比拉伸的补底，重绘强度减半以免放大噪点
    full = _restore_detail(_upscale(label, (tw, th), p), p, 0.5)
    rows = np.arange(th, dtype=float)[:, None, None]
    blend = np.clip((rows - th * 0.42) / max(1.0, th * 0.42), 0.0, 1.0)
    blend *= _v(p, "WRAP_LOWER_MIX")
    y_word = (lh * anchor - y0) / max(1.0, win_h) * th
    return big * (1.0 - blend) + full * blend, float(y_word)


def _mirror_sides(label: np.ndarray, p: dict[str, Any]) -> np.ndarray:
    """标签左右留白：默认用镜像延展（连续、无逐行横条）。

    旧实现用「本行像素中位色」左右各补 pad 列，行与行独立取值，放大后是
    一条条扫描线式横纹。镜像延展直接把包装自身的图案接续出去，边界处像素
    连续，视觉上不再有「中间清晰、两侧条纹」的割裂感。
    """
    pad = max(2, int(round(label.shape[1] * _v(p, "WRAP_SIDE_PAD"))))
    if _v(p, "WRAP_SIDE_MIRROR"):
        return np.pad(label, ((0, 0), (pad, pad), (0, 0)), mode="reflect")
    row_color = np.median(label, axis=1, keepdims=True)
    return np.concatenate([np.repeat(row_color, pad, axis=1), label,
                           np.repeat(row_color, pad, axis=1)], axis=1)


def _envelope_profile(hard, shape: tuple[int, int],
                      p: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """从衣服硬蒙版求逐行「中心 / 半宽」，供贴图做包络形变。

    没有蒙版（例如脚本单独生成纹理）或蒙版行太少时返回 None，此时退化为
    纯平面连续采样 —— 依然没有任何接缝，只是不跟随衣服轮廓收放。
    """
    if hard is None:
        return None
    H, W = int(shape[0]), int(shape[1])
    m = np.asarray(hard, dtype=bool)
    if m.shape != (H, W):
        m = np.asarray(Image.fromarray((m * 255).astype(np.uint8))
                       .resize((W, H), Image.Resampling.NEAREST)) > 127
    left = np.full(H, np.nan, dtype=float)
    right = np.full(H, np.nan, dtype=float)
    for y in range(H):
        xs = np.flatnonzero(m[y])
        if xs.size >= 3:
            lo, hi = float(np.percentile(xs, 2)), float(np.percentile(xs, 98))
            if hi - lo > 2:
                left[y], right[y] = lo, hi
    valid = np.isfinite(left) & np.isfinite(right)
    if valid.sum() < 8:
        return None
    idx = np.arange(H, dtype=float)
    left = np.interp(idx, idx[valid], left[valid])
    right = np.interp(idx, idx[valid], right[valid])
    sigma = max(2.0, H * float(_v(p, "WRAP_ENVELOPE_SMOOTH") or 0.02))
    left = ndi.gaussian_filter1d(left, sigma)
    right = ndi.gaussian_filter1d(right, sigma)
    return (left + right) * 0.5, np.maximum(4.0, (right - left) * 0.5)


def _warp_offsets(depth: np.ndarray | None, detail: np.ndarray | None,
                  hard: np.ndarray | None, shape: tuple[int, int],
                  bbox, p: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """深度视差场 + 布料明度细节 → 2D 采样坐标偏移 (dy, dx)。

    这是 R3 的核心：**AI 只输出坐标，不输出像素**。深度模型给出胸廓级的大尺度
    起伏，原图明度高频补上布料细节（先削平号码/字母那种强边缘），叠加到旧的
    1D 逐行包络之上，让花纹随身体曲面弯曲。`full` 标签一个像素都不改。

    三道保护：衣服轮廓内淡出（边界不跳变）、主字区降幅（字不被拉歪）、
    单点位移钳制（总幅度有上限）。任何输入缺失都返回 None 走旧路径。
    """
    if depth is None and detail is None:
        return None
    H, W = int(shape[0]), int(shape[1])
    if hard is None:
        return None
    mask = np.asarray(hard, dtype=bool)
    if mask.shape != (H, W) or not mask.any():
        return None
    # 语义分割会把白色号码/字母/描边挖成封闭洞；几何场必须先用填实后的
    # 轮廓，否则 distance_transform_edt 在洞内为 0，会把原字样形状印进 warp 场。
    # 底部补两行前景，让「压到画面下缘的号码」也成为封闭洞（与
    # compose._solid_cover_mask 同一处理）。
    geom = ndi.binary_fill_holes(
        np.vstack([mask, np.ones((2, W), dtype=bool)]))[:H]
    dist_geom = ndi.distance_transform_edt(geom)

    dy = np.zeros((H, W), dtype=float)
    dx = np.zeros((H, W), dtype=float)
    scale = float(_v(p, "WRAP_DEPTH_SCALE") or 0)
    if depth is not None and scale > 0:
        d = np.asarray(depth, dtype=float)
        if d.shape != (H, W):
            d = ndi.zoom(d, (H / d.shape[0], W / d.shape[1]), order=1)
        # 用「大尺度形体」而不是「局部残差」：单目深度模型会把球衣印花文字
        # 误判成表面凹凸，做高通会把 LAKERS/24 的形状印进 warp 场。
        # 只保留低通趋势（胸口整体比肩部更近）就既符合视差物理、又完全干净。
        sigma = max(4.0, min(H, W) * float(_v(p, "WRAP_DEPTH_SMOOTH")))
        shape = ndi.gaussian_filter(d, sigma)
        lo = float(np.percentile(shape[geom], 5))
        hi = float(np.percentile(shape[geom], 95))
        field = np.clip((shape - (lo + hi) / 2.0)
                        / max(1e-6, (hi - lo) / 2.0), -1.0, 1.0)
        dy -= scale * field                             # 视差：近处采样点上移
        gx = ndi.gaussian_filter(np.gradient(field, axis=1), 1.2)
        dx += scale * 0.5 * np.clip(gx, -1.0, 1.0)     # 曲面法线的横向分量

    dscale = float(_v(p, "WRAP_DEPTH_DETAIL_SCALE") or 0)
    if detail is not None and dscale > 0:
        t = np.asarray(detail, dtype=float)
        if t.shape != (H, W):
            t = ndi.zoom(t, (H / t.shape[0], W / t.shape[1]), order=1)
        dy += dscale * np.clip(t, -1.0, 1.0)
        gx = ndi.gaussian_filter(np.gradient(t, axis=1), 0.8)
        dx += dscale * 0.45 * np.clip(gx * 3.0, -1.0, 1.0)

    # 柱面/球面分量：完全由衣服轮廓的距离场构造（确定性、绝不含原图字样）。
    # 胸口中央离边界最远，采样点略下移；肩部/袖口边缘略上移，
    # 形成绕身体的弧线，让平面标签看上去是穿在立体躯干上。
    cyl = float(_v(p, "WRAP_DEPTH_CYL_SCALE") or 0)
    if cyl > 0:
        dist = ndi.gaussian_filter(dist_geom, max(1.0, min(H, W) * 0.015))
        r = float(np.percentile(dist[geom], 90))
        dn = np.clip(dist / max(1e-6, r), 0.0, 1.0)
        dy += cyl * (0.5 - dn)

    # 主字区保护：以 word_y 为中心、bbox 中部为范围做高斯降幅
    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    bw, bh = max(8, bx2 - bx1), max(8, by2 - by1)
    cx = (bx1 + bx2) / 2.0
    word_y = float(p.get("word_y", _v(p, "WRAP_TEXT_POS")) or 0)
    yc = by1 + bh * word_y
    yy = np.arange(H, dtype=float)[:, None]
    xx = np.arange(W, dtype=float)[None, :]
    g = (np.exp(-((yy - yc) / (bh * 0.18)) ** 2)
         * np.exp(-((xx - cx) / (bw * 0.38)) ** 2))
    keep = float(_v(p, "WRAP_DEPTH_TEXT_PROTECT"))
    protect = 1.0 - (1.0 - keep) * g
    dy *= protect
    dx *= protect

    # 轮廓边界淡出 + 总位移钳制
    fade = ndi.gaussian_filter(
        np.clip(dist_geom / max(1.0, float(_v(p, "WRAP_DEPTH_EDGE_FADE"))),
                0.0, 1.0), 0.6)
    dy *= fade
    dx *= fade
    mag = np.hypot(dy, dx)
    limit = float(_v(p, "WRAP_DEPTH_MAX_SHIFT"))
    k = np.minimum(1.0, limit / np.maximum(mag, 1e-6))
    return dy * k, dx * k


def _continuous_texture(label: np.ndarray, shape: tuple[int, int], bbox,
                        p: dict[str, Any], hard, depth=None,
                        detail=None) -> tuple[np.ndarray, tuple[float, float]]:
    """整张画布从同一张等比放大的标签连续采样 —— 取消「模糊底 + 清晰矩形」。

    几何（tw / th / win_h / y0 / y_word / word_y）与 _label_window 完全一致，
    所以 55 个品牌逐个调好的主字位置、缩放和取景都不变。区别只有两点：

    1. 不再先把标签高斯模糊成渐变底、再把清晰窗口硬贴上去；整张画布都由
       同一个 full（一次等比放大 + 一次笔画重绘）采样得到，清晰度处处一致。
       采样坐标超出标签时用 nearest 把边缘行/列平推出去：函数值连续，标签
       边界处行间跳变实测 0.03（旧版 22.2），也不会像镜像反射那样折出倒字。
    2. 横向坐标按衣服逐行包络做轻度形变：肩线/V 领收窄处把花纹一起收进去，
       而不是让一个轴对齐矩形横穿衣服。
    """
    label = _mirror_sides(label, p)
    lh, lw = label.shape[:2]
    H, W = int(shape[0]), int(shape[1])
    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    bw, bh = max(8, bx2 - bx1), max(8, by2 - by1)
    zoom = float(p.get("zoom", 1) or 1)
    tw = max(8, int(round(bw * _v(p, "WRAP_FILL_W") * zoom)))
    th = max(8, int(round(bh * _v(p, "WRAP_FILL_H") * zoom)))
    scale = tw / float(lw)
    full = _restore_detail(_upscale(label, (tw, max(8, int(round(lh * scale)))), p), p)

    win_h = int(np.clip(round(lw * th / tw), 16, lh))
    anchor, text_pos = _v(p, "WRAP_TEXT_ANCHOR"), _v(p, "WRAP_TEXT_POS")
    y0 = int(np.clip(round(lh * anchor - win_h * text_pos), 0, max(0, lh - win_h)))
    y_word = (lh * anchor - y0) / max(1.0, win_h) * th
    cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
    left = int(round(cx - tw / 2))
    word_y = float(p.get("word_y", text_pos) or 0)
    top = int(round(by1 + bh * word_y - y_word))

    yy = np.arange(H, dtype=float)[:, None]
    xx = np.arange(W, dtype=float)[None, :]
    sy = np.broadcast_to(yy - top + y0 * scale, (H, W))
    env = _envelope_profile(hard, (H, W), p)
    strength = float(_v(p, "WRAP_ENVELOPE") or 0)
    if env is None or strength <= 0:
        sx = np.broadcast_to(xx - left, (H, W))
    else:
        center, half = env
        rng = max(0.01, float(_v(p, "WRAP_ENVELOPE_RANGE") or 0.28))
        ratio = np.clip((bw * 0.5) / half, 1.0 / (1.0 + rng), 1.0 + rng)
        factor = 1.0 + strength * (ratio - 1.0)
        center_eff = cx + strength * (center - cx)
        sx = tw / 2.0 + (xx - center_eff[:, None]) * factor[:, None]
    warp = _warp_offsets(depth, detail, hard, (H, W), bbox, p)
    if warp is not None:
        sy = sy + warp[0]
        sx = sx + warp[1]
    coords = np.stack([sy, sx])
    order = int(_v(p, "WRAP_ENV_ORDER") or 3)
    mode = str(_v(p, "WRAP_EXTEND_MODE") or "nearest")
    if mode not in ("nearest", "reflect", "mirror", "grid-wrap", "wrap"):
        mode = "nearest"
    canvas = np.stack([ndi.map_coordinates(full[..., c], coords, order=order,
                                           mode=mode) for c in range(3)], axis=-1)
    return np.clip(canvas, 0.0, 255.0), (cx, cy), warp is not None


def _crisp(label: np.ndarray, p: dict[str, Any],
           brand_hex: str | None) -> np.ndarray:
    """把低分辨率包装字「重新描清楚」。

    有些源图只有 2KB、主字每字 10~15px，多级 Lanczos 放大 + 高斯补底后
    字边缘会糊成一团。这里只对**高饱和的彩色笔画**（红字、蓝标、logo）
    做颜色归位：按与品牌主色的色相距离生成软掩码，把笔画推回饱和的纯色，
    笔画之间的灰白过渡推回白色，最后再做一次细半径 unsharp。

    只影响配置了 crisp 的品牌；低饱和的奶白/透明瓶身不会被染成色块。
    """
    amount = float(p.get("crisp", 0) or 0)
    if amount <= 0:
        return label
    a = np.clip(label, 0.0, 255.0)
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    # 笔画掩码：饱和 + 与白色有明显距离
    m = np.clip((sat - 0.22) / 0.30, 0.0, 1.0) * np.clip((235.0 - a.mean(axis=2)) / 90.0, 0.0, 1.0)
    # 颜色归位：保持色相，把笔画的饱和度与明度推到端点
    gray = a.mean(axis=2, keepdims=True)
    target = gray + (a - gray) * 1.45
    target = np.clip(target, 0.0, 255.0)
    if brand_hex and p.get("crisp_hex"):
        try:
            base = _hex_rgb(str(p["crisp_hex"]))
            hue = a / np.maximum(mx[..., None], 1.0)
            target = np.clip(base * (0.55 + 0.45 * hue), 0.0, 255.0)
        except ValueError:
            pass
    w = (m * amount)[..., None]
    out = a * (1.0 - w) + target * w
    # 笔画内部与边缘再做一次细锐化，抵消前面多级重采样带来的糊
    sig = float(p.get("crisp_sigma", 0.65) or 0.65)
    blur = ndi.gaussian_filter(out, (sig, sig, 0))
    return np.clip(out + float(p.get("crisp_sharp", 0.85)) * (out - blur), 0.0, 255.0)


def _curve(arr: np.ndarray, cx: float, cy: float,
           p: dict[str, Any]) -> np.ndarray:
    """轻微胸廓形变：中间略放宽、中央微微上拱，文字不再像平贴的矩形。"""
    H, W = arr.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    u = np.clip((xx - cx) / max(1.0, W / 2), -1.0, 1.0)
    v = np.clip((yy - cy) / max(1.0, H / 2), -1.0, 1.0)
    sx = cx + (xx - cx) * (1.0 - _v(p, "WRAP_CURVE") * (1.0 - u ** 2))
    sy = cy + (yy - cy) - _v(p, "WRAP_ARC") * H * (1.0 - u ** 2) * (1.0 - v ** 2)
    coords = np.stack([sy, sx])
    # 三次插值比双线性少一次低通，曲面形变后笔画不会又软一层。
    # 逐品牌仍可用 curve_order 覆盖（_v 会优先读 yaml 里的同名键）。
    order = int(_v(p, "WRAP_CURVE_ORDER") or 3)
    return np.stack([ndi.map_coordinates(arr[..., c], coords, order=order,
                                         mode="nearest") for c in range(3)], axis=-1)


def build_texture(key: str, shape: tuple[int, int], bbox,
                  cloth_mask=None, depth=None, detail=None) -> dict[str, Any]:
    """生成与人像同尺寸的包装纹理画布。

    返回 {"texture": float ndarray|None, "source": str|None, "reason": str|None,
          "continuous": bool, "depth_warp": bool}
    reason 非空表示应回退到旧的平涂印花风格。
    cloth_mask 是衣服硬蒙版（可选），用于连续纹理的逐行包络形变；
    depth/detail 是 R3 的 AI 视差场与布料细节场，只改采样坐标、不改像素。
    """
    out: dict[str, Any] = {"texture": None, "source": None, "reason": None,
                           "depth_warp": False}
    p = _overrides().get(str(key or ""), {})
    # 逐品牌可设 depth_warp: 0 单独回退；全局开关 WRAP_DEPTH_WARP 同理
    if not bool(p.get("depth_warp", config.WRAP_DEPTH_WARP)):
        depth = detail = None
    path = brand_image_path(str(key or ""))
    if path is None:
        out["reason"] = "品牌没有可用包装图"
        return out
    try:
        with Image.open(path) as src:
            src.load()
            arr = np.asarray(src.convert("RGB"), dtype=float)
    except (OSError, ValueError) as exc:
        out["reason"] = f"包装图无法读取：{type(exc).__name__}"
        return out
    try:
        arr = _crop_rotate(arr, p)
    except (ValueError, TypeError) as exc:
        out["reason"] = f"逐品牌裁剪参数有误：{exc}"
        return out
    if min(arr.shape[:2]) < 40:  # 极端小图放大也救不回来
        out["reason"] = f"包装图太小（{arr.shape[1]}x{arr.shape[0]}）"
        return out
    # 聚焦裁剪后很多源图只有 60~120px 宽；先等比放大再走后续像素级处理，
    # 否则会撞上 WRAP_MIN_SHORT_EDGE 直接回退成平涂风格。
    h, w = arr.shape[:2]
    if min(h, w) < 160:
        scale = 160.0 / min(h, w)
        arr = _upscale(arr, (max(8, round(w * scale)),
                             max(8, round(h * scale))), p)
    if min(arr.shape[:2]) < _v(p, "WRAP_MIN_SHORT_EDGE"):
        out["reason"] = f"包装图太小（{arr.shape[1]}x{arr.shape[0]}）"
        return out

    bg, fg = _background(arr, p)
    if fg.mean() < _v(p, "WRAP_MIN_FOREGROUND"):
        out["reason"] = "包装图几乎没有彩色主体"
        return out
    filled = _fill_background(arr, bg, fg, p, _brand_hex(key))
    label = _label_crop(filled, fg, p)
    if label is None:
        out["reason"] = "无法从包装图裁出标签主视觉"
        return out
    label = _trim_light_edges(label, p)
    label = _focus(label, p)
    label = _enhance(label, p, _brand_hex(key))
    label = _crisp(label, p, _brand_hex(key))

    H, W = int(shape[0]), int(shape[1])
    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    bw, bh = max(8, bx2 - bx1), max(8, by2 - by1)
    continuous = bool(p.get("continuous", config.WRAP_CONTINUOUS))
    if continuous:
        canvas, (cx, cy), warped = _continuous_texture(
            label, (H, W), bbox, p, cloth_mask, depth, detail)
        out["continuous"] = True
        out["depth_warp"] = warped
    else:
        # 旧路径（逐品牌可用 continuous: 0 回退）：模糊渐变底 + 胸口清晰矩形
        big, y_word = _label_window(label, bw, bh, p)
        nh, nw = big.shape[:2]
        base = np.asarray(
            Image.fromarray(np.rint(np.clip(label, 0, 255)).astype(np.uint8))
            .resize((W, H), Image.Resampling.BILINEAR), dtype=float
        )
        sigma = max(6.0, min(H, W) * 0.06)
        canvas = ndi.gaussian_filter(base, (sigma, sigma, 0))
        cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
        left = int(round(cx - nw / 2))
        word_y = float(p.get("word_y", _v(p, "WRAP_TEXT_POS")) or 0)
        top = int(round(by1 + bh * word_y - y_word))
        sx1, sy1, sx2, sy2 = (max(0, -left), max(0, -top),
                              min(nw, W - left), min(nh, H - top))
        dx1, dy1 = left + sx1, top + sy1
        if sx2 > sx1 and sy2 > sy1:
            canvas[dy1:dy1 + (sy2 - sy1), dx1:dx1 + (sx2 - sx1)] = big[sy1:sy2, sx1:sx2]
        out["continuous"] = False

    canvas = _curve(canvas, cx, cy, p)
    # 不使用任何程序绘制的品牌字：衣服上只出现包装图自带的艺术字与图案。
    sharp = _v(p, "WRAP_SHARP")
    if sharp:
        sig = _v(p, "WRAP_SHARP_SIGMA")
        blur = ndi.gaussian_filter(canvas, (sig, sig, 0))
        canvas = np.clip(canvas + sharp * (canvas - blur), 0.0, 255.0)
    out.update(texture=canvas, source=str(path))
    return out
