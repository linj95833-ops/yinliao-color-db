"""人像衣服换色 + 品牌印花，生成一张新的娱乐合成图。

三种风格（零新增依赖，numpy / Pillow / scipy / sklearn）：
wrap（默认，像把饮料包装真的穿上身）
1. cloth.segment() 分割真实衣服轮廓；
2. wrap.build_texture() 从品牌产品图里裁出标签主视觉、补掉白底、等比放大并
   轻微曲面化，得到一整幅包装纹理；
3. 用原衣服的**大尺度明度场**调制纹理亮度（肩部高光、腋下阴影、褶皱），
   再按软边 mask 一次性覆盖 —— 原号码 / 字母 / 描边彻底消失，包装上的
   中文英文艺术字与图案就是印花，不需要程序画字。
garment（基准衣服迁移）
1. 从已换装的基准成品图离线切出精确衣服条带与逐行轮廓（data/garments/）；
2. garment.build_texture() 把源轮廓非刚性映射到目标衣服轮廓，图案随肩线、
   领口与躯干弧度一起变形，且保留基准图自带的真实褶皱与单个居中 logo；
3. 后续光照调制 / 覆盖 / 羽化与 wrap 完全共用。资产缺失时自动回退 wrap。
print（旧版）
1. 衣服整体换成品牌主色（抑制印花亮度痕迹，无幽灵号码）；
2. 画中英文字样、类别元素、徽章、色板与产品贴片，用 mask 裁切。

包装图缺失 / 太小 / 无彩色主体时，wrap 自动回退 print 并写明原因。

合成图含使用者上传的照片，属本地私有文件，不随仓库发布。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

from . import cloth, color, config, depth, garment, portrait, wrap

_CATEGORY = {
    "carbonated": ("SPARKLING", "气泡畅爽", "bubble"),
    "juice": ("FRUIT POWER", "鲜果活力", "fruit"),
    "water": ("PURE WATER", "纯净补给", "drop"),
    "tea": ("TEA TIME", "茶香一刻", "leaf"),
    "coffee": ("COFFEE BOOST", "醒神一刻", "bean"),
    "energy": ("ENERGY ON", "能量满格", "bolt"),
    "sports": ("HYDRATION", "运动补水", "bolt"),
    "dairy": ("SMOOTH MILK", "丝滑奶香", "drop"),
    "soy": ("SOY GOOD", "豆香本味", "bean"),
    "herbal": ("HERBAL COOL", "草本清爽", "leaf"),
}
_DEFAULT_CATEGORY = ("COLOR MATCH", "官方配色", "bubble")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in config.COMPOSE_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10
    except TypeError:
        return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: float, start: int,
              min_size: int = 11):
    size = max(min_size, int(start))
    font = _font(size)
    while size > min_size:
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_w:
            return font, size
        size = max(min_size, size - max(1, size // 10))
        font = _font(size)
    return font, size


def _shade(hex_value: str, factor: float) -> tuple[int, int, int]:
    s = str(hex_value).lstrip("#")
    if len(s) != 6:
        return (30, 30, 30)
    try:
        rgb = [int(s[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return (30, 30, 30)
    return tuple(int(max(0, min(255, v * factor))) for v in rgb)


def _data_url(img: Image.Image, fmt: str = "JPEG", max_side: int = 1000,
              quality: int = 88) -> str:
    im = img.convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                       Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if fmt == "PNG":
        im.save(buf, "PNG")
        mime = "png"
    else:
        im.save(buf, "JPEG", quality=quality)
        mime = "jpeg"
    import base64

    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _prepare(photo, query: dict[str, Any]) -> Image.Image:
    """与 portrait.analyze 使用同一尺寸，保证 mask / 装饰框坐标对齐。"""
    im = portrait.load_image(photo)
    target = query.get("image_size")
    if target and len(target) == 2 and list(im.size) != [int(target[0]), int(target[1])]:
        im = im.resize((int(target[0]), int(target[1])), Image.Resampling.LANCZOS)
    return im


def _ensure_resolution(im: Image.Image) -> tuple[Image.Image, float]:
    """小尺寸输入自动高清化：输出短边拉到 config.COMPOSE_MIN_SHORT_EDGE。

    171x234 这类测试图里衣服只有 119x148 px，包装文字只有十几像素高，
    无论贴图算法多好，放大到网页上都会糊。把整幅图先高质量放大再跑
    分割 / 贴图 / 光照，衣服框同步变大，包装字才有足够的像素承载笔画。
    人脸与背景用边缘掩码引导的轻锐化，避免把皮肤噪点一起放大。
    """
    target = int(getattr(config, "COMPOSE_MIN_SHORT_EDGE", 0) or 0)
    short = min(im.size)
    if target <= 0 or short >= target:
        return im, 1.0
    # cloth.load_image 会把最长边压到 PORTRAIT_MAX_SIDE。极窄长图（外星人
    # 电解质水 88x240）按短边 480 放大后长边会到 1309，导致 seg 蒙版是
    # 1200x440、原图是 1309x480，后续 _shading_field 直接广播报错。
    # 先把短边目标按长边上限收一下，让两处尺寸始终一致。
    cap = int(getattr(config, "PORTRAIT_MAX_SIDE", 1200) or 0)
    if cap > 0:
        long_side = max(im.size)
        target = max(short + 1, min(target, int(cap * short / long_side)))
    scale = target / float(short)
    big = im.resize((max(1, round(im.width * scale)),
                     max(1, round(im.height * scale))),
                    Image.Resampling.LANCZOS)
    amount = float(getattr(config, "COMPOSE_UPSCALE_SHARP", 0) or 0)
    if amount > 0:
        a = np.asarray(big, dtype=float)
        hp = a - ndi.gaussian_filter(a, (0.7, 0.7, 0))
        edge = np.clip(np.abs(hp).mean(axis=2) / 12.0, 0.0, 1.0)[..., None]
        big = Image.fromarray(
            np.rint(np.clip(a + amount * hp * edge, 0, 255)).astype(np.uint8))
    return big, scale


def _pairs(query: dict[str, Any], brand: dict[str, Any]) -> list[dict[str, Any]]:
    """衣服色 → 品牌包装色的逐色配对明细，仅用于结果展示。"""
    hit = {}
    for p in brand.get("color_matches") or []:
        qh = str(p.get("query_hex", "")).upper()
        bh = str(p.get("brand_hex", "")).upper()
        if qh and bh:
            hit[qh] = bh
    fallback = str(brand.get("brand_hex") or "#C62828").upper()
    pal = list(query.get("palette") or [])[:config.COMPOSE_QUERY_COLORS]
    if not pal and query.get("dominant_hex"):
        pal = [{"hex": query["dominant_hex"], "ratio": 1.0}]
    out = []
    for c in pal:
        qh = str(c.get("hex") or "").upper()
        out.append({"cloth": qh, "brand": hit.get(qh, fallback)})
    return out or [{"cloth": str(query.get("dominant_hex") or "#888888"),
                    "brand": fallback}]


def _recolor(arr: np.ndarray, mask: np.ndarray, main_lab: np.ndarray,
             cloth_lab: np.ndarray, strength: float) -> np.ndarray:
    """整件衣服换成品牌主色；平滑明度抹掉原号码/字样，保留布料褶皱。"""
    lab = color.rgb_to_lab(arr)
    L = lab[..., 0]
    sigma = max(2.0, min(arr.shape[:2]) * config.COMPOSE_L_SMOOTH_FRAC)
    # 印花/号码与布料色调明显不同：它们既不该贡献局部明度，也不该保留自己的亮度，
    # 否则换色后会留下"幽灵数字"。局部明度只由真正的布料像素插值得到。
    chroma = np.sqrt((lab[..., 1] - cloth_lab[1]) ** 2
                     + (lab[..., 2] - cloth_lab[2]) ** 2)
    ink = chroma > config.COMPOSE_INK_CHROMA
    cloth_only = mask * np.where(ink, 0.0, 1.0)
    weight = ndi.gaussian_filter(cloth_only, sigma)
    local = ndi.gaussian_filter(L * cloth_only, sigma) / np.maximum(weight, 1e-6)
    local = np.where(weight > 0.01, local, main_lab[0])

    detail = np.where(ink, 0.0, (L - local) * config.COMPOSE_L_CONTRAST)
    target_L = np.clip(main_lab[0] + detail, 8.0, 98.0)
    target = np.stack([target_L,
                       np.full_like(L, main_lab[1]),
                       np.full_like(L, main_lab[2])], axis=-1)
    rgb = color.lab_to_rgb_array(target).astype(float)
    m = np.clip(mask * strength, 0.0, 1.0)[..., None]
    return arr * (1.0 - m) + rgb * m


def _solid_cover_mask(mask: np.ndarray, hard: np.ndarray,
                      protect: np.ndarray | None = None) -> np.ndarray:
    """把衣服轮廓内部的封闭洞补成「完全覆盖」，消掉原 LAKERS / 24 字样的淡影。

    分割时语义模型会把白色号码、字母与深色描边判成 Background / 非上衣，
    后续减去背景/皮肤的减法会在衣服中间挖出洞；软边羽化又让洞缘 alpha
    落在 0~1，于是原字样以半透明形式混进包装贴图，表现为白字里的黄色碎斑
    与斜线。V 领、袖口、手臂和背景都是与画面外部连通的开放缺口，
    binary_fill_holes 不会填它们，所以脸、脖子、皮肤保持原样。
    底部补两行白是为了让「压到画面下缘的号码」也成为封闭洞。
    但这招有副作用：手臂与躯干之间那条贯穿到底边的背景缝，也会被补底封口后
    整条填满，换装出来手臂直接粘在躯干上。所以补底只允许填「非 protect」
    的部分 —— protect 是分割阶段算出的背景缝掩版。
    外轮廓另用有符号距离场做 1~2px 的亚像素 alpha 过渡：内部彻底覆盖，
    边缘按真实覆盖率渐变，避免硬二值切边把轮廓的像素阶梯直接印到图上。
    """
    if not getattr(config, "COMPOSE_FILL_CLOTH_HOLES", True):
        return mask
    h = np.asarray(hard, dtype=bool)
    W = h.shape[1]
    closed = ndi.binary_fill_holes(h)
    filled = ndi.binary_fill_holes(
        np.vstack([closed, np.ones((2, W), dtype=bool)]))[:h.shape[0]]
    if protect is not None and np.shape(protect) == h.shape:
        # 缝隙可能在上半段被两条手臂封成一个「封闭洞」，fill_holes 照样会填，
        # 所以不能只拦补底那一档，必须整体扣掉。
        p = np.asarray(protect, dtype=bool)
        filled = filled & ~p
        closed = closed & ~p
    holes = filled & ~h
    feather = float(getattr(config, "COMPOSE_EDGE_FEATHER", 1.2) or 0)
    protect_soft = None
    if protect is not None and np.shape(protect) == h.shape:
        # 缝隙边界是逐像素挖的，直接当硬掩版用会在手臂内侧留下阶梯；这里给
        # 它 1px 左右的亚像素过渡，和轮廓边缘的羽化保持同一套观感。
        p32 = np.asarray(protect, dtype=np.float32)
        protect_soft = np.clip(
            ndi.gaussian_filter(p32, max(0.9, feather * 0.9)) * 2.0, 0.0, 1.0)
    if feather <= 0:
        out = np.maximum(np.asarray(mask, dtype=float), filled.astype(float))
        return out * (1.0 - protect_soft) if protect_soft is not None else out
    din = ndi.distance_transform_edt(filled)
    dout = ndi.distance_transform_edt(~filled)
    alpha = np.clip(0.5 + (din - dout) / (2.0 * feather), 0.0, 1.0)
    out = np.maximum(alpha, holes.astype(float))
    return out * (1.0 - protect_soft) if protect_soft is not None else out


def _shading_field(arr: np.ndarray, mask: np.ndarray, hard: np.ndarray,
                   cloth_lab: np.ndarray) -> np.ndarray:
    """从原衣服提取「大尺度光照场」，让包装贴图有肩部高光、腋下阴影与褶皱。

    只保留低频明度：原号码 / 字母 / 描边属于高频且与布料色调不同，先用 ink
    判据剔除，再做大半径平滑，所以贴图不会把 LAKERS/24 的幽灵轮廓带回来。
    """
    lab = color.rgb_to_lab(arr)
    L = lab[..., 0]
    chroma = np.sqrt((lab[..., 1] - cloth_lab[1]) ** 2
                     + (lab[..., 2] - cloth_lab[2]) ** 2)
    ink = chroma > config.COMPOSE_INK_CHROMA
    cloth_only = mask * np.where(ink, 0.0, 1.0)
    sigma = max(3.0, min(arr.shape[:2]) * config.WRAP_L_SMOOTH_FRAC)
    weight = ndi.gaussian_filter(cloth_only, sigma)
    local = ndi.gaussian_filter(L * cloth_only, sigma) / np.maximum(weight, 1e-6)
    mean_L = float(np.median(L[hard])) if hard.any() else float(np.median(L))
    local = np.where(weight > 0.01, local, mean_L)
    ratio = local / max(mean_L, 1e-3)
    shade = 1.0 + (ratio - 1.0) * config.WRAP_SHADE_GAIN
    return np.clip(shade, config.WRAP_SHADE_MIN, config.WRAP_SHADE_MAX)


def _element(d: ImageDraw.ImageDraw, kind: str, x: float, y: float, r: float,
             accent: tuple[int, int, int], phase: float) -> None:
    """画一个类别相关的小元素；坐标均为画布像素。"""
    white = (255, 255, 255, 165)
    faint = (255, 255, 255, 55)
    line = max(1, int(r * 0.16))
    if kind == "bubble":
        d.ellipse([x - r, y - r, x + r, y + r], outline=white, width=line)
        d.ellipse([x - r * 0.55, y - r * 0.6, x - r * 0.1, y - r * 0.15], fill=faint)
    elif kind == "drop":
        d.polygon([(x, y - r * 1.35), (x + r * 0.85, y + r * 0.15),
                   (x, y + r), (x - r * 0.85, y + r * 0.15)],
                  fill=faint, outline=white)
        d.ellipse([x - r * 0.28, y + r * 0.1, x + r * 0.05, y + r * 0.5], fill=faint)
    elif kind == "leaf":
        d.polygon([(x - r, y + r * 0.7), (x - r * 0.2, y - r),
                   (x + r, y - r * 0.55), (x + r * 0.75, y + r * 0.65)], fill=faint)
        d.line([(x - r * 0.7, y + r * 0.5), (x + r * 0.55, y - r * 0.35)],
               fill=white, width=line)
    elif kind == "bean":
        d.ellipse([x - r, y - r * 0.7, x + r, y + r * 0.7], fill=faint, outline=white)
        d.arc([x - r * 0.1, y - r * 0.7, x + r * 0.9, y + r * 0.7],
              40, 220, fill=white, width=line)
    elif kind == "bolt":
        d.polygon([(x + r * 0.25, y - r), (x - r * 0.65, y + r * 0.1),
                   (x - r * 0.05, y + r * 0.1), (x - r * 0.35, y + r),
                   (x + r * 0.7, y - r * 0.2), (x, y - r * 0.2)],
                  fill=faint, outline=white)
    elif kind == "fruit":
        d.ellipse([x - r, y - r, x + r, y + r], fill=faint, outline=white)
        d.polygon([(x, y - r), (x + r * 0.75, y - r * 1.25),
                   (x + r * 0.95, y - r * 0.75)], fill=(*accent, 150))
    else:
        d.ellipse([x - r * 0.5, y - r * 0.5, x + r * 0.5, y + r * 0.5], fill=faint)
    if phase > 0.72:  # 零星光点，让印花不那么规整
        d.ellipse([x + r * 1.4, y - r * 1.2, x + r * 1.7, y - r * 0.9], fill=white)


def _brand_image_path(key: str) -> Path | None:
    return wrap.brand_image_path(key)


def _sticker(overlay: Image.Image, path: Path, safe_box, size: int,
             hard: np.ndarray) -> None:
    """把包装图做成圆形贴片，位置选在衣服覆盖率最高的候选点。"""
    x1, y1, x2, y2 = safe_box
    w, h = x2 - x1, y2 - y1
    H, W = hard.shape
    with Image.open(path) as src:
        im = color.to_rgb(src)
    side = min(im.size)
    im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                  (im.width + side) // 2, (im.height + side) // 2))
    im = im.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    ring = max(2, size // 28)
    # 产品图常是白底：把近白像素变透明，贴片才不会变成一个白圆盘
    px = np.asarray(im).copy()
    rgb = px[..., :3].astype(float)
    near_white = ((rgb.min(axis=2) > 228)
                  & ((rgb.max(axis=2) - rgb.min(axis=2)) < 22))
    px[..., 3] = np.where(near_white, 0, 255).astype(np.uint8)
    circle = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle).ellipse([ring, ring, size - ring, size - ring], fill=255)
    px[..., 3] = (px[..., 3].astype(float)
                  * (np.asarray(circle, dtype=float) / 255.0)).astype(np.uint8)
    im = Image.fromarray(px, "RGBA")

    # 贴片固定放在顶部两侧，尺寸按左上角定位，避免压住中央品牌字样
    candidates = [(0.72, 0.02), (0.08, 0.02), (0.72, 0.12), (0.08, 0.12)]
    best, best_score = None, -1.0
    for fx, fy in candidates:
        px = int(np.clip(x1 + w * fx - size / 2, 0, max(0, W - size - 1)))
        py = int(np.clip(y1 + h * fy, 0, max(0, H - size - 1)))
        score = float(hard[py:py + size, px:px + size].mean())
        if score > best_score:
            best, best_score = (px, py), score
    px, py = best
    d = ImageDraw.Draw(overlay)
    d.ellipse([px, py, px + size, py + size],
              outline=(255, 255, 255, 190), width=ring)
    overlay.paste(im, (px, py), im.getchannel("A"))


def _decorate(overlay: Image.Image, safe_box, brand: dict[str, Any],
              query: dict[str, Any], sticker: bool, hard: np.ndarray) -> None:
    d = ImageDraw.Draw(overlay)
    x1, y1, x2, y2 = safe_box
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    cx, cy = (x1 + x2) / 2, y1 + h * 0.46
    main_hex = str(brand.get("brand_hex") or "#C62828").upper()
    vector = brand.get("color_vector") or []
    accent = _shade(str(vector[1].get("hex", main_hex)) if len(vector) > 1 else main_hex, 1.15)
    dark = _shade(main_hex, 0.42)
    white = (255, 255, 255, 245)
    ink = (*dark, 205)

    eng, cn_tag, kind = _CATEGORY.get(str(brand.get("category") or ""), _DEFAULT_CATEGORY)
    rng = np.random.default_rng(config.COLOR_SEED + sum(ord(c) for c in str(brand.get("key", ""))))
    for _ in range(26):
        x = x1 + float(rng.uniform(0.05, 0.95)) * w
        y = y1 + float(rng.uniform(0.04, 0.96)) * h
        # 中央竖列留给品牌名/英文/标语/色板，元素只画到两侧
        if abs(x - cx) < w * 0.40 and y1 + h * 0.30 < y < y1 + h * 0.96:
            continue
        r = float(rng.uniform(0.012, 0.030)) * min(w, h)
        _element(d, kind, x, y, r, accent, float(rng.uniform(0, 1)))

    # 圆形徽章放左上，产品贴片放右上，中间留给品牌名，避免互相压字
    badge_r = max(11, int(min(w, h) * 0.085))
    bx, by = x1 + w * 0.15, y1 + h * 0.13
    d.ellipse([bx - badge_r, by - badge_r, bx + badge_r, by + badge_r],
              fill=(*dark, 190), outline=white, width=max(2, badge_r // 14))
    d.ellipse([bx - badge_r * 0.84, by - badge_r * 0.84,
               bx + badge_r * 0.84, by + badge_r * 0.84],
              outline=(255, 255, 255, 135), width=max(1, badge_r // 26))
    if config.COMPOSE_PRINT_TEXT:  # 默认关闭：衣服上不出现程序打印的品牌字
        badge_char = (str(brand.get("name_cn") or "饮") or "饮")[0]
        bf, _ = _fit_font(d, badge_char, badge_r * 1.3, int(badge_r * 1.25), 9)
        d.text((bx, by), badge_char, font=bf, fill=white, anchor="mm")

    # 品牌字样：中文名 / 英文名 / 类别标语 + 联合色差，按可用高度分层排布
    # COMPOSE_PRINT_TEXT=False 时整段跳过，只留装饰元素、徽章图形、色板与贴片。
    if config.COMPOSE_PRINT_TEXT:
        cn = str(brand.get("name_cn") or brand.get("key") or "")
        font_cn, size_cn = _fit_font(d, cn, w * 0.84, int(w * 0.20), 11)
        cn_y = y1 + h * 0.42
        d.text((cx, cn_y), cn, font=font_cn, fill=white, anchor="mm",
               stroke_width=max(2, size_cn // 16), stroke_fill=ink)
        font_en, size_en = _fit_font(d, eng, w * 0.76, int(max(11, w * 0.095)), 9)
        en_y = y1 + h * 0.60
        d.text((cx, en_y), eng, font=font_en, fill=white, anchor="mm",
               stroke_width=max(1, size_en // 18), stroke_fill=ink)
        # 标语只放品牌相关内容；ΔE 是匹配指标，留在报告与接口数据里，不印到衣服上
        tag = f"{cn_tag} · {str(brand.get('name_en') or '')}"
        font_tag, size_tag = _fit_font(d, tag, w * 0.86, int(max(11, w * 0.080)), 9)
        tag_y = y1 + h * 0.735
        d.text((cx, tag_y), tag, font=font_tag, fill=(255, 255, 255, 220), anchor="mm",
               stroke_width=max(1, size_tag // 18), stroke_fill=ink)

    # 色板放在最后一行，避免压住标语
    swatches = [str(brand.get("brand_hex") or main_hex)]
    swatches += [str(c.get("hex")) for c in vector[1:4] if c.get("hex")]
    sw = max(6, int(w * 0.045))
    gap = max(2, sw // 5)
    total = len(swatches) * sw + (len(swatches) - 1) * gap
    sx, sy = cx - total / 2, y1 + h * 0.87
    for i, hx in enumerate(swatches):
        left = sx + i * (sw + gap)
        d.rectangle([left, sy, left + sw, sy + sw],
                    fill=(*_shade(hx, 1.0), 235), outline=white, width=1)

    if sticker:
        path = _brand_image_path(str(brand.get("key") or ""))
        if path:
            s = max(40, min(190, int(w * 0.20)))
            _sticker(overlay, path, safe_box, min(s, int(h * 0.26)), hard)


def compose(photo, query: dict[str, Any], brand: dict[str, Any], out=None,
            strength: float | None = None, sticker: bool | None = None,
            save: bool = True, with_download: bool = False,
            style: str | None = None, badge: bool | None = None,
            garment_id: str | None = None) -> dict[str, Any]:
    """生成合成图。返回 image / path / preview / download / 生成参数。

    style=wrap     把品牌包装图贴到衣服上（包装自带艺术字/图案 + 原图光照）；
    style=garment  把基准成品图切出的衣服按目标轮廓变形后贴上去（需 garment_id）；
    style=print    旧的平涂主色 + 程序印花。
    包装图 / 基准资产不可用时逐级自动回退。
    """
    if not query.get("palette") and not query.get("dominant_hex"):
        raise ValueError("合成图需要人像识别结果（色板 + ROI），请先用 --photo 匹配")
    wanted = str(style or config.COMPOSE_STYLE).lower()
    if wanted not in ("wrap", "print", "garment"):
        raise ValueError("合成风格只能是 wrap（包装贴图）/ garment（基准衣服迁移）/ print（平涂印花）")
    im = _prepare(photo, query)
    im, output_scale = _ensure_resolution(im)
    arr = np.asarray(im, dtype=float)
    # 资产缺失时按贴图档参数分割：这样回退后的结果与直接 style=wrap 逐像素
    # 一致，不会因为「袖子不收窄 + 轮廓外扩张」而在边缘多出一圈改动。
    garment_ready = (wanted == "garment" and garment.has_asset(garment_id))
    seg = cloth.segment(
        im, box=portrait.parse_box(query.get("box")),
        exclude_skin=bool(query.get("exclude_skin", True)),
        exclude_bg=bool(query.get("exclude_bg", True)),
        engine=query.get("engine"),
        # 换装档整件替换：基准衣服自带肩带/袖型，收窄会在两肩留下原色条
        sleeve_trim=(0.0 if garment_ready else None),
        # 换装档额外吞掉轮廓外的同色阴影/侧条，避免边缘留一圈原色描边；
        # 贴图/平涂档保持关闭，历史效果不变。
        edge_grow=(config.CLOTH_EDGE_GROW if garment_ready else 0),
    )
    mask, hard = seg["mask"], seg["hard"]
    # 光照场 / 包络仍用原始软边，只有「覆盖权重」把衣服内部的字样洞补实心；
    # 臂-身之间那条背景缝不能补（补了手臂就粘在躯干上）。
    cover_mask = _solid_cover_mask(mask, hard, protect=seg.get("gap"))
    holes_filled = int(np.count_nonzero(cover_mask > mask + 1e-6))

    main_hex = str(brand.get("brand_hex") or "#C62828").upper()
    main_lab = np.asarray(color.hex_to_lab(main_hex), dtype=float)
    cloth_hex = str((query.get("cloth") or {}).get("cloth_hex")
                    or query.get("dominant_hex") or main_hex)
    cloth_lab = np.asarray(color.hex_to_lab(cloth_hex), dtype=float)
    strength = config.COMPOSE_STRENGTH if strength is None else float(strength)

    # R3：AI 只预测采样坐标场，不产生像素。默认只用深度模型给出的平滑视差场，
    # 加 wrap.py 里由衣服轮廓构造的柱面分量；两者都不含原图字样。
    depth_field = detail_field = None
    if wanted == "wrap" and getattr(config, "WRAP_DEPTH_WARP", True):
        depth_field = depth.field(im)
        if float(getattr(config, "WRAP_DEPTH_DETAIL_SCALE", 0) or 0) > 0:
            L = arr.mean(axis=2)
            hi = L - ndi.gaussian_filter(L, 2.0)
            hi = hi - ndi.gaussian_filter(hi, 1.2)
            clip = float(getattr(config, "WRAP_DEPTH_DETAIL_CLIP", 6.0))
            detail_field = np.tanh(hi / clip)

    style_used, fallback_reason, tex = wanted, None, None
    info_wrap: dict[str, Any] = {"source": None}
    # 袖子排除只收窄「覆盖遮罩」；贴图几何沿用收窄前的衣服框，否则
    # 标签取景会随 bbox 一起变窄并上移，把罐顶银白边印到领口/脖子上。
    tex_bbox = seg.get("wrap_bbox", seg["bbox"])
    tex_mask = seg.get("wrap_hard", seg["hard"])
    if wanted == "garment":
        info_wrap = garment.build_texture(garment_id, arr.shape[:2],
                                          tex_bbox, cloth_mask=tex_mask)
        tex = info_wrap["texture"]
        if tex is None:
            style_used = "wrap"
            fallback_reason = info_wrap["reason"] or "基准衣服资产不可用"
            # 回退包装贴图时补算视差场，保证与直接选 wrap 的结果一致
            if getattr(config, "WRAP_DEPTH_WARP", True):
                depth_field = depth.field(im)
                if float(getattr(config, "WRAP_DEPTH_DETAIL_SCALE", 0) or 0) > 0:
                    L = arr.mean(axis=2)
                    hi = L - ndi.gaussian_filter(L, 2.0)
                    hi = hi - ndi.gaussian_filter(hi, 1.2)
                    clip = float(getattr(config, "WRAP_DEPTH_DETAIL_CLIP", 6.0))
                    detail_field = np.tanh(hi / clip)
    if style_used == "wrap":
        info_wrap = wrap.build_texture(str(brand.get("key") or ""),
                                       arr.shape[:2], tex_bbox,
                                       cloth_mask=tex_mask,
                                       depth=depth_field, detail=detail_field)
        tex = info_wrap["texture"]
        if tex is None:
            style_used = "print"
            fallback_reason = info_wrap["reason"] or "包装贴图不可用"

    if style_used == "garment" and info_wrap.get("alpha") is not None:
        # 覆盖范围改成「基准衣服自己的版型」：领口形状、袖型、下摆都跟着基准走，
        # 目标那件衬衫的领子 / 外套的袖子不会再被整块涂平。
        a = np.asarray(info_wrap["alpha"], dtype=float)
        # 抹掉源轮廓放大后的锯齿：基准衣服只有 200~600px，4x 超分后边界仍是
        # 阶梯，直接拿它当掩版会在肩膀/腋下留一圈 4~6px 的锯齿。
        sig = max(1.6, min(arr.shape[:2]) * 0.007)
        a = ndi.gaussian_filter(a, sig)
        shape_src = str(getattr(config, "COMPOSE_GARMENT_SHAPE", "target")).lower()
        if shape_src == "source":
            # 跟着基准衣服的剪影：袖型/肩宽都来自基准，但基准比目标窄时
            # 两侧会留下原色竖带，需要更大的外扩去盖。
            limited = ndi.binary_dilation(hard, iterations=2) & (a > 0.5)
            limited = ndi.binary_closing(limited, np.ones((3, 3)))
            grow = max(2, round(min(arr.shape[:2]) * 0.0045))
        else:
            # 跟着目标人像的衣服：把基准衣服"穿"到目标这件衣服的范围内，
            # 边缘干净、不会漏原色，是最稳的默认。
            limited = hard
            grow = max(2, round(min(arr.shape[:2]) * 0.003))
        # 臂-身缝隙：外扩会从两侧把它重新抹平（缝只有 4~12px），外扩前后
        # 各扣一次，保证手臂与躯干之间始终留着背景。
        gap = seg.get("gap")
        if gap is not None and np.shape(gap) == limited.shape:
            limited = limited & ~gap
        limited = ndi.binary_dilation(limited, iterations=grow)
        if gap is not None and np.shape(gap) == limited.shape:
            limited = limited & ~gap
        # 在硬掩版层面取交集后重算亚像素过渡，不要拿两张软蒙版相乘 ——
        # 那样两层锯齿会叠加，成图上是一圈"撕纸边"式的原色残点。
        cover_mask = _solid_cover_mask(mask, limited, protect=gap)

    if style_used in ("wrap", "garment"):
        # 一次性覆盖：原号码/字母被包装像素整体替换，软边只参与边缘过渡
        shade = _shading_field(arr, mask, hard, cloth_lab)
        target = np.clip(tex * shade[..., None], 0.0, 255.0)
        m = np.clip(cover_mask * strength, 0.0, 1.0)[..., None]
        out_arr = arr * (1.0 - m) + target * m
        decorate = config.COMPOSE_WRAP_DECORATE if badge is None else bool(badge)
    else:
        out_arr = _recolor(arr, cover_mask, main_lab, cloth_lab, strength)
        decorate = True
    base = Image.fromarray(np.rint(np.clip(out_arr, 0, 255)).astype(np.uint8))

    if decorate:
        overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        _decorate(overlay, seg["safe_box"], brand, query,
                  config.COMPOSE_STICKER if sticker is None else bool(sticker), hard)
        alpha = np.asarray(overlay.getchannel("A"), dtype=float) / 255.0
        alpha *= np.clip(cover_mask * 1.15, 0.0, 1.0)  # 印花只落在衣服上
        overlay.putalpha(Image.fromarray(np.rint(alpha * 255).astype(np.uint8)))
        result = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    else:
        result = base

    path = Path(out) if out else config.COMPOSE_IMAGE
    if save:
        config.ensure_dirs()
        result.save(path, "PNG")
    info: dict[str, Any] = {
        "image": result,
        "path": str(path) if save else None,
        "preview": _data_url(result),
        "brand_key": brand.get("key"),
        "brand_name": brand.get("name_cn"),
        "brand_hex": main_hex,
        "compose_style": style_used,
        "compose_style_label": {"wrap": "包装贴图", "garment": "基准衣服迁移",
                                "print": "平涂印花"}[style_used],
        "garment_id": info_wrap.get("garment_id") if style_used == "garment" else None,
        "garment_meta": info_wrap.get("garment_meta") if style_used == "garment" else None,
        "garment_warp_rows": info_wrap.get("warp_rows") if style_used == "garment" else None,
        "wrap_source": (Path(info_wrap["source"]).name
                        if info_wrap.get("source") else None),
        "wrap_fallback_reason": fallback_reason,
        "wrap_continuous": info_wrap.get("continuous"),
        "wrap_extend_mode": config.WRAP_EXTEND_MODE,
        "depth_warp": bool(info_wrap.get("depth_warp")),
        "wrap_hd_source": "images_hd" in str(info_wrap.get("source") or ""),
        "cloth_coverage": seg["coverage"],
        "cloth_holes_filled": holes_filled,
        "cloth_sleeves_trimmed": seg.get("sleeves_trimmed", 0),
        "cloth_method": seg["method"],
        "cloth_engine": seg.get("engine"),
        "semantic_note": seg.get("semantic_note"),
        "cloth_bbox": seg["bbox"],
        "safe_box": seg["safe_box"],
        "neckline_y": seg["neckline_y"],
        "fallback_mask": seg["fallback"],
        "color_pairs": _pairs(query, brand),
        "size": list(result.size),
        "output_scale": output_scale,
        "source_size": [round(result.width / output_scale),
                        round(result.height / output_scale)],
    }
    if with_download:
        info["download"] = _data_url(result, fmt="PNG", max_side=1600)
    return info


def compose_from_payload(photo, payload: dict[str, Any], brand_key: str | None = None,
                         out=None, **kwargs) -> dict[str, Any]:
    """从 match_photo() 的结果里挑品牌（默认第一名）生成合成图。"""
    matches = payload.get("matches") or []
    if not matches:
        raise ValueError("没有可合成的品牌匹配结果")
    item = next((m for m in matches if m.get("key") == brand_key), None) if brand_key else None
    if brand_key and item is None:
        # --brand / 网页指定品牌可能不在展示的 Top-N 里，重算全量档案找出它。
        from . import matcher  # 延迟导入，避免与 matcher 形成模块级循环

        item = next((m for m in matcher.match(payload["query"], top=10 ** 6)
                     if m.get("key") == brand_key), None)
    if brand_key and item is None:
        raise ValueError(f"品牌清单里没有 {brand_key}，请检查 brands.yaml")
    return compose(photo, payload["query"], item or matches[0], out=out, **kwargs)
