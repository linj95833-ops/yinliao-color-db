"""基准人像衣橱（wardrobe）—— 用「已经换好装的人像」当衣服素材库。

与旧的「饮料包装库」的区别：
  旧库：每个品牌是罐/瓶的商品图，运行时要把矩形标签贴到衣服上；
  新库：每个品牌是一张**已经穿着该品牌球衣的人像**，衣服本身自带真实
        褶皱、图案随胸廓的透视、袖型与领口形状 —— 直接切下来变形贴到
        新输入的人像上，比贴标签像得多。

三件事都在这里：
  build_item()  离线把一张基准人像切成可复用的衣服资产（语义模型只在这里跑）
  list_items()  运行时读资产清单（不需要模型、不需要原图）
  match()       用输入人像的衣服多色色板，找颜色最接近的基准衣服

素材端的衣服提取不能用目标端那套「按肤色剔除」的规则：
金色 / 米白 / 亮黄的球衣会被当成皮肤啃掉一圈，纯白球衣又会被模型判成
背景（阿萨奶茶只认出胸标那一小块）。这里改成「语义概率场定范围 + 主色
聚类扩张定细节」：概率给的是"这件是不是上衣"，主色给的是"这块布是不是
同一件衣服"，两者都与肤色判据无关。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import cloth, color, config, garment, human, privacy

# 中文品牌名 -> 稳定的 ASCII 资产 id（URL、目录名都用它）
SLUGS = {
    "evian": "evian",
    "七喜": "seven_up",
    "九寒沙棘": "shaji_jiuhan",
    "五粮液": "wuliangye",
    "伊利纯牛奶": "yili_milk",
    "健力宝": "jianlibao",
    "加多宝": "jiaduobao",
    "劲凉冰红茶": "jinliang_ice_tea",
    "勇闯天涯": "yongchuang_tianya",
    "双黄连口服液": "shuanghuanglian",
    "可口可乐": "coca_cola",
    "和其正": "heqizheng",
    "哈尔滨啤酒": "harbin_beer",
    "外星人电解质水": "alien_electrolyte",
    "宝矿力矿泉水": "pocari_sweat",
    "康师傅冰红茶": "kangshifu_ice_tea",
    "康师傅水晶葡萄": "kangshifu_grape",
    "恒大冰泉": "hengda_spring",
    "景田矿泉水": "jingtian_water",
    "格瓦斯": "kvass",
    "水溶c100": "c100",
    "水溶C100": "c100",
    "王老吉": "wanglaoji",
    "盐汽水": "salt_soda",
    "红牛": "red_bull",
    "美年达": "mirinda",
    "茉莉绿茶": "jasmine_green_tea",
    "蜜桃乌龙茶": "peach_oolong",
    "蜜桃四季春": "peach_sijichun",
    "补水啦": "bushula",
    "酸梅汤": "sour_plum_soup",
    "金麦一号": "jinmai_no1",
    "阿萨奶茶": "asa_milk_tea",
    "雪碧": "sprite",
    "青梅绿茶": "green_plum_tea",
    "魔爪": "monster_energy",
    "鱼露调味汁": "fish_sauce",
}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif")

_CACHE: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------
# 命名
# --------------------------------------------------------------------------

def slugify(name_cn: str) -> str:
    """中文名 → 资产 id；表里没有的用「拼音化不了的」规则兜底（稳定哈希）。"""
    key = SLUGS.get(name_cn.strip())
    if key:
        return key
    low = name_cn.strip().lower()
    ascii_part = re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    if ascii_part:
        return ascii_part
    return "g" + hex(abs(hash(name_cn)) % (1 << 32))[2:]


def _dir(gid: str) -> Path:
    return config.WARDROBE_DIR / gid


# --------------------------------------------------------------------------
# 素材端衣服提取
# --------------------------------------------------------------------------

def _keep_components(mask: np.ndarray, n: int = 2) -> np.ndarray:
    cc, k = ndi.label(mask)
    if k <= 1:
        return mask
    sizes = np.bincount(cc.ravel())
    order = np.argsort(sizes[1:])[::-1][:n] + 1
    return np.isin(cc, order)


def border_background(arr: np.ndarray) -> np.ndarray | None:
    """与画面边框连通的背景像素（纯色棚拍/抠图最常见的形态）。

    开襟外套、夹克两侧会透出照相馆底色，`fill_holes` 会把这块背景当成
    「衣服内部」一起填进蒙版，条带图两侧就带出蓝色竖带。这里先用边框主色
    做一次连通域，把「连到画外」的那片底色摘掉；不是纯色背景时返回 None，
    调用方按原逻辑继续。
    """
    bg_rgb, bg_ok = cloth.background_color(np.asarray(arr, dtype=float))
    if not bg_ok:
        return None
    dist = max(float(getattr(config, "CLOTH_BG_DISTANCE", 26.0)), 22.0)
    like = np.linalg.norm(np.asarray(arr, dtype=float) - bg_rgb, axis=2) < dist
    cc, k = ndi.label(like)
    if k == 0:
        return np.zeros(like.shape, dtype=bool)
    edge = np.unique(np.concatenate([cc[0], cc[-1], cc[:, 0], cc[:, -1]]))
    edge = edge[edge != 0]
    if edge.size == 0:
        return np.zeros(like.shape, dtype=bool)
    return np.isin(cc, edge)


def _fill_holes(mask: np.ndarray, bg: np.ndarray | None = None) -> np.ndarray:
    """填洞，但不许把背景填回来（背景是洞，不是衣服）。"""
    filled = ndi.binary_fill_holes(mask)
    if bg is None:
        return filled
    return filled & ~bg


def drop_ad_banner(rgb: np.ndarray, mask: np.ndarray,
                   min_frac: float = 0.65, alien: float = 30.0,
                   min_rows_frac: float = 0.030,
                   max_rows_frac: float = 0.20,
                   ) -> tuple[np.ndarray, dict[str, Any]]:
    """裁掉压在衣服上的广告横幅条 —— 它是广告设计元素，不是衣服。

    实测来源：红牛的基准人像在下摆位置压了一条「红牛维生素功能饮料」的深蓝
    横幅。语义模型把它当成上衣的一部分，切进条带后换装时会在目标人像胯部印出
    一条蓝带，还伸出身体轮廓之外。

    判据用**色度**而不是全色差：横幅是纯色底 + 一排白字，明度在行内跳得很厉害
    （逐行中位色靠不住），但底色的色相与衣服整体完全不是一回事。
      · 先取整件衣服的色度中心（a*/b* 中位数，明度不参与）；
      · 逐行统计「色度离该中心 > 30」的像素占比，横贯整行的横幅会接近 1.0，
        而印花/胸标只占行宽的一部分；
      · 这条带要**上下都被正常衣服夹住**（两端各回落到 <0.2），高度占衣服的
        3%~20%。这样球衣下摆那种「一路到底的纯色」不会被误判成横幅。
    命中后从带起点整段截断，条带下缘落在横幅上沿那道真实衣摆上。
    """
    info: dict[str, Any] = {"dropped": False}
    H = mask.shape[0]
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size < 16:
        return mask, info
    top, bot = int(rows[0]), int(rows[-1])
    span = bot - top + 1
    if span < 40:
        return mask, info

    lab = color.rgb_to_lab(np.asarray(rgb, dtype=float).reshape(-1, 3))
    lab = lab.reshape(mask.shape[0], mask.shape[1], 3)
    body = np.zeros_like(mask)
    body[top + int(0.12 * span):top + int(0.88 * span) + 1] = True
    sel = body & mask
    if int(sel.sum()) < 200:
        return mask, info
    ca = float(np.median(lab[sel][:, 1]))
    cb = float(np.median(lab[sel][:, 2]))
    ab = np.hypot(lab[..., 1] - ca, lab[..., 2] - cb)
    far = ab > alien

    frac = np.zeros(H, dtype=float)
    count = np.zeros(H, dtype=int)
    for y in range(top, bot + 1):
        s = mask[y]
        count[y] = int(s.sum())
        if count[y] >= 3:
            frac[y] = float(far[y][s].mean())

    need = max(6, int(min_rows_frac * span))
    cap = max(need, int(max_rows_frac * span))
    edge = max(2, int(0.02 * span))

    runs: list[list[int]] = []
    cur = None
    for y in range(top, bot + 1):
        if frac[y] >= min_frac and count[y] >= 3:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)

    for start, end in runs:
        height = end - start + 1
        if height < need or height > cap:
            continue
        if start < top + 0.45 * span:
            continue
        above = frac[max(top, start - edge):start]
        below = frac[end + 1:min(bot, end + edge) + 1]
        if above.size < 2 or below.size < 2:
            continue
        if float(above.mean()) > 0.20 or float(below.mean()) > 0.20:
            continue
        out = mask.copy()
        out[start:] = False
        band = mask.copy()
        band[:start] = False
        px = lab[band]
        info.update({
            "dropped": True,
            "rows": [int(start), int(end)],
            "height": int(height),
            "span": int(span),
            "frac": round(float(frac[start:end + 1].mean()), 3),
            "hex": (color.rgb_to_hex(color.lab_to_rgb(np.median(px, axis=0)))
                    if len(px) >= 80 else None),
        })
        return out, info
    return mask, info


def _inpaint_nearest(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """用最近的「可信布料像素」替换不可信像素（背景混色、肤色）。

    条带图是逐行插值出来的，只要行内有一小段不可信像素（夹克与手臂之间的
    背景缝、领口洞里的下巴），插值的端点就会带出底色 / 肉色。与其在后续
    每一步都去猜，不如先把这些像素按最近邻布料色抹平。
    """
    if not valid.any():
        return rgb
    idx = ndi.distance_transform_edt(~valid, return_distances=False,
                                     return_indices=True)
    return rgb[tuple(idx)]


def _heal_down(rgb: np.ndarray, hole: np.ndarray,
               skip: np.ndarray | None = None) -> np.ndarray:
    """按列用上下两侧的布料像素线性插值，把 hole 补平。

    水印压的是一块垂直渐变的纯色布（下摆），纵向插值出来的颜色与周围连着，
    不会像「最近邻搬运」那样拖出一条横向接缝。列数最多几百，逐列 resample
    比整图 inpaint 更可控，也不引入额外依赖。

    skip 是「不能当端点」的像素（白色 logo）。左段的水印压在 logo 白尖上，
    端点取到白就会把白往下拖成一条雾带 —— 跳过之后端点落在白尖上方的布料上，
    洞里填的就是布，而不是白的延长线。
    """
    out = np.asarray(rgb, dtype=float).copy()
    H = hole.shape[0]
    ys = np.arange(H)
    for x in np.flatnonzero(hole.any(axis=0)):
        h = hole[:, x]
        v = ~h if skip is None else (~h & ~skip[:, x])
        if int(v.sum()) < 2:
            continue
        for c in range(3):
            out[h, x, c] = np.interp(ys[h], ys[v], out[:, x, c][v])
    return out


def _heal_rows(rgb: np.ndarray, hole: np.ndarray,
               skip: np.ndarray | None = None) -> np.ndarray:
    """逐行插值版本：只给「上下端点都干净」的列用。

    纵向插值要求洞的上下边都落在布上。水印左段压在 logo 白尖上时这条不成立，
    那一列与其硬插（会把白拖下来），不如让邻居列来给色 —— 见 _heal_patch。
    """
    out = np.asarray(rgb, dtype=float).copy()
    H = hole.shape[0]
    ys = np.arange(H)
    cols = np.flatnonzero(hole.any(axis=0))
    if cols.size == 0:
        return out
    ok_cols: list[int] = []
    for x in cols:
        h = hole[:, x]
        idx = np.flatnonzero(h)
        v = ~h
        if int(v.sum()) < 2:
            continue
        if skip is not None and (skip[idx[0], x] or skip[idx[-1], x]):
            continue
        for c in range(3):
            out[h, x, c] = np.interp(ys[h], ys[v], out[:, x, c][v])
        ok_cols.append(int(x))
    bad = np.setdiff1d(cols, np.asarray(ok_cols, dtype=int))
    if bad.size and ok_cols:
        okc = np.asarray(ok_cols, dtype=int)
        for x in bad:
            src = int(okc[np.argmin(np.abs(okc - x))])
            rows = hole[:, x]
            out[rows, x] = out[rows, src]
    return out


def _heal_patch(rgb: np.ndarray, hole: np.ndarray,
                skip: np.ndarray | None = None,
                sigma: tuple[float, float] = (2.0, 12.0)) -> np.ndarray:
    """把 hole 这块「重铺」成周围布料的样子。

    两步：先按列用上下两侧的像素纵向插值（保住垂直渐变的走向），再对洞内做
    一道横向为主的低通 —— 各列的上下端点颜色有细微差异，纯逐列插值会拉出
    一道一道的竖条纹，横向低通正好把条纹压掉而留下渐变。最后按「离洞边的
    距离」羽化：贴边处用插值原值（与外面严丝合缝），洞里才用平滑值。
    """
    out = _heal_rows(rgb, hole, skip)
    if not hole.any():
        return out
    smooth = ndi.gaussian_filter(out, sigma=(sigma[0], sigma[1], 0.0))
    d = ndi.distance_transform_edt(hole)
    w = np.clip(d / 6.0, 0.0, 1.0)[..., None]
    out[hole] = (out * (1.0 - w) + smooth * w)[hole]
    return out


def drop_watermark(rgb: np.ndarray, mask: np.ndarray | None = None,
                   *, bottom_frac: float = 0.46,
                   light: float = 82.0, chroma_max: float = 20.0,
                   thin_radius: float = 3.5,
                   min_width_frac: float = 0.20,
                   max_height_frac: float = 0.10,
                   pad: int = 3,
                   ) -> tuple[np.ndarray, dict[str, Any]]:
    """抹掉压在下摆上的账号水印（「抖音号：62239612181」那类）。

    基准人像是从短视频里截的图，画面底部常压一行半透明白色账号水印。切条带
    时它就是「布料上的白像素」，语义模型分不出来，换装后原样印到目标人像的
    下摆上（雪碧那张最明显）。

    难点是雪碧 logo 的白色描边同样纯白，单靠「白 + 位置」必然误伤。这里改用
    **笔画粗细**定位：对白色掩膜做距离变换，取「到白区边界 ≤ 3.5px」的那圈 ——
    水印是 6~8px 宽的细字，全是这种细笔画；logo 白框有 30px 以上的实心区，
    它的内部不属于细笔画。于是
      · 细笔画只在衣服下部出现（logo、印花都在中上部）→ 用 bottom_frac 卡；
      · 细笔画连成一条「横向铺开约半个衣宽、高不过衣高一成」的行带 → 水印；
      · 零散高光点凑不出这样的行带，落选。
    修复范围是**行带内所有白像素**（含被细笔画漏掉的字芯）外扩 pad，逐列用
    上下两侧布料纵向插值。水印压在 logo 白尖上的那几笔本来就看不清，插值时
    上下边界仍是白色，白尖的轮廓会顺势延续下来，不会切掉一块。
    """
    info: dict[str, Any] = {"dropped": False}
    if mask is not None and not mask.any():
        return rgb, info
    arr = np.asarray(rgb, dtype=float)
    H, W = arr.shape[:2]
    if H < 60 or W < 60:
        return rgb, info

    rows = np.flatnonzero(mask.any(axis=1)) if mask is not None else np.arange(H)
    if rows.size < 16:
        return rgb, info
    top, bot = int(rows[0]), int(rows[-1])
    span = bot - top + 1
    cut = top + int(bottom_frac * span)

    lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
    white = np.hypot(lab[..., 1], lab[..., 2]) < chroma_max
    white &= lab[..., 0] > light
    if mask is not None:
        white &= mask
    # 距离变换要在**完整**的白色区域上算：先按 bottom_frac 截断的话，截断处会
    # 多出一整行「贴边细笔画」，行投影的峰值跑到那里去。截断放到取 thin 之后。
    dist = ndi.distance_transform_edt(white)
    # logo 白框的外沿也是「细笔画」，光看自身厚度分不开；水印是孤零零写在绿布
    # 上的，离任何厚白都有段距离，白框的外沿紧贴自己那块厚白。用「到厚白的
    # 距离」把贴边的这圈摘掉。
    solid = white & (dist > 5.0)
    gap = ndi.distance_transform_edt(~solid)
    thin = white & (dist <= thin_radius) & (gap > 7.0)
    thin[:cut] = False
    if int(thin.sum()) < 60:
        return rgb, info
    # 细笔画之间免不了几个像素的空档，横向连一下再投影，行带才不会碎。
    thin = ndi.binary_closing(thin, np.ones((1, 9), dtype=bool))

    per_row = thin.sum(axis=1)
    win = per_row[cut:]
    if win.size == 0 or int(win.max()) < 12:
        return rgb, info
    # 从最厚的那一行字（数字串中部）向两侧扩，落到「薄到三成以下」为止。
    # 这样行带贴住字高，不会顺着 logo 白框的细边一路往上爬。
    peak = int(np.argmax(win)) + cut
    thr = max(6.0, 0.32 * float(per_row[peak]))
    y0 = peak
    while y0 > cut and per_row[y0 - 1] >= thr:
        y0 -= 1
    y1 = peak
    while y1 < H - 1 and per_row[y1 + 1] >= thr:
        y1 += 1
    h = y1 - y0 + 1
    if h < 6 or h > max(1.0, max_height_frac * span):
        return rgb, info
    xs = np.flatnonzero(thin[y0:y1 + 1].any(axis=0))
    if xs.size < 8:
        return rgb, info
    lo, hi = int(np.percentile(xs, 2)), int(np.percentile(xs, 98))
    if hi - lo + 1 < min_width_frac * W:
        return rgb, info

    # 最后三道门，把「logo 的白字」「下摆的白色描边」挡在外面：
    #   · 账号水印压在下摆偏上一点，不会贴着衣服最底边 —— 贴边的是下摆描边；
    #   · 水印是稀疏的细字，白像素占这块框的一两成；logo 大字能占到三到六成。
    if (y1 - top) / span < 0.90:
        return rgb, info
    if bot - y1 < 6:
        return rgb, info
    cover = float(white[y0:y1 + 1, lo:hi + 1].mean())
    if not (0.04 <= cover <= 0.28):
        return rgb, info

    y0 = max(cut, y0 - 2)
    y1 = min(H - 1, y1 + 2)
    lo = max(0, lo - 2 * pad)
    hi = min(W - 1, hi + 2 * pad)
    # 洞的上下边界必须落在干净的布上：边界就算只沾到一两笔白（字的顶边），
    # 纵向插值的端点也会取到白色，整行泛起一圈亮斑。所以边界一旦还带白就再
    # 往外让，最多让 8 行，免得顺着 logo 的白框一路爬上去。
    for _ in range(8):
        if y0 <= cut or int(white[y0 - 1, lo:hi + 1].sum()) == 0:
            break
        y0 -= 1
    for _ in range(8):
        if y1 >= H - 1 or int(white[y1 + 1, lo:hi + 1].sum()) == 0:
            break
        y1 += 1
    # 行带整块重铺：水印的四角是半透明抗锯齿，白判据只咬到字芯，只补字芯会
    # 留下一圈「暗字轮廓」。这块矩形里除水印就是平布，整块补最干净。
    hole = np.zeros((H, W), dtype=bool)
    hole[y0:y1 + 1, lo:hi + 1] = True
    if mask is not None:
        hole &= ndi.binary_dilation(mask, np.ones((5, 5), dtype=bool))
    # 能当插值端点的只有「绿布」：白色 logo 以及它那圈抗锯齿的浅边都不是布，
    # 拿它们当端点会把白往下拖成一条雾带。判据要卡两道：绿要比红高出四十、
    # 比蓝高出三十。只卡一道「绿压过红蓝」的话，白尖边缘那种 (176,235,206)
    # 的浅绿白也会混进来 —— 插值出来是一片发灰的绿。
    g = arr[..., 1]
    green = (g > arr[..., 0] + 40.0) & (g > arr[..., 2] + 30.0)
    out = _heal_patch(arr, hole, skip=~green)
    info.update({
        "dropped": True,
        "box": [int(lo), int(y0), int(hi) + 1, int(y1) + 1],
        "px": int(hole.sum()),
        "span": int(span),
    })
    return out, info


def _shave_bottom(im: Image.Image, frac: float = 0.085) -> Image.Image:
    """裁掉画面最底部一条（平台账号水印所在），再用尾行拉伸补回原尺寸。

    缩略图（thumb.jpg）是整张基准人像缩的，水印在天上地下都动不了它。240px
    的缩略图上水印笔画只有一两像素宽，阈值检测无从下手（白字和 logo 白框糊在
    一起），抹一笔就会在 logo 上留缺口。而短视频水印固定压在画面下缘 —— 直接
    把这一条裁掉、用衣服下摆的最后十几行拉伸补回来，人像比例不变、下摆只多
    出百分之一二的绿色，看不出痕迹。
    """
    W, H = im.size
    cut = int(round(frac * H))
    if cut < 2 or cut >= max(4, H // 4):
        return im
    arr = np.asarray(im.convert("RGB"), dtype=float)
    keep = arr[:H - cut]
    src = Image.fromarray(np.rint(np.clip(keep[-14:], 0, 255)).astype(np.uint8))
    tail = np.asarray(src.resize((W, cut), Image.Resampling.LANCZOS), dtype=float)
    # 极轻微压暗，免得补出来的下摆比上面亮一档。
    tail = tail * np.linspace(1.0, 0.985, cut)[:, None, None]
    out = np.concatenate([keep, np.clip(tail, 0, 255)], axis=0)
    return Image.fromarray(np.rint(out).astype(np.uint8))


def _base_from_prob(prob: np.ndarray) -> tuple[np.ndarray, float]:
    """概率场从高到低找第一档「够大」的上衣区域，作为扩张种子。"""
    H, W = prob.shape
    need = max(400.0, 0.004 * H * W)
    for th in (0.50, 0.35, 0.22, 0.14):
        b = _keep_components(prob >= th, 3)
        if b.sum() >= need:
            return b, th
    return _keep_components(prob >= 0.10, 3), 0.10


def material_mask(im: Image.Image) -> np.ndarray:
    """基准人像 → 整件衣服的硬蒙版（含袖子、领口、下摆，不含皮肤/背景）。

    语义模型给「哪里可能是上衣」的软概率，主色聚类给「这些像素是不是同一
    块布」。素材端不做肤色剔除 —— 金色球衣的 Lab 就在肤色带里，剔了就缺一圈。
    """
    arr = np.asarray(im.convert("RGB"), dtype=float)
    H, W = arr.shape[:2]
    info = human.parse(im)
    if info is None:
        seg = cloth.segment(im, engine="tone", layer_split=False,
                            edge_smooth=False)
        return np.asarray(seg["hard"], dtype=bool)

    prob = np.asarray(info["upper_prob"], dtype=np.float32)
    labels = np.asarray(info["labels"])
    upper_seed = np.asarray(info["upper"], dtype=bool)
    lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)

    def _torso_seed(seed: np.ndarray) -> np.ndarray:
        """从语义上衣里剔除「手持物」和手臂误判，只留躯干那块主种子。

        景田那张蓝衣蓝幕，模型把手里那瓶景田也判成 Upper-clothes。瓶子
        和躯干是两个不连通的块，且质心落在躯干框左侧；胸前紫字虽然也是
        独立色块，却在躯干框内，会保留。手臂误判块再用肤色比例剔掉。
        """
        cc, n = ndi.label(seed)
        if n <= 1:
            return seed
        sizes = np.bincount(cc.ravel())
        main = int(np.argmax(sizes[1:]) + 1)
        ys, xs = np.where(cc == main)
        if xs.size < 200:
            return seed
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        px = max(4, int(0.08 * (x1 - x0)))
        py = max(4, int(0.08 * (y1 - y0)))
        skin = cloth.skin_mask(arr)
        keep = [main]
        for i in range(1, n + 1):
            if i == main or sizes[i] < 0.02 * sizes[main]:
                continue
            cy, cx = ndi.center_of_mass(seed, cc, i)
            if not (x0 - px <= cx <= x1 + px and y0 - py <= cy <= y1 + py):
                continue
            comp = cc == i
            if float((comp & skin).sum()) / max(1, int(comp.sum())) > 0.35:
                continue
            keep.append(i)
        return np.isin(cc, keep)

    upper_seed = _torso_seed(upper_seed)

    base, lo = _base_from_prob(prob)
    if base.sum() < 200:
        seg = cloth.segment(im, engine="tone", layer_split=False,
                            edge_smooth=False)
        return np.asarray(seg["hard"], dtype=bool)

    def _source_components(mask: np.ndarray) -> np.ndarray:
        """只保留与语义上衣有交集的连通块。

        旧版按面积保留 Top-N：蓝衣蓝幕那张把同色的手持水瓶、胸前紫字
        当成独立衣服块留下来，条带里出现瓶身和波纹。语义 upper 明确
        不包含瓶子/背景，所以改为按「是否与 upper 有实质重叠」筛选；
        小块若大部分是肤色（模型误把手臂判成上衣），同样剔除。主块很大，
        金色衣服落在肤色区间也不会被这条规则误伤。
        """
        cc, n = ndi.label(mask)
        if n <= 1:
            return mask
        keep: list[int] = []
        skin = None
        for i in range(1, n + 1):
            comp = cc == i
            area = int(comp.sum())
            if int((comp & upper_seed).sum()) < max(50, 0.02 * area):
                continue
            if area < 0.25 * base.sum():
                if skin is None:
                    skin = cloth.skin_mask(arr)
                if float((comp & skin).sum()) / max(1, area) > 0.35:
                    continue
            keep.append(i)
        return np.isin(cc, keep) if keep else np.zeros_like(mask)

    # 脸/头发/四肢/下装：模型只要判成这些就一律不进衣服（袖子不会被判成 arm）
    protect = np.isin(labels, (1, 2, 3, 6, 9, 10, 11, 12, 13, 14, 15))
    protect = ndi.binary_dilation(protect, iterations=2)

    px = lab[base]
    rng = np.random.default_rng(config.COLOR_SEED)
    sample = px[rng.choice(len(px), min(6000, len(px)), replace=False)]
    k = int(np.clip(6, 2, max(2, len(np.unique(np.round(sample, 1), axis=0)))))
    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=k, n_init=3, random_state=config.COLOR_SEED).fit(sample)
    ratio = np.bincount(km.labels_, minlength=k) / max(1, len(sample))
    cores = km.cluster_centers_[ratio >= 0.06]
    if len(cores) == 0:
        cores = km.cluster_centers_

    dist = np.full((H, W), 1e9, dtype=np.float32)
    for c in cores:
        dist = np.minimum(dist, np.linalg.norm(lab - c, axis=2))

    grown = base.copy()
    bg = border_background(arr)
    if bg is not None:
        # 蓝衣配蓝幕（景田那张）会被颜色判据整件误标成背景。模型已经用
        # upper_prob 高置信度圈出了上衣，这里必须从背景掩版里豁免，否则
        # cand &= ~bg 会把整件衣服删掉，只剩瓶身和胸前紫字被当成"衣服"。
        # 只豁免高置信度主体，不豁免 prob 低的边缘，缝隙/脖子仍会被扣掉。
        bg = bg & ~ndi.binary_dilation(base, iterations=1)
    for _ in range(3):
        cand = (prob >= max(0.10, lo - 0.06)) & (dist < 14.0) & ~protect
        if bg is not None:
            cand &= ~bg
        new = ndi.binary_closing(cand, np.ones((3, 3)))
        new = _source_components(_fill_holes(new, bg))
        if new.sum() <= grown.sum() * 1.01:
            grown = new
            break
        grown = new
    grown = _source_components(_fill_holes(grown, bg))
    grown = ndi.binary_closing(grown, np.ones((5, 5)))
    grown = _fill_holes(grown, bg)

    # 顶部 25% 单独扣一次肤色：高领/立领的基准人像里，模型常把下巴与脖子
    # 一起判成上衣，切出来的条带顶部就带着一小块皮肤（魔爪那张最明显），
    # 贴到新衣服领口会印出一片肉色。衣服本体在最上面 25% 里不会出现肤色。
    ys = np.flatnonzero(grown.any(axis=1))
    if ys.size:
        top = int(ys[0] + 0.25 * (ys[-1] - ys[0]))
        band = np.zeros_like(grown)
        band[ys[0]:top + 1] = True
        grown &= ~(band & cloth.skin_mask(arr))
    # 素材端也抹一遍锯齿：这个轮廓会以 4x 放大后进入条带图。但平滑是按逐行
    # left..right 重建的，会把手臂与躯干之间那条背景缝重新填平，所以平滑完再
    # 减一次背景 —— 缝留着，row_fill 才会用两侧布料插值，而不是采样到底色。
    smoothed = cloth._smooth_edges(grown, strength=0.7)
    return smoothed if bg is None else (smoothed & ~bg)


# --------------------------------------------------------------------------
# 超分（只注入高频残差，衣服结构与图案位置仍由原图决定）
# --------------------------------------------------------------------------

_SR_SESSION = None


def _sr_session():
    global _SR_SESSION
    if _SR_SESSION is not None:
        return _SR_SESSION or None
    path = config.MODELS_DIR / "upscale_realesr_general_x4.onnx"
    if not path.is_file():
        _SR_SESSION = False
        return None
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(4, (__import__("os").cpu_count() or 2)))
        so.log_severity_level = 3
        _SR_SESSION = ort.InferenceSession(str(path), sess_options=so,
                                           providers=["CPUExecutionProvider"])
    except Exception:  # noqa: BLE001 - 超分只是加分项
        _SR_SESSION = False
    return _SR_SESSION or None


def _resize_rgb(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        .resize(size, Image.Resampling.LANCZOS), dtype=np.float32)


def upscale_rgb(rgb: np.ndarray, max_feed: int = 512,
                alpha: float = 0.66) -> tuple[np.ndarray, dict[str, Any]]:
    """4x 超分后再把「只含高频」的残差叠加到原图放大结果上。"""
    sess = _sr_session()
    orig = np.asarray(rgb, dtype=np.float32)
    H, W = orig.shape[:2]
    if sess is None:
        return orig, {"upscaled": False, "reason": "缺超分模型"}
    scale = min(1.0, max_feed / max(H, W))
    fw, fh = max(8, round(W * scale)), max(8, round(H * scale))
    feed = _resize_rgb(orig, (fw, fh))
    x = feed.transpose(2, 0, 1)[None] / 255.0
    names = [i.name for i in sess.get_inputs()]
    sr = sess.run(None, {names[0]: x})[0][0]
    sr = np.clip(np.transpose(sr, (1, 2, 0)) * 255.0, 0, 255).astype(np.float32)
    oh, ow = sr.shape[:2]
    base = _resize_rgb(orig, (ow, oh))
    detail = sr - _resize_rgb(_resize_rgb(sr, (fw, fh)), (ow, oh))
    out = np.clip(base + alpha * detail, 0, 255)
    return out, {"upscaled": True, "src_size": [W, H], "out_size": [ow, oh],
                 "alpha": alpha}


# --------------------------------------------------------------------------
# 衣服色板
# --------------------------------------------------------------------------

def garment_palette(rgb: np.ndarray, mask: np.ndarray, k: int = 6,
                    min_ratio: float = 0.05, top: int | None = None
                    ) -> list[dict[str, Any]]:
    """衣服区域的主色板（按面积降序），供换装匹配用。"""
    from sklearn.cluster import KMeans

    lab = color.rgb_to_lab(np.asarray(rgb, dtype=float).reshape(-1, 3))
    lab = lab.reshape(rgb.shape[0], rgb.shape[1], 3)
    px = lab[mask]
    if len(px) < 60:
        return []
    rng = np.random.default_rng(config.COLOR_SEED)
    sample = px[rng.choice(len(px), min(12000, len(px)), replace=False)]
    kk = int(np.clip(k, 2, max(2, len(np.unique(np.round(sample, 1), axis=0)))))
    km = KMeans(n_clusters=kk, n_init=4, random_state=config.COLOR_SEED).fit(sample)
    labels = km.predict(px)
    counts = np.bincount(labels, minlength=kk).astype(float)
    order = np.argsort(counts)[::-1]
    total = counts.sum() or 1.0
    out: list[dict[str, Any]] = []
    for i in order:
        ratio = counts[i] / total
        if ratio < min_ratio and out:
            continue
        r, g, b = color.lab_to_rgb(km.cluster_centers_[i])
        out.append({
            "hex": color.rgb_to_hex((r, g, b)),
            "lab": [round(float(v), 2) for v in km.cluster_centers_[i]],
            "ratio": round(float(ratio), 4),
        })
        if top and len(out) >= top:
            break
    return out[:top] if top else out


# --------------------------------------------------------------------------
# 离线构建单件资产
# --------------------------------------------------------------------------

def build_item(src_path: str | Path, name_cn: str, gid: str | None = None,
               out_root: Path | None = None,
               do_upscale: bool = True) -> dict[str, Any]:
    """把一张基准人像切成衣服资产：strip + profile + meta + thumb。"""
    src_path = Path(src_path)
    gid = gid or slugify(name_cn)
    out = (Path(out_root) if out_root else config.WARDROBE_DIR) / gid
    out.mkdir(parents=True, exist_ok=True)

    im = cloth.load_image(src_path)
    arr = np.asarray(im.convert("RGB"), dtype=float)
    mask = material_mask(im)
    ys, xs = np.where(mask)
    if len(xs) < 200:
        raise ValueError(f"{name_cn}：衣服区域过小（{len(xs)} px）")

    # 裁到衣服外接框（留 3% 边），小图做 4x 超分
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    pad = max(2, int(0.03 * max(x1 - x0, y1 - y0)))
    H, W = mask.shape
    x0, x1 = max(0, x0 - pad), min(W, x1 + pad)
    y0, y1 = max(0, y0 - pad), min(H, y1 + pad)
    crop = arr[y0:y1, x0:x1]
    cm = mask[y0:y1, x0:x1]
    bg_full = border_background(arr)
    if bg_full is not None:
        # 同上：蓝衣蓝幕时颜色判据会把衣服本身也标成背景。material_mask 已
        # 经给出可信衣服轮廓，后续从条带里清背景混色时必须把它保护起来，
        # 否则整件蓝衣会被 nearest 主色替换掉，只剩印花被拉伸成波纹。
        bg_full = bg_full & ~ndi.binary_dilation(mask, iterations=1)
    bg_crop = bg_full[y0:y1, x0:x1] if bg_full is not None else None

    sr_info: dict[str, Any] = {"upscaled": False}
    short = min(crop.shape[:2])
    if do_upscale and short < int(getattr(config, "WARDROBE_UPSCALE_SHORT", 260)):
        rgb_sr, sr_info = upscale_rgb(crop)
        m_sr = np.asarray(Image.fromarray((cm * 255).astype(np.uint8)).resize(
            (rgb_sr.shape[1], rgb_sr.shape[0]), Image.Resampling.BILINEAR),
            dtype=np.float32) > 110
        m_sr = cloth._smooth_edges(m_sr, strength=0.6)
    else:
        rgb_sr, m_sr = crop, cm

    Hc, Wc = m_sr.shape
    cap = int(getattr(config, "WARDROBE_MAX_SIDE", 760))
    if max(Hc, Wc) > cap:
        s = cap / max(Hc, Wc)
        size = (max(8, round(Wc * s)), max(8, round(Hc * s)))
        rgb_sr = _resize_rgb(rgb_sr, size)
        m_sr = np.asarray(Image.fromarray((m_sr * 255).astype(np.uint8))
                          .resize(size, Image.Resampling.BILINEAR)) > 110
    if bg_crop is not None:
        bg_crop = np.asarray(
            Image.fromarray((bg_crop * 255).astype(np.uint8)).resize(
                (m_sr.shape[1], m_sr.shape[0]), Image.Resampling.BILINEAR)) > 110

    # 广告横幅（压在衣服上的深蓝条之类）不是布料：整段截掉再算轮廓，否则它会
    # 被当成下摆的一部分，换装时印到目标人像的胯部并伸出身体轮廓（红牛那张）。
    m_sr, banner = drop_ad_banner(np.asarray(rgb_sr, dtype=float), m_sr)

    # 领口是球衣的版型，不能填平：留作 alpha 的开口，换装后脖子/内搭才露得出来。
    # 条带也直接用未填洞的蒙版：row_fill 只会用「蒙版内」的像素插值，洞一旦被
    # fill_holes 标成有效，里面的脖子肤色 / 开襟底色就会被采进纹理。
    m_solid = m_sr
    ratio = float(getattr(config, "GARMENT_WIDTH_RATIO", 0.45))
    shrink = float(getattr(config, "WARDROBE_SHRINK", 0.05))
    left, right, center, half, ry0, ry1, good = garment.row_profile(
        m_solid, ratio, shrink)
    # 领口以上的那几行（脖子露出来的地方）不是布料：它们只会把肤色带进纹理，
    # 还会让 warp 把整个条带高度当成衣服高度。从「行宽达到峰值 70%」的第一行
    # 开始算衣服顶部 —— 肩线。下面几行由 row_fill 用肩部那行复制补齐。
    if good.any():
        width = right - left
        peak = float(np.percentile(width[good], 90))
        strong = np.flatnonzero(good & (width >= 0.70 * peak))
        if strong.size and strong[0] > ry0:
            good = good.copy()
            good[:int(strong[0])] = False
            ry0 = int(strong[0])
    # 采样蒙版比轮廓蒙版内缩 1px：最外圈是「布料 × 背景」的抗锯齿混色，颜色
    # 已经偏底色了（黑皮衣两侧会带出一条亮蓝边）。缩掉之后 row_fill 会用内部
    # 布料插值补上这一圈，条带就不会自己带背景色。
    m_fill = ndi.binary_erosion(m_solid, np.ones((3, 3), dtype=bool))
    if m_fill.sum() < m_solid.sum() * 0.5:
        m_fill = m_solid
    # 再抹掉两类不可信像素，用最近布料色替代：
    #   ① 背景混色（夹克与手臂之间的缝、抠图边缘）—— 会在条带里留竖条底色；
    #   ② 顶部 35% 的肤色（下巴 / 脖子）—— 会印到目标人像的领口。
    valid = m_fill.copy()
    if bg_crop is not None:
        valid &= ~ndi.binary_dilation(bg_crop, iterations=1)
    rows = np.flatnonzero(m_fill.any(axis=1))
    if rows.size:
        top = int(rows[0] + 0.35 * (rows[-1] - rows[0]))
        # 领口洞里露出来的是下巴 / 脖子：它在阴影里常常偏棕，肤色判据抓不住。
        # 换一个更硬的判据 —— 顶部这条带里，颜色必须落在「衣服自己的色板」附近，
        # 否则一律当异物（肤色、内搭、背景）用最近布料像素替换。印花本来就在
        # 色板里，不会被抹掉。
        body = np.zeros_like(valid)
        body[top + 1:] = True
        pal = garment_palette(np.asarray(rgb_sr, dtype=float), body & m_fill,
                              k=6, min_ratio=0.0)
        if pal:
            lab = color.rgb_to_lab(np.asarray(rgb_sr, dtype=float).reshape(-1, 3))
            lab = lab.reshape(valid.shape[0], valid.shape[1], 3)
            centers = np.asarray([c["lab"] for c in pal], dtype=float)
            d_full = np.full(valid.shape, 1e9)
            d_hue = np.full(valid.shape, 1e9)
            for c in centers:
                d_full = np.minimum(d_full, np.linalg.norm(lab - c, axis=2))
                # 只看彩度差：脖子肤色是「暖、弱饱和」，皮衣灰是「中性」，
                # 两者明度可以很接近（实测 ΔE 20.9 < 阈值），但 b* 差 17。
                d_hue = np.minimum(d_hue, np.max(
                    np.abs(lab - c)[..., 1:], axis=2))
            band = np.zeros_like(valid)
            band[rows[0]:top + 1] = True
            valid &= ~(band & ((d_hue > 8.0) | (d_full > 30.0)))
    rgb_clean = _inpaint_nearest(np.asarray(rgb_sr, dtype=float), valid)
    strip = garment.row_fill(rgb_clean, valid, left, right, good)

    # 视频截图自带的账号水印（「抖音号：xxxx」）压在衣服下摆上，切进条带后就
    # 成了布料的一部分，换装时会被印到目标人像身上。这一步只认「贴底、稀疏、
    # 横贯大半衣宽的白字」，命中才动，其余资产原样通过。
    strip, watermark = drop_watermark(strip, m_sr)

    Hc, Wc = m_sr.shape
    Image.fromarray(np.rint(np.clip(strip, 0, 255)).astype(np.uint8)).save(
        out / "strip.webp", "WEBP", quality=95, method=4)
    Image.fromarray((m_sr * 255).astype(np.uint8)).save(out / "mask.png")
    np.savez_compressed(out / "profile.npz",
                        center=center.astype(np.float32),
                        half=half.astype(np.float32),
                        good=good, y0=ry0, y1=ry1, height=Hc, width=Wc,
                        width_ratio=ratio, shrink=0.0)

    palette = garment_palette(rgb_sr, m_sr, k=6, min_ratio=0.05,
                              top=int(getattr(config, "MATCH_VECTOR_MAX", 5)))
    # 缩略图同样不能带水印：命中水印的素材，底部一律裁掉那一条再补回。
    thumb = _shave_bottom(im) if watermark.get("dropped") else im.copy()
    thumb.thumbnail((240, 240), Image.Resampling.LANCZOS)
    # 再给脸打码：thumb 是整张人像，交付物里不该出现可辨认的脸（见 privacy.py）。
    thumb, face_mask = privacy.mask_head(thumb)
    thumb.save(out / "thumb.jpg", "JPEG", quality=86)

    meta = {
        "id": gid,
        "name_cn": name_cn,
        "brand_key": gid,
        "layout_class": "full_wrap",
        "source_file": src_path.name,
        "size": [Wc, Hc],
        "src_rows": [ry0, ry1],
        "width_ratio": ratio,
        "shrink": shrink,
        "mask_coverage": round(float(m_sr.mean()), 4),
        "mask_bbox": [x0, y0, x1, y1],
        "source_size": [int(W), int(H)],
        "upscale": sr_info,
        "banner_trim": banner,
        "watermark": watermark,
        "privacy": face_mask,
        "palette": palette,
        "hex": palette[0]["hex"] if palette else "#888888",
        "segment_engine": "material-prob",
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE.pop(gid, None)
    return meta


# --------------------------------------------------------------------------
# 运行时：清单 / 加载 / 匹配
# --------------------------------------------------------------------------

def list_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    root = config.WARDROBE_DIR
    if not root.is_dir():
        return items
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        f = d / "meta.json"
        if not f.is_file():
            continue
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        has_strip = any((d / n).is_file()
                        for n in ("strip.png", "strip.webp", "strip.jpg"))
        if not has_strip or not (d / "profile.npz").is_file():
            continue
        m["available"] = True
        m["thumb_url"] = f"/api/thumb/{m['id']}"
        items.append(m)
    items.sort(key=lambda x: x["name_cn"])
    return items


def get(gid: str) -> dict[str, Any] | None:
    for m in list_items():
        if m["id"] == gid:
            return m
    return None


def as_brand(item: dict[str, Any]) -> dict[str, Any]:
    """包成 compose() 认识的 brand 结构（garment 档只用主色与中文名）。"""
    palette = item.get("palette") or []
    return {
        "key": item["id"],
        "name_cn": item.get("name_cn") or item["id"],
        "name_en": "",
        "brand_hex": (palette[0]["hex"] if palette else item.get("hex")) or "#888888",
        "color_vector": palette,
    }


def status() -> dict[str, Any]:
    items = list_items()
    return {"dir": str(config.WARDROBE_DIR), "count": len(items),
            "available": bool(items), "ids": [x["id"] for x in items]}


def match(query: dict[str, Any], top: int = 8) -> list[dict[str, Any]]:
    """输入人像的衣服色板 → 颜色最接近的基准衣服。复用 matcher 的双向色差。"""
    from . import matcher  # 延迟导入，避免循环

    q_items, main, query_hex = matcher._query_palette(query)
    out: list[dict[str, Any]] = []
    for item in list_items():
        b_items = item.get("palette") or []
        if not b_items:
            continue
        joint, q2b, b2q, details = matcher._joint_distance(q_items, b_items)
        out.append({
            "id": item["id"],
            "key": item["id"],
            "name_cn": item.get("name_cn"),
            "brand_hex": b_items[0]["hex"],
            "query_hex": query_hex,
            "joint_delta_e": round(joint, 2),
            "query_to_brand": round(q2b, 2),
            "brand_to_query": round(b2q, 2),
            "score": round(max(0.0, 100.0 - config.MATCH_SCORE_SCALE * joint), 2),
            "color_vector": b_items,
            "color_matches": details,
            "thumb_url": item.get("thumb_url"),
        })
    out.sort(key=lambda x: x["joint_delta_e"])
    for i, item in enumerate(out[:max(1, top)], 1):
        item["rank"] = i
    return out[:max(1, top)]


def thumb_bytes(gid: str) -> bytes | None:
    d = _dir(gid)
    for name in ("thumb.jpg", "source.jpg", "strip.webp"):
        p = d / name
        if p.is_file():
            return p.read_bytes()
    return None
