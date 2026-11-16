"""全局配置：路径常量、网络礼仪、颜色算法参数。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
HD_IMAGE_DIR = DATA_DIR / "images_hd"   # R2 离线超分素材；删掉此目录即回退原图
GARMENT_DIR = DATA_DIR / "garments"     # 基准成品图切出的衣服资产（strip + 逐行轮廓）
# 新库：每个品牌 = 一张「已经换好装的人像」，衣服直接从人像上切（wardrobe.py）。
# 它同时是 garment.build_texture 的数据源，所以目录结构一致（strip + profile + meta）。
WARDROBE_DIR = DATA_DIR / "wardrobe"
WARDROBE_MAX_SIDE = 760        # 衣服 assets 存盘最长边（超分后按此上限压缩体积）
WARDROBE_UPSCALE_SHORT = 260   # 衣服裁剪短边低于此值时做 4x 超分（基准图普遍 130~250px）
# 条带取样区间逐行向内收窄的比例。衣服最外缘 2~5px 是「衣服色 × 背景」的抗锯齿
# 混合，还压着轮廓阴影；取样区间贴到最外缘，纹理就自带一圈暗边，贴到新衣服上
# 会表现为肩线/腋下的一排深色锯齿。0.05 收掉这圈边，图案主体不受影响。
WARDROBE_SHRINK = 0.05
INBOX_DIR = DATA_DIR / "inbox"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "catalog.db"
CONFIG_DIR = ROOT / "config"
REPORT_DIR = ROOT / "report"
BRANDS_FILE = CONFIG_DIR / "brands.yaml"
MANUAL_URLS_FILE = CONFIG_DIR / "manual_urls.yaml"
OFFICIAL_SITES_FILE = CONFIG_DIR / "official_sites.yaml"
SEED_COLORS_FILE = CONFIG_DIR / "seed_colors.yaml"
ATTRIBUTION_FILE = ROOT / "ATTRIBUTION.md"
WEB_DIR = ROOT / "web"
MODELS_DIR = ROOT / "models"
HUMAN_MODEL_FILE = MODELS_DIR / "segformer_clothes.onnx"
DEPTH_MODEL_FILE = MODELS_DIR / "depth_anything_v2_small.onnx"
MATCH_RESULT_JSON = REPORT_DIR / "match_result.json"
MATCH_RESULT_HTML = REPORT_DIR / "match_result.html"
COMPOSE_IMAGE = REPORT_DIR / "composite.png"

# Commons / OFF 要求可识别的 User-Agent；已指向本仓库地址。
USER_AGENT = (
    "BeverageColorDB/0.1 "
    "(non-commercial educational project; +https://github.com/linj95833-ops/yinliao-color-db)"
)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OFF_PRODUCT_API = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_SEARCH_API = "https://{host}/api/v2/search"
OFF_SEARCH_HOSTS = ("world.openfoodfacts.org", "us.openfoodfacts.org", "fr.openfoodfacts.org")
OFF_LICENSE = "CC BY-SA 3.0"
OFF_ATTRIBUTION = "Open Food Facts contributors (https://world.openfoodfacts.org)"

# OFF search 限流很严：实测 page_size>5 或高频请求立刻 503。
# 因此小分页 + 单并发 + 域名间隔 + 指数退避 + 检索结果本地缓存。
OFF_SEARCH_PAGE_SIZE = 5
OFF_SEARCH_PAGES = 3
OFF_SEARCH_DELAY = 1.3
OFF_SEARCH_RETRIES = 5
OFF_CACHE_TTL_DAYS = 7
# 实测 OFF 对国内品牌几乎零收录（王老吉/元气森林/东鹏/蜜雪均为 0），
# 不把宝贵的限流额度浪费在必然空结果的检索上；国内走官网 + inbox 兜底。
OFF_SKIP_CN_SEARCH = True

# 品牌官网抓取：遵守 robots.txt、可识别 UA、每域名限速、页面缓存
OFFICIAL_DELAY = 1.5
OFFICIAL_MAX_IMAGES_PER_PAGE = 30
OFFICIAL_LICENSE = (
    "OFFICIAL-SITE — 图片版权归品牌方所有；本项目仅非商用娱乐研究参考，"
    "不随仓库分发二进制，使用前请自行确认授权"
)
OFFICIAL_ATTRIBUTION = "品牌官方网站（见 source_page）"

# 下载策略：低并发 + 每域名限速 + 指数退避，避免给公共服务造成压力
CONCURRENCY = 6
PER_HOST_DELAY = 0.4
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
RETRY_BACKOFF = 1.6
MAX_IMAGES_PER_BRAND = 5
# 先多下载候选，再按「包装图质量分」择优保留 MAX_IMAGES_PER_BRAND 张。
# 从「先到先得」改成「择优录取」，避免随手拍/宣传图占掉名额。
CANDIDATE_FACTOR = 3

# 图片质量门槛（产品包装实物图）
MIN_IMAGE_BYTES = 3000
MIN_SHORT_EDGE = 120
MIN_LONG_EDGE = 300
MAX_ASPECT_RATIO = 3.2
ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})


def size_ok(w: int, h: int) -> bool:
    """罐/瓶正面图常为瘦长比例，用短边 + 长边双门槛，避免误杀 151x400 的标准包装图。"""
    if w <= 0 or h <= 0:
        return False
    short, long_ = min(w, h), max(w, h)
    return (short >= MIN_SHORT_EDGE and long_ >= MIN_LONG_EDGE
            and long_ / short <= MAX_ASPECT_RATIO)


# 颜色算法
COLOR_SAMPLES = 12000
N_COLORS = 5
MIN_COLOR_RATIO = 0.03
COLOR_SEED = 0
PHASH_MATRIX = 32
PHASH_LOW = 8
PHASH_HAMMING_MAX = 8  # 同品牌内视为重复的汉明距离阈值

# 手动填图「采信模式」：用户放进目录的图一律视为正确，不跑质检闸门。
SCAN_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
USER_IMAGE_SOURCE = "user"
USER_IMAGE_LICENSE = "USER_PROVIDED — 使用者自行提供并确认授权"
USER_IMAGE_ATTRIBUTION = "使用者本地提供（手动填图）"
SEED_PROFILE_CONFIDENCE = 0.25

# 人像衣服颜色识别（启发式，零新增依赖）
PORTRAIT_MAX_SIDE = 1200
PORTRAIT_DEFAULT_BOX = (0.25, 0.28, 0.75, 0.88)  # 归一化 x1,y1,x2,y2：仅作「取衣服颜色」的提示区
PORTRAIT_BG_DISTANCE = 45.0   # 与四角背景色距离小于该值的像素降权
PORTRAIT_SKIN_WEIGHT = 0.04   # 肤色像素权重
PORTRAIT_BG_WEIGHT = 0.08     # 背景像素权重
PORTRAIT_NEUTRAL_WEIGHT = 0.45  # 近白/近黑像素权重（黑白衣服仍可识别）
PORTRAIT_COLORS = 5

# 衣服区域分割（按色调，不假设人体是矩形）
CLOTH_CLUSTERS = 8             # 全图 Lab 色调聚类数
CLOTH_SAMPLE_PIXELS = 20000    # 拟合聚类时的抽样像素上限
CLOTH_BORDER_FRAC = 0.06       # 四边框取样厚度（估计背景主色）
CLOTH_BG_QUANT = 24            # 边框颜色量化步长
CLOTH_BG_MIN_BORDER_FRAC = 0.12  # 主色占比低于此值视为「复杂背景」，不做背景剔除
CLOTH_BG_DISTANCE = 58.0       # 与背景主色的 RGB 距离阈值
CLOTH_BG_LAB_DISTANCE = 26.0   # 色调簇中心到背景色的 Lab 距离阈值
CLOTH_BG_CLUSTER_RATIO = 0.45  # 簇内背景像素占比超过此值 → 背景簇
CLOTH_SKIN_CLUSTER_RATIO = 0.30  # 簇内肤色种子占比超过此值 → 皮肤簇
CLOTH_CHROMA_MERGE = 26.0      # 色度(a,b)距离小于此值视为同一件衣服的高光/阴影
CLOTH_NECK_CENTER = (0.42, 0.58)  # 检测领口的中央竖条
CLOTH_NECK_MIN_FRAC = 0.75     # 中央竖条被衣服填满的比例
CLOTH_RUN_GAP = 3              # 领口以上：布料段之间超过此间隔就断开
CLOTH_MIN_RUN = 2              # 领口以上：至少多长的连续布料段才算衣服
CLOTH_VERTICAL_CLOSE_FRAC = 0.03  # 竖向形态学封口半径（相对短边）
CLOTH_MIN_COVERAGE = 0.02      # 衣服占比异常 → 回退矩形 ROI 蒙版
CLOTH_MAX_COVERAGE = 0.60
CLOTH_SOFT_BLUR_FRAC = 0.003   # 边缘羽化半径（相对短边），保持很窄以免留原色金边
CLOTH_SOFT_GAIN = 2.6          # 羽化后放大，保证衣服内部为完全覆盖
# 轮廓外一圈「滚边/侧条」补回：只在紧贴 mask 的窄带里按色调判断，避免染到皮肤
CLOTH_EDGE_DILATE = 3          # 轮廓外搜索宽度（像素）
CLOTH_EDGE_CHROMA = 42.0       # 与衣服色调的色度距离上限
CLOTH_EDGE_DARK_CHROMA = 48.0  # 深色描边允许的色度距离
CLOTH_EDGE_BRIGHT_L = 52.0     # 高于此明度视为亮色滚边（而非深色皮肤）
CLOTH_EDGE_PANEL_L = 68.0      # 近白侧条/号码边缘的最低明度
CLOTH_EDGE_NEUTRAL = 30.0      # 近白侧条的最大色度
# 语义模型会在「腋下 / 侧缝 / 袖口」这些被阴影压暗的地方判成背景或非上衣，
# 换装后就在衣服边缘留一圈原色细条（雪碧换王老吉时可量到 1000+ 像素）。
# 这里把紧贴轮廓、彩度仍接近衣服种子的像素并回衣服（彩度与明度无关，所以
# 阴影压暗也能命中）。0 = 关闭。
CLOTH_EDGE_GROW = 4            # 轮廓外搜索宽度（像素）
CLOTH_EDGE_GROW_CHROMA = 14.0  # 与衣服种子的色度距离上限
CLOTH_LEGACY_L_WEIGHT = 0.55   # 兜底蒙版的 Lab 明度权重

# 手臂排除（cloth.py 语义分割）：把模型判为 Left/Right-arm(14/15) 的像素并入
# 皮肤排除。RGB 肤色判据对深肤色/阴影里的手臂会漏检，导致换装蒙版溢到手臂；
# 语义 arm 类别可靠且与 upper 互斥（全库验证交集恒为 0），不会误伤上衣。
CLOTH_ARM_EXCLUDE = True

# 人体语义解析（C 档：SegFormer-B2 / ATR 18 类，可选 ONNX 模型）
# 模型文件 models/segformer_clothes.onnx 或 onnxruntime 缺失时自动回退到上面的色调启发式。
# auto = 模型优先且失败自动降级；model = 强制模型（失败仍降级，但记在 method 里）；
# tone = 完全不用模型，回到旧行为。
HUMAN_SEGMENT_ENABLED = True
HUMAN_SEGMENT_PRIORITY = "auto"
HUMAN_INPUT_SIZE = 512         # 模型输入边长（预处理配置 512x512，强制拉伸）
HUMAN_MIN_COVERAGE = 0.008     # 模型给出的上衣占比低于此值 → 认为解析失败
HUMAN_MAX_COVERAGE = 0.85      # 高于此值 → 认为把背景也判成衣服，降级
HUMAN_MIN_PERSON = 0.12        # 模型判出的人像占画面低于此值 → 没认出人（合成图/远景），降级
HUMAN_MIN_UPPER_RATIO = 0.15   # 上衣占人像的比例低于此值 → 模型没定位到衣服，降级
HUMAN_COMPONENT_NEAR = 8        # 只保留距主体这么近的其它连通域（挡住背景碎块）
HUMAN_COMPONENT_MIN_RATIO = 0.10   # 其它连通域至少要占主体的这个比例
HUMAN_CACHE_SIMILARITY = 0.985  # 缩略图相关系数超过此值视为同一张照片（复用推理结果）
HUMAN_TONE_DILATE = 6           # 沿轮廓按色调补回的宽度（像素）
HUMAN_TONE_DISTANCE = 30.0      # 与衣服多色中心的 Lab 距离上限
HUMAN_REGION_GROW = False       # 整块连通区域并入：能补漏判袖子，也会放大背景碎块，默认关
HUMAN_REGION_DILATE = 6         # 区域生长：与衣服相邻的连通色调块
HUMAN_GROW_MAX_RATIO = 0.30     # 单块扩张面积上限（相对已有衣服面积），防止吞掉整个人
HUMAN_KEEP_COMPONENTS = 2       # 保留最大的几个连通域（保住分离的袖子）
HUMAN_SMOOTH_LABELS = True      # 类别图用 one-hot 双线性上采样 + argmax，消除最近邻放大的阶梯锯齿

# 袖子 / 上臂排除：ATR 18 类只有「裸露的 Left/Right-arm」，穿了长袖时整件
# 外套（含袖子）会被统一判成 Upper-clothes，语义层无法区分。这里在语义扩张
# 之后按「人像逐行轮廓」向内收窄，让覆盖停在肩线与躯干，边界随身形弯曲。
# 0 = 关闭（回到旧行为，整件上衣含袖子一起换装）。
CLOTH_SLEEVE_TRIM = 0.18       # 每行从人像左右边界向内收窄的比例；0.26 会切断包装主字
CLOTH_SLEEVE_TRIM_SMOOTH = 11  # 收窄边界沿 y 的中值滤波窗口（行），避免逐行抖动

# 边缘平滑：分割蒙版边缘是逐像素的二值轮廓，逐行外凸/内凹 3~5px 就会在衣服
# 轮廓上留下"撕纸边"锯齿（冰红茶案例放大后最明显）。这里按逐行 4/96 分位取
# 轮廓，中值去单行跳变 + 高斯平滑，再用平滑轮廓重建覆盖区：只填内凹、不外扩。
# 0 = 关闭（回到旧的逐像素锯齿边）。
CLOTH_EDGE_SMOOTH = 1.0        # 平滑强度倍率（0=关闭）
CLOTH_EDGE_SMOOTH_SIGMA = 0.012  # 高斯半径（相对衣服高度，642px 图约 7.7px）
CLOTH_EDGE_SMOOTH_MEDIAN = 5   # 逐行轮廓的中值窗口（行）

# 多层着装：西装/夹克会把里面的衬衫、V 领背心一起判成 Upper-clothes（ATR 18 类
# 没有"外套"类），覆盖后领口层次被抹平、整块铺成一枚围兜。这里用「外层在左右
# 两侧多、内搭在中轴多」的空间先验剥掉外层色簇，只留内搭，领口 V 形自然保留。
# 单色 T 恤剥完剩余面积会过小，判定误判自动放弃。
#
# 默认关闭（2026-11-16）：新的基准库是单层球衣/背心，而"大面积居中印花"本身
# 就满足「左右多、中轴少」的空间先验 —— 实测把雪碧的绿色球衣判成外套剥掉，
# 只留下中间那块白色印花，换装后变成胸口一小片红。换装的目标是整件替换，
# 需要保留旧的"只换内搭"行为时把它打开。
CLOTH_LAYER_SPLIT = False
CLOTH_LAYER_CLUSTERS = 4         # 衣服内 Lab 聚类数
CLOTH_LAYER_EDGE_FRAC = 0.22     # 左右各取这么宽（相对衣服宽）作为外层带
CLOTH_LAYER_CENTER_FRAC = 0.12   # 中轴带半宽（相对衣服宽）
CLOTH_LAYER_SIDE_MIN = 0.16      # 簇在左带、右带都必须达到的占比
CLOTH_LAYER_CENTER_MAX = 0.45    # 簇在中轴带的占比上限
CLOTH_LAYER_MIN_SAMPLE = 0.08    # 簇像素占比低于此值不参与判定
CLOTH_LAYER_MIN_KEEP = 0.22      # 剥完剩余面积低于原面积此比例 → 判定误判，放弃
CLOTH_LAYER_MAX_KEEP = 0.92      # 剥完几乎没减少 → 单色衣服，不启用
# 多色联合匹配：品牌色板聚合 + 双向加权色差
MATCH_VECTOR_MAX = 5           # 品牌色板最多保留几色
MATCH_FEATURE_MIN_RATIO = 0.04  # 单图内某色占比低于此值，不进入品牌色板
MATCH_MERGE_DELTA_E = 12.0     # 聚合时色差小于此值视为同一色
MATCH_QUERY_MAX = 5            # 参与匹配的查询色最多几色
MATCH_MAIN_WEIGHT = 0.0        # 主色 ΔE 只展示、不参与排序；想让主色更强势可调到 0.2
MATCH_JOINT_WEIGHT = 1.0       # 多色联合色差权重（联合匹配主导）
MATCH_Q2B_WEIGHT = 0.75        # 衣服色 -> 品牌色的覆盖度（衣服每种颜色都要被照顾到）
MATCH_B2Q_WEIGHT = 0.25        # 品牌色 -> 衣服色的覆盖度
MATCH_SCORE_SCALE = 1.8        # score = 100 - scale * adjusted_delta_e

# 合成图：把衣服换成品牌配色，并在衣服上印品牌字样与元素（仅本地娱乐用途）
COMPOSE_MASK_INNER = 12.0      # Lab 距离小于此值 → 完全属于衣服
COMPOSE_MASK_OUTER = 34.0      # Lab 距离大于此值 → 完全不属于衣服
COMPOSE_STRENGTH = 1.0        # 换色强度（1 = 衣服区域完全覆盖为品牌主色）
COMPOSE_L_SMOOTH_FRAC = 0.012 # 局部明度平滑半径（相对短边），抹平原号码/字样的亮度痕迹
COMPOSE_L_CONTRAST = 0.12     # 布料纹理保留比例（太高会让原印花出现"幽灵轮廓"）
COMPOSE_INK_CHROMA = 32.0     # 与衣服色调色度差超过此值 = 印花/号码，抑制其亮度差
COMPOSE_MIN_COVERAGE = 0.02   # 衣服像素低于此占比时回退到 ROI 椭圆区域
COMPOSE_FILL_CLOTH_HOLES = True  # 衣服轮廓内部（号码/字母/描边/网眼）强制完全覆盖，杜绝原字样淡影
COMPOSE_EDGE_FEATHER = 1.2      # 衣服外轮廓抗锯齿过渡宽度(px)，0=硬边；只在边缘 1~2px 生效
COMPOSE_QUERY_COLORS = 3      # 最多用几个衣服色块做分区换色
COMPOSE_FORCE_DOMINANT = True # 面积最大的衣服色块强制换成品牌权威主色
COMPOSE_STICKER = True         # 是否把品牌包装图做成圆形贴片（仅 print 风格）
COMPOSE_STYLE = "wrap"         # 默认风格：wrap=包装贴图 / print=平涂主色+程序印花 / garment=基准衣服迁移
COMPOSE_WRAP_DECORATE = False  # wrap 风格默认不再叠程序绘制的大字/徽章/色板
COMPOSE_PRINT_TEXT = False     # 合成图上不打印任何程序绘制的品牌文字（包装自带艺术字照常保留）
# 基准衣服迁移：从已换装的基准成品图精确切出衣服，按目标衣服逐行轮廓做非刚性变形。
# width_ratio 过滤领口尖角行/下摆碎行（0=关闭）；local_print 档收窄更多以排除两侧外套，
# full_wrap 档不收窄，保留满版主字。改这两个值后需重跑 scripts/build_garments.py。
GARMENT_WIDTH_RATIO = 0.45
GARMENT_SHRINK_LOCAL = 0.30
GARMENT_SHRINK_FULL = 0.0
# 小尺寸输入的高清化：171x234 的测试图里衣服只有 119x148，放大到网页上必然糊。
# 输出短边低于此值时，先整幅高质量放大再跑分割/贴图/光照，几何参数大多是相对的，
# 实测衣服 mask 与原尺寸 IoU 0.97；设为 0 可关闭、完全保持原输出尺寸。
COMPOSE_MIN_SHORT_EDGE = 480   # 输出短边下限（px），0 = 关闭
COMPOSE_UPSCALE_SHARP = 0.30   # 输入图放大后的轻度轮廓锐化（0 = 关闭）
COMPOSE_FONTS = (
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)

# 包装贴图（wrap 风格）：把品牌产品图的真实包装视觉穿到衣服上。
# 目标效果见 report/_ref_target.webp —— 衣服上是包装自带的艺术字/图案，
# 而不是程序绘制的文字，同时保留原图的肩部高光与布料褶皱。
WRAP_MIN_SHORT_EDGE = 80       # 小于此尺寸的产品图放大后太糊，回退 print
WRAP_MIN_FOREGROUND = 0.06     # 彩色主体占比低于此值视为无效图
WRAP_WHITE_MIN = 226           # 近白背景：三通道最小值下限
WRAP_WHITE_SPAN = 24           # 近白背景：最大通道差
WRAP_BLACK_MAX = 32            # 近黑影棚背景：最大通道上限
WRAP_ROW_MIN = 0.02            # 行/列彩色像素占比门槛，用于裁主体外接框
WRAP_EDGE_LIGHT_MIN = 165      # 瓶身边反光：平均亮度下限
WRAP_EDGE_LIGHT_SAT = 0.38     # 瓶身边反光：最大饱和度（橙色包装不属于反光）
WRAP_EDGE_LIGHT_RATIO = 0.15   # 边缘三列反光占比中位数超过此值才判定为反光带
WRAP_EDGE_MAX_FRAC = 0.22      # 单侧最多裁掉多少宽度（保留给后续扩展）
WRAP_EDGE_TRIM_FRAC = 0.09     # 确认有反光带时单侧固定切掉的宽度
WRAP_TRIM_TOP = 0.10           # 裁掉瓶盖占比
WRAP_TRIM_BOTTOM = 0.06        # 裁掉瓶底收缩段占比
WRAP_BG_BLUR_FRAC = 0.05       # 背景补色模糊半径（相对原图短边）
WRAP_SIDE_PAD = 0.08           # 标签两侧用本行包装色补的留白比例（防主字顶边）
WRAP_FILL_W = 1.06             # 标签窗口宽度相对衣服外接框宽度的倍数（含补边补偿）
WRAP_FILL_H = 1.10             # 标签窗口高度相对衣服外接框高度的倍数
WRAP_TEXT_ANCHOR = 0.32        # 包装主字在标签竖直方向的大致位置
WRAP_TEXT_POS = 0.42           # 希望主字落在衣服较宽的中部，避开肩线收窄处
WRAP_LOWER_MIX = 0.70          # 下摆渐入整张标签的比例（带出橙红/柠檬图案）
WRAP_CURVE = 0.10              # 胸廓横向形变：中间略放宽
WRAP_ARC = 0.012               # 胸口中央微微上拱（相对画布高）
WRAP_SHARP = 0.35              # 放大后的 unsharp 强度
WRAP_SHARP_SIGMA = 1.1         # unsharp 的模糊半径
WRAP_L_SMOOTH_FRAC = 0.045     # 光照场平滑半径（相对短边，比换色明度更大尺度）
WRAP_SHADE_GAIN = 1.05         # 原图明暗调制强度（1 = 完全沿用原衣服明暗）
WRAP_SHADE_MIN = 0.62          # 明暗调制下限，避免衣服出现死黑
WRAP_SHADE_MAX = 1.45          # 明暗调制上限，避免白底被放大成纯白

# 清晰度：包装源图普遍只有 171~270px，标签裁完常常仅 70~160px 宽，
# 多级 Lanczos 会把文字笔画低通成「发虚的软边」。下面这组参数在放大后
# 做一次「笔画重绘」：局部对比拉回、颜色归位把灰蒙蒙的过渡推回纯色、
# 再用边缘掩码引导的细半径 USM 只锐化笔画而不放大背景噪点。
# 全部为纯 numpy/scipy 运算，不引入任何新依赖；逐品牌可用 detail 覆盖。
# 1.2 是实测档位：1.0 稍软、1.4 开始出现白边，1.2 笔画清楚又干净。
WRAP_DETAIL = 1.2              # 重绘总强度，0 = 关闭（回到旧观感）
WRAP_DETAIL_PRE = 0.35         # 放大前的预锐化，抵消插值低通
WRAP_DETAIL_GAIN = 1.22        # 局部对比增益
WRAP_DETAIL_PUSH = 0.50        # 颜色归位（高饱和笔画推向纯色）比例
WRAP_DETAIL_USM = 0.85         # 边缘自适应 USM 强度
WRAP_DETAIL_SIG_BIG = 6.0      # 局部对比的高斯半径
WRAP_DETAIL_SIG_SMALL = 0.75   # 细锐化半径
WRAP_CURVE_ORDER = 3           # 胸廓形变插值阶数：3=三次，比双线性明显少糊

# 连续纹理：旧实现先把整张标签高斯模糊成渐变底，再在胸口硬贴一块清晰矩形，
# 框内框外是两版画面，四条边没有羽化 → 花纹出现水平/竖直「换了一版」的断裂。
# 现在整张画布从同一张等比放大的标签连续采样（超出边界做镜像反射，处处连续），
# 再按衣服逐行包络做轻度形变；55 个品牌调好的 word_y / zoom / focus 仍生效。
WRAP_CONTINUOUS = True         # 关掉则回到旧的「模糊底 + 清晰矩形」路径
WRAP_SIDE_MIRROR = True        # 标签左右留白用镜像延展代替逐行中位色（消横条纹）
WRAP_ENVELOPE = 0.55           # 逐行包络形变强度：0=纯平面，1=完全跟随衣服宽度
WRAP_ENVELOPE_SMOOTH = 0.02    # 包络纵向平滑半径（相对画布高）
WRAP_ENVELOPE_RANGE = 0.28     # 单行最大压缩/拉伸比例（±28%），防止文字被拉爆
WRAP_ENV_ORDER = 3             # 连续采样插值阶数
# 采样坐标超出标签范围时的延展方式：
#   nearest  边缘行/列向外平推（默认；同一色带延续，接缝跳变实测 ≈0）
#   reflect  镜像反射（有图案重复，折返处会出现上下颠倒的字，跳变 ≈8）
WRAP_EXTEND_MODE = "nearest"

# --- R2 / R3 渲染层 AI 化 ---------------------------------------------------
# R2：离线超分素材（scripts/upscale_brands.py 生成，只注入高频残差，字形不变）
WRAP_USE_HD_SOURCE = True        # 关掉则读取用户原始图片，逐像素回到超分前

# R3：深度模型驱动的 2D warp 坐标场（AI 只预测坐标，像素仍取自原包装）
WRAP_DEPTH_WARP = True           # 关掉则回到旧的 1D 逐行包络
WRAP_DEPTH_INPUT_SHORT = 518     # Depth Anything V2 Small 输入短边（14 的倍数）
WRAP_DEPTH_SCALE = 7.0          # 胸廓级视差位移幅度(px)
WRAP_DEPTH_CYL_SCALE = 4.0      # 由衣服轮廓距离场构造的柱面/球面分量(px)
WRAP_DEPTH_DETAIL_SCALE = 0.0   # 默认关闭：原图明度高频混着号码/字母的边缘轮廓，
                                # 无法干净分离，会把原字样形状印进 warp 场并让包装笔画起毛刺
WRAP_DEPTH_MAX_SHIFT = 8.0      # 单点总位移钳制(px)，防止主字被拉歪
WRAP_DEPTH_SMOOTH = 0.12        # 深度场低通半径（相对短边）；足够大才能滤掉印花误判
WRAP_DEPTH_DETAIL_CLIP = 6.0    # 仅在显式开启 DETAIL_SCALE 时使用的软饱和尺度
WRAP_DEPTH_TEXT_PROTECT = 0.55  # 主字区保留的形变比例（0=完全不动，1=不保护）
WRAP_DEPTH_EDGE_FADE = 4.0       # 衣服轮廓内淡出宽度(px)，保证边界不跳变


def ensure_dirs() -> None:
    for p in (DATA_DIR, IMAGE_DIR, INBOX_DIR, CACHE_DIR, CONFIG_DIR, REPORT_DIR):
        p.mkdir(parents=True, exist_ok=True)

# 换装（garment）档的覆盖版型：
#   target（默认）把基准衣服"穿"进目标这件衣服的范围 —— 边缘干净、不漏原色；
#   source 跟着基准衣服自己的剪影走 —— 版型更接近基准，但基准比目标窄时
#          两侧会露出原色竖带（需要靠外扩去盖）。
COMPOSE_GARMENT_SHAPE = "target"

# 基准衣服纹理的坐标映射方式（garment / 衣橱换装风格）：
#   rowwise     逐行贴合目标身形：纵向拉伸铺满，横向把每一行的源半宽拉到目标
#               该行半宽，中心也逐行取。图案完全跟着身材走（肩宽腰窄都对得上），
#               代价是字会被横向压窄/拉宽、竖笔画逐行轻微错位，看起来带点"扭曲"。
#   similarity  整件衣服一个等比缩放，字形完全不变，但目标比源胖时两侧退化成
#               纯色渐变、上下可能留一段同色。
# 2026-11-16 之前是 rowwise；改成 similarity 是为了"字要正的"。两者只是观感
# 取舍，改这一行即可切换，不需要动算法。
GARMENT_TEXTURE_ALIGN = "rowwise"
