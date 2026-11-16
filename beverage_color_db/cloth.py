"""按「衣服色调」分割人像衣服区域，不再假设人体是矩形。

为什么要独立做一层分割：
- 旧版用归一化矩形 ROI + Lab 距离取衣服色，遇到 V 领、无袖、裸臂就会把
  皮肤/背景框进来，合成图也只能在矩形里换色；
- 衣服上的白色号码、深色描边、印花与布料色调不同，逐像素取色会留下"洞"，
  换色后仍能看到原来的 LAKERS / 24 字样。

本模块的做法（只用 numpy / Pillow / scipy / sklearn，均已随项目安装）：
1. 全图 Lab 色调聚类，把像素分成若干「色调簇」；
2. 用四边框量化直方图估计背景色，用 RGB+YCbCr 判据给出肤色种子；
   据此把簇标成 背景 / 皮肤 / 衣服 / 印花；
3. 取 ROI 提示区里占比最大的衣服簇作为种子色调，合并色度相近的簇
   （高光、阴影、网眼仍算同一件衣服）；
4. 检测领口：中央竖条第一次被衣服填满的行。领口以上只保留左右肩的
   连续衣服段（V 领、脖子、脸不会被填），领口以下按每行衣服的左右边界
   整段填充 —— 号码、字母、描边、网眼全部并入衣服区域；
5. 形态学封口 + 最大连通域 + 填洞（底边补齐，处理号码压到图片边缘）；
   再扣除背景色像素；
6. 边缘羽化成 0..1 软 mask，并给出衣服外接框与"可安全印字"的核心框。

分割失败/覆盖异常时回退到旧的「ROI 内 Lab 距离软蒙版」，保证不崩。
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage as ndi
from sklearn.cluster import KMeans

from . import color, config, human


def load_image(source: str | Image.Image) -> Image.Image:
    """统一转成 RGB（透明 PNG 合成到白底），并按人像上限等比缩放。"""
    if isinstance(source, Image.Image):
        im = color.to_rgb(source.copy())
    else:
        with Image.open(source) as raw:
            raw.load()
            im = color.to_rgb(raw)
    w, h = im.size
    if max(w, h) > config.PORTRAIT_MAX_SIDE:
        s = config.PORTRAIT_MAX_SIDE / max(w, h)
        im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.Resampling.LANCZOS)
    return im


def skin_mask(arr: np.ndarray) -> np.ndarray:
    """RGB + YCbCr 双重肤色判据。深色皮肤由色调聚类兜底，这里只做种子。"""
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    rgb_rule = ((r > 95) & (g > 40) & (b > 20) & ((mx - mn) > 15)
                & (np.abs(r - g) > 15) & (r > g) & (r > b))
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return rgb_rule & (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)


def background_color(arr: np.ndarray) -> tuple[np.ndarray, bool]:
    """四边框量化直方图取主色。比四角中位数稳健：人像下方常是手臂/身体。"""
    H, W, _ = arr.shape
    k = max(3, int(min(H, W) * config.CLOTH_BORDER_FRAC))
    border = np.concatenate([
        arr[:k].reshape(-1, 3), arr[-k:].reshape(-1, 3),
        arr[:, :k].reshape(-1, 3), arr[:, -k:].reshape(-1, 3),
    ])
    q = (border // config.CLOTH_BG_QUANT).astype(int)
    keys, counts = np.unique(q, axis=0, return_counts=True)
    top = int(np.argmax(counts))
    frac = float(counts[top] / len(border))
    bg = keys[top] * config.CLOTH_BG_QUANT + config.CLOTH_BG_QUANT / 2
    return bg.astype(float), frac >= config.CLOTH_BG_MIN_BORDER_FRAC


def _hint_mask(shape: tuple[int, int], box: tuple[float, float, float, float]) -> np.ndarray:
    H, W = shape
    m = np.zeros((H, W), dtype=bool)
    x1, y1, x2, y2 = box
    m[max(0, int(y1 * H)):min(H, max(1, int(y2 * H))),
      max(0, int(x1 * W)):min(W, max(1, int(x2 * W)))] = True
    return m


def _tone_labels(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """全图 Lab 色调聚类：拟合用抽样，预测用全图，兼顾速度与精度。"""
    lab = color.rgb_to_lab(arr.reshape(-1, 3))
    flat = lab.reshape(-1, 3)
    step = max(1, len(flat) // config.CLOTH_SAMPLE_PIXELS)
    sample = flat[::step]
    # 纯色自测图可能只有 3~5 种颜色，聚类数不能多于样本里的不同颜色
    distinct = len(np.unique(np.round(sample, 3), axis=0))
    k = max(2, min(config.CLOTH_CLUSTERS, distinct))
    km = KMeans(n_clusters=k, n_init=4, random_state=config.COLOR_SEED)
    km.fit(sample)
    labels = km.predict(flat).reshape(arr.shape[:2])
    return labels, km.cluster_centers_


def _morph(m: np.ndarray, op: str, structure: np.ndarray) -> np.ndarray:
    """边界复制补齐后再做形态学，避免衣服贴到画面底边时被腐蚀掉。"""
    pad = max(2, max(int(s) // 2 + 1 for s in structure.shape))
    padded = np.pad(m, pad, mode="edge")
    out = getattr(ndi, op)(padded, structure=structure)
    return out[pad:-pad, pad:-pad]


def _envelope(seed: np.ndarray, bg_like: np.ndarray) -> tuple[np.ndarray, int | None, bool]:
    """由衣服色调种子长出真实衣服轮廓。返回 (硬 mask, 领口行, 是否猜测领口)。"""
    H, W = seed.shape
    s0, s1 = int(config.CLOTH_NECK_CENTER[0] * W), int(config.CLOTH_NECK_CENTER[1] * W)
    center_frac = seed[:, s0:max(s0 + 1, s1)].mean(axis=1)
    solid: int | None = None
    for y in range(int(0.15 * H), H - 4):
        if float(center_frac[y:y + 4].mean()) >= config.CLOTH_NECK_MIN_FRAC:
            solid = y
            break
    guessed = solid is None
    if solid is None:
        solid = int(0.40 * H)

    env = np.zeros((H, W), dtype=bool)
    for y in range(H):
        xs = np.flatnonzero(seed[y])
        if not len(xs):
            continue
        if y >= solid:
            # 领口以下：整件衣服是连续的，中间的白号码/描边都算衣服
            env[y, xs[0]:xs[-1] + 1] = True
        else:
            # 领口以上：V 领和脖子露在外面，只保留肩部连续布料段
            cuts = np.where(np.diff(xs) > config.CLOTH_RUN_GAP)[0]
            starts = np.r_[0, cuts + 1]
            ends = np.r_[cuts, len(xs) - 1]
            for st, en in zip(starts, ends):
                if en - st >= config.CLOTH_MIN_RUN:
                    env[y, xs[st]:xs[en] + 1] = True
    # 整行都是印花、没有任何布料种子时，用上一行的衣服宽度续上
    for y in range(solid + 1, H):
        if not env[y].any() and env[y - 1].any():
            xs = np.flatnonzero(env[y - 1])
            env[y, xs[0]:xs[-1] + 1] = True

    radius = max(3, int(min(H, W) * config.CLOTH_VERTICAL_CLOSE_FRAC))
    env = _morph(env, "binary_closing", np.ones((radius, 1)))
    cc, n = ndi.label(env)
    if n:
        sizes = np.bincount(cc.ravel())
        sizes[0] = 0
        env = cc == int(np.argmax(sizes))
    # 底部补两行衣服，让压到画面底边的号码也变成"洞"从而被填上
    env = ndi.binary_fill_holes(np.vstack([env, np.ones((2, W), dtype=bool)]))[:H]
    hard = _morph(env & ~bg_like, "binary_opening", np.ones((3, 3)))
    return hard, None if guessed else solid, guessed


def _edge_refine(arr: np.ndarray, hard: np.ndarray, ref: np.ndarray,
                 skin: np.ndarray, bg_like: np.ndarray,
                 exclude_skin: bool = True) -> np.ndarray:
    """把紧贴轮廓的滚边/侧条/抗锯齿像素并回衣服，消掉换色后的原色细线。

    逐行包络只按"衣服色调种子"的左右边界填充，因此领口、肩线上的金色滚边、
    深紫描边、白色侧条会留在 mask 外，换色后形成一条显眼的金边。这里只在
    轮廓外很窄的一圈里，把与衣服同色调的明暗像素、近白侧条、深紫描边补回来；
    背景色像素始终排除，深色肤色像素在启用肤色排除时也排除。
    """
    H, W = hard.shape
    lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
    chroma = np.hypot(lab[..., 1] - ref[1], lab[..., 2] - ref[2])
    neutral = np.hypot(lab[..., 1], lab[..., 2])
    band = ndi.binary_dilation(
        hard, iterations=config.CLOTH_EDGE_DILATE) & ~hard
    bright = (band & (chroma < config.CLOTH_EDGE_CHROMA)
              & (lab[..., 0] > config.CLOTH_EDGE_BRIGHT_L) & ~bg_like)
    dark = (band & (chroma < config.CLOTH_EDGE_DARK_CHROMA)
            & (lab[..., 0] <= config.CLOTH_EDGE_BRIGHT_L)
            & (~skin if exclude_skin else True) & ~bg_like)
    panel = (band & (lab[..., 0] > config.CLOTH_EDGE_PANEL_L)
             & (neutral < config.CLOTH_EDGE_NEUTRAL) & ~bg_like)
    purple = (band & (lab[..., 0] < 40) & (lab[..., 2] < -4) & ~bg_like)
    edge = bright | dark | panel | purple
    if not edge.any():
        return hard
    return _morph(hard | edge, "binary_closing", np.ones((3, 3)))


def _legacy_mask(arr: np.ndarray, ref: np.ndarray, box, skin: np.ndarray,
                 bg_like: np.ndarray) -> np.ndarray:
    """兜底：旧的「ROI 内 Lab 距离」软蒙版，形状受矩形限制但不会崩。"""
    H, W, _ = arr.shape
    x1, y1, x2, y2 = box
    bx = (max(0, int(x1 * W)), max(0, int(y1 * H)),
          min(W, int(x2 * W)), min(H, int(y2 * H)))
    crop = arr[bx[1]:bx[3], bx[0]:bx[2]]
    if not crop.size:
        return np.zeros((H, W), dtype=float)
    lab = color.rgb_to_lab(crop)
    d = np.sqrt((config.CLOTH_LEGACY_L_WEIGHT * (lab[..., 0] - ref[0])) ** 2
                + (lab[..., 1] - ref[1]) ** 2 + (lab[..., 2] - ref[2]) ** 2)
    soft = np.clip((config.COMPOSE_MASK_OUTER - d)
                   / max(1.0, config.COMPOSE_MASK_OUTER - config.COMPOSE_MASK_INNER), 0, 1)
    soft = np.where(skin[bx[1]:bx[3], bx[0]:bx[2]], soft * 0.05, soft)
    soft = np.where(bg_like[bx[1]:bx[3], bx[0]:bx[2]], soft * 0.2, soft)
    full = np.zeros((H, W), dtype=float)
    full[bx[1]:bx[3], bx[0]:bx[2]] = soft
    return full


def _safe_box(hard: np.ndarray, neckline: int | None) -> list[int]:
    """在衣服最扎实的核心区域里给印字/贴片找一个框，避开 V 领和手臂。"""
    H, W = hard.shape
    row_w = hard.sum(axis=1)
    if row_w.max() == 0:
        return [0, 0, W, H]
    start = neckline if neckline is not None else int(0.35 * H)
    wide = np.zeros(H, dtype=bool)
    wide[start:] = row_w[start:] >= 0.72 * row_w.max()
    ys = np.flatnonzero(wide)
    if not len(ys):
        ys = np.flatnonzero(row_w >= 0.6 * row_w.max())
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    sub = hard[y1:y2]
    col_w = sub.sum(axis=0)
    xs = np.flatnonzero(col_w >= 0.45 * max(1, y2 - y1))
    if not len(xs):
        xs = np.flatnonzero(col_w > 0)
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    if x2 - x1 < 12 or y2 - y1 < 12:
        yy, xx = np.where(hard)
        return [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]
    return [x1, y1, x2, y2]


def _soft_from_hard(hard: np.ndarray) -> np.ndarray:
    """硬蒙版 → 0..1 软蒙版：中值去毛刺 + 窄高斯羽化 + 增益保证内部全覆盖。"""
    H, W = hard.shape
    pad = 4
    padded = np.pad((hard * 255).astype(np.uint8), pad, mode="edge")
    m = Image.fromarray(padded)
    m = m.filter(ImageFilter.MedianFilter(3))
    m = m.filter(ImageFilter.GaussianBlur(
        max(0.8, min(H, W) * config.CLOTH_SOFT_BLUR_FRAC)))
    return np.clip(np.asarray(m, dtype=float)[pad:pad + H, pad:pad + W]
                   / 255.0 * config.CLOTH_SOFT_GAIN, 0.0, 1.0)


def _neckline_from_mask(mask: np.ndarray) -> tuple[int, bool]:
    """中央竖条第一次被衣服填满的行；找不到则硬猜 0.40*H 并标记 guessed。"""
    H, W = mask.shape
    s0, s1 = int(config.CLOTH_NECK_CENTER[0] * W), int(config.CLOTH_NECK_CENTER[1] * W)
    frac = mask[:, s0:max(s0 + 1, s1)].mean(axis=1)
    for y in range(int(0.15 * H), H - 4):
        if float(frac[y:y + 4].mean()) >= config.CLOTH_NECK_MIN_FRAC:
            return y, False
    return int(0.40 * H), True


def _semantic_expand(arr: np.ndarray, hard: np.ndarray, info: dict,
                     skin: np.ndarray, exclude_skin: bool) -> np.ndarray:
    """模型给出的上衣范围之外的修补。

    模型会把袖子判成 Left/Right-arm、把深色拼块判成 Bag、把高光判成 Background，
    所以这里做两件事：
    1. 沿轮廓按「衣服多色中心」补一圈（不再只用一个主色）；
    2. 区域生长：与衣服空间相邻、色调一致、且仍属于人体的整块连通区域并回来，
       这样整条袖子/拼色下摆不会被丢掉。背景、头发、裤子、鞋、肤色始终排除。
    """
    H, W = hard.shape
    lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
    px = lab[hard]
    if len(px) < 50:
        return hard
    rng = np.random.default_rng(config.COLOR_SEED)
    sample = px[rng.choice(len(px), min(12000, len(px)), replace=False)]
    distinct = len(np.unique(np.round(sample, 2), axis=0))
    km = KMeans(n_clusters=max(1, min(4, distinct)), n_init=3,
                random_state=config.COLOR_SEED).fit(sample)
    # 只取占比最大的几个颜色中心：号码描边/阴影这种小比例色不能拿去扩张，
    # 否则会把手臂纹身、深色背景整片认成衣服（实测会放大成竖条伪影）。
    counts = np.bincount(km.labels_, minlength=len(km.cluster_centers_))
    order = np.argsort(counts)[::-1][:3]
    d = np.full((H, W), np.inf, dtype=float)
    for c in km.cluster_centers_[order]:
        dc = np.sqrt((0.75 * (lab[..., 0] - c[0])) ** 2
                     + (lab[..., 1] - c[1]) ** 2 + (lab[..., 2] - c[2]) ** 2)
        d = np.minimum(d, dc)
    allowed = info["person"] & ~info["background"] & ~info["hair"] & ~info["lower"]
    if exclude_skin:
        allowed &= ~skin
    band = ndi.binary_dilation(hard, iterations=config.HUMAN_TONE_DILATE) & ~hard
    hard = hard | (band & allowed & (d < config.HUMAN_TONE_DISTANCE))
    if config.HUMAN_REGION_GROW:
        # 整块连通的同色调区域（袖子、拼色块）。默认关闭：模型本身已给出
        # 整体范围，而这一步会把背景上的零星误判放大成显眼色块。
        tone = allowed & (d < config.HUMAN_TONE_DISTANCE * 1.15)
        cc, n = ndi.label(tone)
        if n:
            near = ndi.binary_dilation(hard, iterations=config.HUMAN_REGION_DILATE)
            touch = np.unique(cc[near & tone])
            touch = touch[touch > 0]
            sizes = np.bincount(cc.ravel(), minlength=n + 1)
            cap = config.HUMAN_GROW_MAX_RATIO * max(1, int(hard.sum()))
            for t in touch:
                if sizes[t] <= cap:
                    hard |= cc == t
    return hard


def _trim_sleeves(hard: np.ndarray, person: np.ndarray,
                  trim: float, smooth: int) -> np.ndarray:
    """按人像逐行轮廓向内收窄，让覆盖停在躯干、不延伸到袖子/上臂。

    语义模型把长袖外套整件判成 Upper-clothes（含袖子）；18 类里只有「裸露的
    Left/Right-arm」，没有「袖子」类，所以扣 arm 标签几乎不起作用（实测基准
    01 只去掉 0.2% 像素）。这里改为沿每行 person 的左右边界按同一比例内缩，
    边界随肩线/躯干外形弯曲，而不是切一条竖直硬线；中值滤波消除逐行抖动。
    """
    H, W = hard.shape
    if person.shape != (H, W) or trim <= 0:
        return hard
    left = np.zeros(H, dtype=float)
    right = np.full(H, W - 1, dtype=float)
    for y in range(H):
        xs = np.flatnonzero(person[y])
        if len(xs) < 2:
            continue
        lo, hi = float(xs[0]), float(xs[-1])
        span = hi - lo
        left[y], right[y] = lo + span * trim, hi - span * trim
    win = max(1, int(smooth) | 1)
    left = ndi.median_filter(left, size=win)
    right = ndi.median_filter(right, size=win)
    yy, xx = np.mgrid[0:H, 0:W]
    return hard & ((xx >= left[:, None]) & (xx <= right[:, None]))


def _row_span(hard: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """逐行左右边界：用 4/96 分位吃掉单像素毛刺，但内切量封顶 3px。

    分位数是按「该行像素」算的，行越宽切得越多 —— 1200px 人像里一行 730px
    宽的衣服，4 分位会直接内切 29px，两侧各留一条原色竖带。这里把分位数
    结果与 min/max±3px 取「更靠外」的那个，毛刺照样去掉，衣服边缘不被啃。
    """
    H = hard.shape[0]
    left = np.full(H, np.nan, dtype=float)
    right = np.full(H, np.nan, dtype=float)
    for y in range(H):
        xs = np.flatnonzero(hard[y])
        if xs.size >= 3:
            lo, hi = float(xs[0]), float(xs[-1])
            left[y] = min(float(np.percentile(xs, 4)), lo + 3.0)
            right[y] = max(float(np.percentile(xs, 96)), hi - 3.0)
    rows = np.flatnonzero(np.isfinite(left) & np.isfinite(right))
    return left, right, rows


def _smooth_edges(hard: np.ndarray, strength: float | None = None,
                  sigma_frac: float | None = None,
                  median: int | None = None) -> np.ndarray:
    """用平滑后的逐行轮廓重建覆盖区，消除衣服轮廓上的锯齿折线。

    hard 的边缘是逐像素二值轮廓，相邻行差 3~5px 就会在成图上看到"撕纸边"。
    这里取逐行 4/96 分位轮廓 → 中值去单行跳变 → 高斯低通 → 行内填充重建：
    外凸的被切回、内凹的被填平，轮廓本身仍是原衣服形状（不是矩形）。
    只填距离原蒙版 <=6px 的区域，手/道具挡出的大洞保持不动。
    重建后面积跌破 93% 视为过度平滑（肩线被整体削掉），直接放弃。
    """
    H, W = hard.shape
    strength = float(getattr(config, "CLOTH_EDGE_SMOOTH", 1.0)
                     if strength is None else strength)
    if strength <= 0 or not hard.any():
        return hard
    sigma_frac = float(getattr(config, "CLOTH_EDGE_SMOOTH_SIGMA", 0.012)
                       if sigma_frac is None else sigma_frac)
    win = int(getattr(config, "CLOTH_EDGE_SMOOTH_MEDIAN", 5)
              if median is None else median)
    left, right, rows = _row_span(hard)
    if rows.size < 8:
        return hard
    idx = np.arange(H, dtype=float)
    left = np.interp(idx, idx[rows], left[rows])
    right = np.interp(idx, idx[rows], right[rows])
    win = max(1, win | 1)
    left = ndi.median_filter(left, size=win)
    right = ndi.median_filter(right, size=win)
    sigma = max(1.2, (rows[-1] - rows[0] + 1) * sigma_frac) * min(1.0, strength)
    left = ndi.gaussian_filter1d(left, sigma)
    right = ndi.gaussian_filter1d(right, sigma)
    xx = np.arange(W, dtype=float)[None, :]
    span = (xx >= left[:, None]) & (xx <= right[:, None])
    near = ndi.binary_dilation(hard, iterations=max(1, int(round(6 * min(1.0, strength)))))
    out = span & near
    y0, y1 = int(rows[0]), int(rows[-1])
    ym = np.zeros(H, dtype=bool)
    ym[y0:y1 + 1] = True
    out &= ym[:, None]
    # 平滑后的轮廓是「贴着原轮廓内切的一条平滑线」，逐行看每边会让出 1~3px，
    # 面积自然掉几个百分点。0.93 的门槛实测把正常平滑也判成过度平滑并丢弃
    # （1200px 人像掉到 0.926，肩线一点没动、台阶全留着）。放到 0.90。
    if out.sum() < hard.sum() * 0.90:
        return hard
    return out


def _strip_outer_layer(arr: np.ndarray, hard: np.ndarray):
    """把外层外套从「上衣」里剥出来，只保留内搭（衬衫 / V 领背心）。

    ATR 18 类里没有"外套"类，西装敞开时整件（西装 + 内搭）会被判成一块
    Upper-clothes，按块铺满就抹掉了领口层次。这里用空间先验代替类别：
    外套总在躯干左右两侧，内搭总在中轴。在衣服内做 Lab 聚类，把「左带、
    右带都占得多、中轴占得少」的簇判为外层，并只剥与左右轮廓相连的连通块
    （防止把内搭上的同色图案误当外套）。剩余面积过大或过小都判定不可信，
    原样返回 —— 单色 T 恤（魔爪）走这条路不会被误伤。
    """
    info: dict[str, Any] = {"applied": False, "removed_px": 0,
                            "keep_ratio": 1.0, "detail": ""}
    H, W = hard.shape
    ys, xs = np.where(hard)
    if len(xs) < 300:
        info["detail"] = "衣服像素太少"
        return hard, info
    x0, x1 = int(xs.min()), int(xs.max())
    bw = max(1, x1 - x0 + 1)
    lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
    px = lab[hard]
    rng = np.random.default_rng(config.COLOR_SEED)
    sample = px[rng.choice(len(px), min(8000, len(px)), replace=False)]
    distinct = len(np.unique(np.round(sample, 1), axis=0))
    k = int(np.clip(config.CLOTH_LAYER_CLUSTERS, 2, max(2, distinct)))
    km = KMeans(n_clusters=k, n_init=3, random_state=config.COLOR_SEED).fit(sample)
    labels = km.predict(px)

    ew = max(2, int(bw * config.CLOTH_LAYER_EDGE_FRAC))
    cw = max(2, int(bw * config.CLOTH_LAYER_CENTER_FRAC))
    cx = 0.5 * (x0 + x1)
    left_band = xs <= x0 + ew
    right_band = xs >= x1 - ew
    center_band = np.abs(xs - cx) <= cw
    n_all = float(len(px))
    outer: list[int] = []
    for li in range(k):
        m = labels == li
        n = int(m.sum())
        if n < config.CLOTH_LAYER_MIN_SAMPLE * n_all:
            continue
        lf, rf = float(left_band[m].mean()), float(right_band[m].mean())
        cf = float(center_band[m].mean())
        if (lf >= config.CLOTH_LAYER_SIDE_MIN
                and rf >= config.CLOTH_LAYER_SIDE_MIN
                and cf <= config.CLOTH_LAYER_CENTER_MAX):
            outer.append(li)
            info["detail"] += f"簇{li}(n={n},左{lf:.2f}/右{rf:.2f}/中{cf:.2f}) "
    if not outer:
        info["detail"] = info["detail"] or "没有符合空间先验的外层色簇"
        return hard, info

    outer_mask = np.zeros((H, W), dtype=bool)
    outer_mask[hard] = np.isin(labels, outer)
    seeds = np.zeros((H, W), dtype=bool)
    for y in np.flatnonzero(hard.any(axis=1)):
        row = np.flatnonzero(hard[y])
        seeds[y, row[0]:row[0] + 2] = True
        seeds[y, row[-1] - 1:row[-1] + 1] = True
    cc, _ = ndi.label(outer_mask)
    hit = np.unique(cc[outer_mask & seeds])
    hit = hit[hit > 0]
    if hit.size == 0:
        info["detail"] += "外层色簇未与轮廓相连，放弃"
        return hard, info
    inner = hard & ~np.isin(cc, hit)
    inner = ndi.binary_fill_holes(inner)
    inner = ndi.binary_opening(inner, structure=np.ones((3, 3)))
    inner = _keep_components(inner)
    ratio = float(inner.sum()) / max(1.0, float(hard.sum()))
    info["keep_ratio"] = round(ratio, 3)
    if ratio < config.CLOTH_LAYER_MIN_KEEP or ratio > config.CLOTH_LAYER_MAX_KEEP:
        info["detail"] += f"→ 剩余 {ratio:.0%}，判为误判，放弃剥离"
        return hard, info
    info["applied"] = True
    info["removed_px"] = int(hard.sum()) - int(inner.sum())
    info["detail"] += f"→ 剩余 {ratio:.0%}，剥掉 {info['removed_px']} px"
    return inner, info


_semantic_skip_reason: str | None = None


def _keep_components(mask: np.ndarray) -> np.ndarray:
    """保留主体连通域 + 与主体相邻且足够大的域。

    SegFormer 输出是 128x128 放大来的，偶尔会在背景上撒几个"上衣"碎块；
    区域生长会把它们放大成显眼的竖条。这里按面积和空间距离把它们剔掉，
    同时保住与躯干分离的袖子（离得近、面积够大）。
    """
    cc, n = ndi.label(mask)
    if n <= 1:
        return mask
    sizes = np.bincount(cc.ravel())
    sizes[0] = 0
    main = int(np.argmax(sizes))
    near = ndi.binary_dilation(cc == main,
                               iterations=config.HUMAN_COMPONENT_NEAR)
    touched = np.unique(cc[near])
    touched = touched[touched > 0]
    keep = [main]
    for i in touched:
        if i == main or sizes[i] < config.HUMAN_COMPONENT_MIN_RATIO * sizes[main]:
            continue
        keep.append(int(i))
        if len(keep) >= config.HUMAN_KEEP_COMPONENTS:
            break
    return np.isin(cc, keep)


def _fill_holes_keep_gaps(hard: np.ndarray, arr: np.ndarray,
                          bg_rgb: np.ndarray, bg_ok: bool,
                          skin: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """填衣服内部的号码/字母洞，但不许把「手臂与躯干之间的背景缝」填平。

    号码/字母是完全封闭的洞；臂-身缝隙是**通到画面下缘的开口**。早先为了让
    压在底边的号码也成洞，先补两行白再 fill_holes —— 副作用是这条缝被一并
    封口填满，换装后手臂直接粘在躯干上（黄仁勋那张黑皮衣最明显，两侧各一条）。
    所以拆成两步：封闭洞照填；「只因为补底才被封」的那部分里，背景色与肤色
    像素一律不填 —— 它们本来就是外部背景，不是衣服。
    返回 (掩版, 被保留为洞的缝隙)，后者要交给轮廓平滑阶段扣回去。
    """
    H, W = hard.shape
    base = ndi.binary_fill_holes(hard)
    pad = ndi.binary_fill_holes(np.vstack([hard, np.ones((2, W), dtype=bool)]))[:H]
    extra = pad & ~base
    gap = np.zeros((H, W), dtype=bool)
    if extra.any():
        bg_like = (np.linalg.norm(np.asarray(arr, dtype=float) - bg_rgb, axis=2)
                   < config.CLOTH_BG_DISTANCE) if bg_ok else np.zeros((H, W), dtype=bool)
        keep_out = bg_like | np.asarray(skin, dtype=bool)
        gap = extra & keep_out
        extra = extra & ~keep_out
    return (base | extra), gap


def _bg_gap_mask(arr: np.ndarray, hard: np.ndarray,
                 bg_rgb: np.ndarray, bg_ok: bool) -> np.ndarray:
    """找出「被衣服掩版吞掉的背景缝」（手臂与躯干之间那一条）。

    语义分割是在 128x128 的网格上做的，几像素宽的缝会被直接判成人；缝上方
    只有 1~2px、下方张到十几像素，模型稳定漏掉。这类缝在换装时会变成一整块
    布料，手臂就粘在躯干上了。

    只能用图像自身的证据捞回来：颜色接近背景色，**而且与画面边界是连通的**
    （缝向上下都开口，通到外部背景）。衣服上的蓝色印花虽然也接近背景色，但
    是封闭的连通块，碰不到边界，所以不会被误吃。
    阈值分两档：seed 用衣服掩版同款的距离算「确定是背景」，再放宽一档把缝里
    投影造成的偏暗像素也并进来；只有同时含 seed 与边界的连通块才算外部背景。
    """
    out = np.zeros(hard.shape, dtype=bool)
    if not bg_ok:
        return out
    d = np.linalg.norm(np.asarray(arr, dtype=float) - bg_rgb, axis=2)
    seed = d < config.CLOTH_BG_DISTANCE
    cand = d < max(config.CLOTH_BG_DISTANCE * 2.2, 90.0)
    lab, n = ndi.label(cand)
    if n == 0:
        return out
    border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    ids = np.unique(border[border > 0])
    if ids.size == 0:
        return out
    for i in ids:
        blk = lab == i
        if float((seed & blk).mean()) < 0.02:   # 整块几乎不含纯背景色 → 不是背景
            continue
        out |= blk
    out &= hard
    if out.any():
        # 缝是纵向细条：竖向闭合补掉投影打断的小豁口，竖向开运算切掉孤点。
        # 只用 (n,1) 结构元，横向宽度保持原样，不会把缝撑开。
        out = ndi.binary_closing(out, np.ones((9, 1))) & hard
        out = ndi.binary_opening(out, np.ones((5, 1)))
    return out


def _semantic_segment(im: Image.Image, arr: np.ndarray, skin: np.ndarray,
                      bg_rgb: np.ndarray, bg_ok: bool,
                      exclude_skin: bool,
                      layer_split: bool = True,
                      edge_smooth: bool = True,
                      sleeve_trim: float | None = None,
                      edge_grow: int | None = None) -> dict[str, Any] | None:
    """语义解析路径：模型定「整体范围」，色调逻辑定「印花/号码/滚边细节」。

    返回与 segment() 完全同结构的结果；模型不可用、没认出人或结果异常时
    返回 None，并把原因写进 _semantic_skip_reason，由调用方回退到色调启发式。
    """
    global _semantic_skip_reason
    info = human.parse(im)
    if info is None:
        _semantic_skip_reason = "模型不可用（缺权重/依赖或推理失败）"
        return None
    H, W = arr.shape[:2]
    person_cov = float(info["person"].mean())
    upper = np.asarray(info["upper"], dtype=bool)
    arm_trim = np.zeros((H, W), dtype=bool)
    if exclude_skin:
        labels = info["labels"]
        # 手臂排除：模型的 Left-arm/Right-arm(14/15) 是语义级掩版，比 RGB 肤色判据
        # 可靠得多 —— 深肤色/阴影里的手臂 skin_mask 会漏检，换装蒙版因此溢到手臂
        # （科比深棕臂、和其正红背心贴红墙两例实测）。全库验证 arm∩upper 恒为 0，
        # 两类互斥，并入 skin 不会误伤上衣（含长袖的袖子）。
        if getattr(config, "CLOTH_ARM_EXCLUDE", True):
            arm = np.isin(labels, (14, 15))
            if arm.any():
                # 无袖球衣的肩带滚边常被模型算进 arm；整条 arm 都排除会在换装后
                # 留一圈原色边（科比红背心换雪碧，肩头留红线）。只豁免紧贴
                # upper、且颜色仍属于上衣（或近白滚边）的 arm 像素。
                if getattr(config, "CLOTH_ARM_TRIM_SPARE", True) and upper.any():
                    lab_full = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
                    ref_upper = np.median(lab_full[upper], axis=0)
                    dist_upper = np.linalg.norm(lab_full - ref_upper, axis=2)
                    near_upper = ndi.binary_dilation(
                        upper, iterations=max(1, int(config.CLOTH_ARM_TRIM_DILATE)))
                    trim = arm & near_upper & (
                        dist_upper < float(config.CLOTH_ARM_TRIM_DIST))
                    neutral = np.hypot(lab_full[..., 1], lab_full[..., 2])
                    trim |= (arm & near_upper
                             & (lab_full[..., 0] > float(config.CLOTH_ARM_TRIM_L))
                             & (neutral < float(config.CLOTH_ARM_TRIM_NEUTRAL)))
                    # 拼色肩块（和其正米色肩片）颜色离主色很远，但语义概率仍
                    # 明显高于真手臂，用 upper_prob 再捞回这一类误标。
                    trim |= (arm & near_upper
                             & (np.asarray(info["upper_prob"], dtype=float)
                                >= float(config.CLOTH_ARM_TRIM_PROB)))
                    arm_trim = trim
                # trim 既不该留在 arm 里，也不该留在 RGB 肤色判据里：
                # 米色拼块/金色滚边都会被 skin_mask 误判成皮肤。
                skin = (skin | arm) & ~arm_trim
        # 肤色豁免：金色/暖色衣服（红牛金 T 恤、阿萨奶茶、蜜桃乌龙茶）的 RGB
        # 落在肤色判据区间，被 skin_mask 误判成皮肤而扣掉，换装后只剩中间图案、
        # 大片袖子肩膀露原色 —— 即「覆盖不完全」。模型语义级 upper 明确判为衣服，
        # 应从 skin 中豁免；脸(11)/手臂(14/15)不在 upper，安全。中位豁免只占 upper
        # 的 3.4%，仅对少数暖色衣生效。
        if getattr(config, "CLOTH_SKIN_SPARE_UPPER", True):
            skin = skin & ~upper
    raw_cov = float(upper.mean())
    # 模型在插画/纯色示意图/远景上会把整片衣服判成 Background，
    # 此时它的"人像"面积会塌掉 —— 不能硬信，直接退回色调引擎。
    if person_cov < config.HUMAN_MIN_PERSON:
        _semantic_skip_reason = f"模型未识别出足够大的人像（{person_cov:.1%}）"
        return None
    if raw_cov < config.HUMAN_MIN_COVERAGE or raw_cov > config.HUMAN_MAX_COVERAGE:
        _semantic_skip_reason = f"模型上衣占比异常（{raw_cov:.1%}）"
        return None
    if raw_cov / max(1e-6, person_cov) < config.HUMAN_MIN_UPPER_RATIO:
        _semantic_skip_reason = "模型没能在人像内定位到上衣"
        return None
    upper = _keep_components(upper)     # 先丢掉背景上的碎块，再谈扩张
    # 号码/字母/网眼/印花是衣服上的洞；再补两行填掉压到画面底边的号码。
    # 臂-身缝隙也是「通到底边的开口」，绝不能跟着补底一起填（手臂会粘住躯干）。
    hard, gap_holes = _fill_holes_keep_gaps(upper, arr, bg_rgb, bg_ok, skin)
    hard = _semantic_expand(arr, hard, info, skin, exclude_skin)
    hard = _morph(hard, "binary_closing", np.ones((3, 3)))
    # 肩带滚边/拼色肩片：标签被判成 arm，但语义概率与空间位置都说明属于上衣。
    # 直接并回硬掩版，后续平滑和缝隙扣除仍会照常约束它。
    if arm_trim.any():
        hard |= arm_trim
    hard = _keep_components(hard)       # 再清一次，挡住被区域生长放大的伪影
    hard &= ~info["background"]
    if exclude_skin:
        hard &= ~skin
    hard = _morph(hard, "binary_opening", np.ones((3, 3)))
    # 语义模型在 128x128 网格上做分割，几像素宽的「手臂—躯干」缝会被整条判成
    # 人；这里用「背景色 + 与画面边界连通」把缝挖回来。占比过大说明判据跑偏
    # （衣服本身接近背景色），宁可不扣，保持原样。
    bg_gap = _bg_gap_mask(arr, hard, bg_rgb, bg_ok)
    if bg_gap.any() and float(bg_gap.mean()) <= 0.15 * float(hard.mean()):
        hard &= ~bg_gap
    else:
        bg_gap = np.zeros_like(hard)

    # 贴图取景必须用「收窄前」的衣服框：袖子排除只应改变覆盖区域，不应改变
    # 标签的缩放与取景。否则 bbox 变窄 → tw 变小 → win_h 变大 → y0 被压到 0，
    # 罐顶的银白金属边会被拉近领口，在脖子下方形成一整块白斑（基准 01 魔爪
    # 实测：bw 347→222，y0 139→0，领口采样行从 full 的第 118 行跳到第 9 行）。
    wrap_hard = hard.copy()
    wy, wx = np.where(wrap_hard)
    wrap_bbox = ([int(wx.min()), int(wy.min()), int(wx.max()) + 1, int(wy.max()) + 1]
                 if len(wx) else [0, 0, W, H])

    # 袖子/上臂排除：放在所有扩张、减法之后，避免被区域生长重新长回去
    # 多层着装先剥外层外套：剥成功说明已精确停在「内搭」上，再按人像轮廓
    # 收窄只会把内搭两侧和主字一起削掉，所以此时跳过 trim。
    layer_info: dict[str, Any] = {"applied": False, "removed_px": 0,
                                  "keep_ratio": 1.0, "detail": "已关闭"}
    if layer_split:
        hard, layer_info = _strip_outer_layer(arr, hard)
    smoothed = 0
    # 领口洞要单独记住：轮廓重建按逐行 left..right 填充，会把它一起填平。
    # 不能用「原始肤色掩版」再扣一次 —— 那是 128x128 分割网格的阶梯边界，
    # 扣回去等于把刚平滑掉的锯齿又贴回来（实测肩线出现 9px 见方的台阶）。
    neck_hole = np.zeros_like(hard)
    if exclude_skin and hard.any():
        neck_hole = (ndi.binary_fill_holes(hard) & ~hard
                     & ndi.binary_dilation(skin, iterations=3))
    # 臂-身缝隙同样会在轮廓重建 / 区域生长时被填平，一起记住。
    # 这里不与 hard 求交：缝隙在 _semantic_expand 阶段就已被吞进衣服里，
    # 求交会得到空集，等于没扣。判据本身已经很保守 —— 只有「靠补底才被封住
    # 的连通区域，且像素是背景色/肤色」才算缝，衣服内部封闭的号码洞进不来。
    gap_keep = gap_holes | bg_gap

    def _resmooth(h: np.ndarray) -> tuple[np.ndarray, int]:
        """平滑逐行轮廓，再把领口洞与臂-身缝隙扣回来（重建会把洞填平）。"""
        before = h.copy()
        h2 = _smooth_edges(h)
        if neck_hole.any():
            sigma = max(2.0, h.shape[0] * 0.008)
            hole = ndi.gaussian_filter(neck_hole.astype(np.float32), sigma) > 0.5
            hole = ndi.binary_closing(hole, np.ones((5, 5)))
            h2 &= ~hole
        if gap_keep.any():
            # 竖向闭合补掉缝里被打断的窄段；不用 5x5 方窗，那会把缝横向扩宽
            gap = ndi.binary_closing(gap_keep, np.ones((9, 1)))
            h2 &= ~gap
        return h2, int(np.count_nonzero(h2 != before))

    if edge_smooth:
        hard, smoothed = _resmooth(hard)

    sleeves_trimmed = 0
    trim = float(getattr(config, "CLOTH_SLEEVE_TRIM", 0.0)
                 if sleeve_trim is None else sleeve_trim)
    if layer_info["applied"]:
        trim = 0.0
    if trim > 0:
        before = int(hard.sum())
        hard = _trim_sleeves(hard, info["person"], trim,
                             int(getattr(config, "CLOTH_SLEEVE_TRIM_SMOOTH", 11)))
        sleeves_trimmed = before - int(hard.sum())
    coverage = float(hard.mean())
    if coverage < config.CLOTH_MIN_COVERAGE or coverage > config.HUMAN_MAX_COVERAGE:
        _semantic_skip_reason = f"修补后衣服占比异常（{coverage:.1%}）"
        return None
    px = arr[hard]
    if len(px) < 50:
        _semantic_skip_reason = "修补后衣服像素过少"
        return None
    step = max(1, len(px) // 12000)
    ref = np.median(color.rgb_to_lab(px[::step]), axis=0)
    # 轮廓外窄带再吸一次「同色但被阴影压暗」的像素：腋下、侧缝、袖口这些地方
    # 语义模型常判成背景，换装在衣服边缘就留一条原色细边。彩度只看 a/b，
    # 与明度无关，所以阴影里也认得出是同一块布；背景色与肤色仍然排除。
    # 默认不做轮廓外扩张（保持贴图/平涂档的历史行为）；换装档显式传入宽度，
    # 因为整件替换时外缘那条「阴影 + 侧条」过渡带不盖住就会留一圈原色描边。
    grow_w = 0 if edge_grow is None else max(0, int(edge_grow))
    edge_grown = 0
    if grow_w > 0:
        lab = color.rgb_to_lab(arr.reshape(-1, 3)).reshape(H, W, 3)
        chroma = np.hypot(lab[..., 1] - ref[1], lab[..., 2] - ref[2])
        neutral = np.hypot(lab[..., 1], lab[..., 2])
        bg_here = (np.linalg.norm(arr - bg_rgb, axis=2) < config.CLOTH_BG_DISTANCE
                   if bg_ok else np.zeros((H, W), dtype=bool))
        skin_here = skin if exclude_skin else np.zeros((H, W), dtype=bool)
        # 外缘常常是一条「阴影 + 白色侧条」的过渡带，宽度可能到十几像素，
        # 单次 4px 吃不下，所以迭代几轮；每轮都重新以当前轮廓为起点，
        # 一旦没有新像素或占比异常就停。
        before_px = int(np.count_nonzero(hard))
        for _ in range(int(getattr(config, "CLOTH_EDGE_GROW_ITERS", 3))):
            band = ndi.binary_dilation(hard, iterations=grow_w) & ~hard
            edge = (band & (chroma < float(getattr(config, "CLOTH_EDGE_GROW_CHROMA", 14.0)))
                    & ~bg_here & ~skin_here)
            # 球衣两侧常有「白底 + 彩色描边」的拼接条，白色部分彩度是中性的，
            # 靠色度判据永远吸不进来，换装后就在身侧留一条白边。
            edge |= band & (neutral < 10.0) & ~bg_here & ~skin_here
            if not edge.any():
                break
            grown = _morph(hard | edge, "binary_closing", np.ones((3, 3)))
            if grown.sum() <= hard.sum():
                break
            if float(grown.mean()) > config.HUMAN_MAX_COVERAGE:
                break
            hard = grown
        # 轮廓外扩张是逐像素按颜色判定的，会把远处「与衣服同色」的小块（背景墙
        # 上的红影、手臂旁的黄斑）也连进来，形成与主体完全分离的绿色孤岛 —— 
        # 换装后像贴纸浮在手臂上（和其正贴红墙、科比黄球衣实测）。扩张后再清
        # 一次连通块，剔除远离主体的小碎片；袖子/肩带与主块相连，不受影响。
        hard = _keep_components(hard)
        # 扩张是逐像素按颜色判定的，边界会重新变得毛糙（肩线/袖口出现细碎
        # 凸起，成图上就是「撕纸边」），所以扩张后再走一遍轮廓平滑。
        if edge_smooth and edge_grown:
            hard, _ = _resmooth(hard)
        edge_grown = int(np.count_nonzero(hard)) - before_px
        coverage = float(hard.mean())
    # 兜底：无论中间哪一步把背景缝吃了，最终掩版都要把它让出来
    if gap_keep.any():
        hard = hard & ~ndi.binary_closing(gap_keep, np.ones((9, 1)))
        coverage = float(hard.mean())
    soft = _soft_from_hard(hard)
    yy, xx = np.where(hard)
    bbox = ([int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]
            if len(xx) else [0, 0, W, H])
    neck, guessed = _neckline_from_mask(hard)
    return {
        "mask": soft,
        "hard": hard,
        "bbox": bbox,
        # 专供 wrap 的取景几何（收窄前）；缺失时调用方回退到 bbox/hard
        "wrap_bbox": wrap_bbox,
        "wrap_hard": wrap_hard,
        "safe_box": _safe_box(hard, neck),
        "coverage": round(coverage, 4),
        "soft_coverage": round(float((soft > 0.35).mean()), 4),
        "cloth_hex": color.rgb_to_hex(color.lab_to_rgb(ref)),
        "background_hex": color.rgb_to_hex(bg_rgb) if bg_ok else None,
        "neckline_y": neck,
        "neckline_guessed": guessed,
        "fallback": False,
        "method": "human-parse-v1",
        "image_size": [W, H],
        "clusters": 0,
        "engine": "model",
        "model": info["model"],
        "semantic_coverage": round(raw_cov, 4),
        "person_coverage": round(person_cov, 4),
        "sleeves_trimmed": sleeves_trimmed,
        "layer_split": bool(layer_info["applied"]),
        "layer_removed_px": int(layer_info["removed_px"]),
        "layer_keep_ratio": float(layer_info["keep_ratio"]),
        "layer_detail": layer_info["detail"],
        "edge_smoothed_px": smoothed,
        "edge_grown_px": edge_grown,
        # 内部用：臂-身背景缝，覆盖阶段不许把它填实（不进 JSON）
        "gap": gap_keep,
    }


def segment(source, box: tuple[float, float, float, float] | None = None,
            exclude_skin: bool = True, exclude_bg: bool = True,
            engine: str | None = None,
            layer_split: bool | None = None,
            edge_smooth: bool | None = None,
            sleeve_trim: float | None = None,
            edge_grow: int | None = None) -> dict[str, Any]:
    """返回衣服区域分割结果。

    mask: 0..1 软蒙版（整图大小）；hard: 布尔硬蒙版；bbox / safe_box 为像素坐标；
    cloth_hex: 识别出的衣服种子色调；fallback: 是否回退到矩形 ROI 兜底。
    engine: auto（模型优先，失败降级）/ model / tone（只用色调启发式）。
    layer_split: 多层着装剥外层（None=读 config）；edge_smooth: 轮廓平滑（同上）。
    sleeve_trim: 袖子收窄比例（None=读 config）。换装（garment）档传 0 —— 基准
        衣服是整件球衣，肩带/袖口本身就是衣服，收窄会在两肩留下原色条。
    edge_grow: 轮廓外同色扩张宽度（0/None=关闭）。只有整件替换（garment）需要，
        贴图档保持历史行为不动，否则改动会溢出到轮廓外。
    导出基准资产时必须都传 False —— 源衣服是"素材"，不该被目标端规则改写。
    """
    global _semantic_skip_reason
    _semantic_skip_reason = None
    do_layer = bool(config.CLOTH_LAYER_SPLIT if layer_split is None else layer_split)
    do_edge = bool(config.CLOTH_EDGE_SMOOTH if edge_smooth is None else edge_smooth)
    im = load_image(source)
    arr = np.asarray(im, dtype=float)
    H, W, _ = arr.shape
    roi = box or config.PORTRAIT_DEFAULT_BOX
    hint = _hint_mask((H, W), roi)
    bg_rgb, bg_ok = background_color(arr)
    bg_lab = np.asarray(color.hex_to_lab(color.rgb_to_hex(bg_rgb)), dtype=float)
    bg_like = (np.linalg.norm(arr - bg_rgb, axis=2) < config.CLOTH_BG_DISTANCE) \
        if (exclude_bg and bg_ok) else np.zeros((H, W), dtype=bool)
    skin = skin_mask(arr) if exclude_skin else np.zeros((H, W), dtype=bool)

    mode = str(engine or config.HUMAN_SEGMENT_PRIORITY or "auto").lower()
    skip_note = None
    if mode in ("auto", "model"):
        sem = _semantic_segment(im, arr, skin, bg_rgb, bg_ok, exclude_skin,
                                layer_split=do_layer, edge_smooth=do_edge,
                                sleeve_trim=sleeve_trim, edge_grow=edge_grow)
        if sem is not None:
            return sem
        skip_note = _semantic_skip_reason or "模型解析结果异常"
    elif mode == "tone":
        skip_note = "已手动指定色调引擎"

    labels, centers = _tone_labels(arr)
    bg_ids: set[int] = set()
    skin_ids: set[int] = set()
    for i, c in enumerate(centers):
        m = labels == i
        if not m.any():
            continue
        frac_bg = float(bg_like[m].mean())
        frac_skin = float(skin[m].mean())
        if exclude_bg and (frac_bg > config.CLOTH_BG_CLUSTER_RATIO
                           or np.linalg.norm(c - bg_lab) < config.CLOTH_BG_LAB_DISTANCE):
            bg_ids.add(i)
        elif exclude_skin and (frac_skin > config.CLOTH_SKIN_CLUSTER_RATIO
                              or (c[0] < 55 and c[1] > 4 and 4 < c[2] < 48
                                  and frac_skin > 0.08)):
            skin_ids.add(i)

    candidates = [i for i in range(len(centers)) if i not in bg_ids | skin_ids]
    scores = {i: int((labels == i)[hint].sum()) for i in candidates}
    fallback = not candidates or not scores or max(scores.values()) < 30

    if fallback:
        # 用提示区里非皮肤/非背景像素的 Lab 中位数当衣服色
        px = arr[hint & ~skin & ~bg_like]
        ref = np.median(color.rgb_to_lab(px), axis=0) if len(px) \
            else np.asarray(color.hex_to_lab("#888888"))
        soft = _legacy_mask(arr, ref, roi, skin, bg_like)
        hard = soft > 0.45
        cloth_hex = color.rgb_to_hex(color.lab_to_rgb(ref))
        neckline, guessed = None, False
        method = "roi-lab-distance-fallback"
    else:
        main_id = max(scores, key=lambda i: scores[i])
        ref = centers[main_id]
        cloth_ids = [i for i in candidates
                     if np.hypot(centers[i][1] - ref[1], centers[i][2] - ref[2])
                     <= config.CLOTH_CHROMA_MERGE]
        seed = np.isin(labels, cloth_ids)
        hard, neckline, guessed = _envelope(seed, bg_like)
        # 领口/肩线上的滚边、白色侧条与衣服色调不同，会留在包络外形成原色细线
        hard = _edge_refine(arr, hard, ref, skin, bg_like, exclude_skin)
        coverage = float(hard.mean())
        if coverage < config.CLOTH_MIN_COVERAGE or coverage > config.CLOTH_MAX_COVERAGE:
            soft = _legacy_mask(arr, ref, roi, skin, bg_like)
            hard = soft > 0.45
            fallback, method = True, "roi-lab-distance-fallback"
        else:
            soft = _soft_from_hard(hard)  # 边缘复制补齐 + 窄高斯羽化
            method = "cloth-tone-v2"
        cloth_hex = color.rgb_to_hex(color.lab_to_rgb(ref))

    yy, xx = np.where(hard)
    bbox = ([int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]
            if len(xx) else [0, 0, W, H])
    result = {
        "mask": soft,
        "hard": hard,
        "bbox": bbox,
        "safe_box": _safe_box(hard, neckline),
        "coverage": round(float(hard.mean()), 4),
        "soft_coverage": round(float((soft > 0.35).mean()), 4),
        "cloth_hex": cloth_hex,
        "background_hex": color.rgb_to_hex(bg_rgb) if bg_ok else None,
        "neckline_y": neckline,
        "neckline_guessed": guessed,
        "fallback": bool(fallback),
        "method": method,
        "image_size": [W, H],
        "clusters": len(centers),
        "engine": "tone",
    }
    if skip_note:
        result["semantic_note"] = skip_note
    return result


def palette_from_mask(arr: np.ndarray, mask: np.ndarray,
                      n_colors: int | None = None) -> list[dict[str, Any]]:
    """只在衣服区域内提色：号码/描边等印花也计入色板，但皮肤和背景不会混入。"""
    sel = mask > 0.55
    pixels = arr[sel]
    weights = mask[sel]
    if len(pixels) < 50:
        pixels, weights = arr.reshape(-1, 3), np.ones(arr.shape[0] * arr.shape[1])
    if len(pixels) > config.COLOR_SAMPLES:
        idx = np.random.default_rng(config.COLOR_SEED).choice(
            len(pixels), config.COLOR_SAMPLES, replace=False)
        pixels, weights = pixels[idx], weights[idx]
    lab = color.rgb_to_lab(pixels)
    distinct = len(np.unique(np.round(lab, 3), axis=0))
    k = min(n_colors or config.PORTRAIT_COLORS, max(1, len(pixels) // 200), distinct)
    km = KMeans(n_clusters=k, n_init=3, random_state=config.COLOR_SEED)
    km.fit(lab, sample_weight=weights)
    counts = np.bincount(km.labels_, weights=weights, minlength=k)
    ratios = counts / max(1e-9, counts.sum())
    out = []
    for i in range(k):
        center = km.cluster_centers_[i]
        rgb = color.lab_to_rgb(center)
        sat = max(rgb) - min(rgb)
        out.append({
            "hex": color.rgb_to_hex(rgb),
            "lab": [round(float(v), 2) for v in center],
            "ratio": round(float(ratios[i]), 4),
            "score": round(float(ratios[i] * (0.80 + 0.20 * min(1.0, sat / 120.0))), 4),
        })
    out.sort(key=lambda c: -c["score"])
    return [c for c in out if c["ratio"] >= config.MIN_COLOR_RATIO] or out[:1]


def outline(hard: np.ndarray) -> np.ndarray:
    """硬蒙版的内边缘，供 debug 图描边。"""
    if not hard.any():
        return hard
    inner = ndi.binary_erosion(hard, structure=np.ones((3, 3)), iterations=1)
    return hard & ~inner
