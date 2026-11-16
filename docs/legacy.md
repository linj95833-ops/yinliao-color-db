# yinliao-color-db · 饮料配色数据库

传一张人像照片，认出你衣服的颜色，配一瓶最搭的饮料，再把它的包装穿到你身上。全程离线，不接生成模型，不需要显卡。

**怎么用**：从 [**Releases**](https://github.com/linj95833-ops/yinliao-color-db/releases/latest) 下载 `DrinkWrap.exe`（就是「饮料换装」）→ 双击运行，浏览器自动打开 → 选衣服或传自己的照片 → 下载成品。

> exe 是 146 MB，超过 GitHub 单文件 100 MB 上限，**不在仓库代码里**，只放在 Releases 附件中（[直达下载](https://github.com/linj95833-ops/yinliao-color-db/releases/latest/download/DrinkWrap.exe)）。首次运行会在 exe 同目录生成 `饮料换装_数据` 文件夹存结果，删掉即清空。

<details>
<summary>项目沿革与当前进度（点开看）</summary>

> 非商业、纯娱乐项目。灵感来自「给名人 P 上饮料」的梗：最终想做**上传人像照片 → 识别衣服颜色 → 匹配最搭的饮料品牌**。
>
> **当前进度：建库 → 多色联合匹配 → 衣服换色合成图，全链路均已跑通。** 图片质量不再纠结：假定图片都正确，由使用者手动填满；数据库保留 55 个品牌的多色主色档案，放图即自动升级为实测色板。合成图默认已升级为**包装贴图**：直接把品牌产品图上的真实图案与文字穿到衣服上，并保留原图光影。
>
> **2026-11-16 起新增「衣橱」主线**：库的语义从「饮料包装图」换成「已经换好装的人像」。
> 36 件基准人像各自切出一件衣服资产（`data/wardrobe/`），输入任意人像后先把衣服
> 整件切下来、按目标身形拉伸变形、再覆盖上去 —— 见 [十六、衣橱重构](#十六衣橱重构饮料库整体换成已换装人像2026-11-16)。
> 网页首页 `web/wardrobe.html`，接口 `/api/wardrobe`、`/api/wd/match`、`/api/wd/compose`。

</details>

---

## 零、当前主线：图片自己填，直接跑通匹配（2026-11-11 起）

> 自动采集来的图片质量参差，因此改用「**假定每款饮料的图片都正确、图片由使用者自己放**」的模式。
> 数据库 = 55 个品牌主色档案；未放图的品牌先用人工占位色，放图后 `scan` 自动升级。
> **当前库（2026-11-11 手动填图后）**：55 张图片 / **55 个图片实测档案 / 0 个占位档案**，磁盘与 SQLite 一致（0 孤儿、0 缺失）。用户删掉目录的 5 个品牌已从清单彻底移除。

```bash
# 1) 建库：55 品牌 + 首次安装时生成 data/images/<key>_<中文名>/ 目录 + 占位种子档案
#    之后目录由使用者自己管理，删掉的目录不会被 scan / init 自动重建
.venv/Scripts/python.exe -m beverage_color_db.cli init

# 2) 手动填图：把产品包装实物图放进对应品牌目录，例如 data/images/coca_cola_可口可乐/1.jpg
#    支持 jpg/jpeg/png/webp/bmp；也支持 data/inbox/<key>_<中文名>/
.venv/Scripts/python.exe -m beverage_color_db.cli scan
#    白底/影棚纯色背景自动识别并剔除（含「产品只占中间一小块」的电商白底图）；
#    带场景的图加 --remove-bg；替换同名文件或改了提色逻辑后加 --force 重算

# 3) 上传人像照片 -> 识别衣服颜色 -> 匹配品牌
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo examples/portrait_sample.png
.venv/Scripts/python.exe -m beverage_color_db.cli match --color "#E60012"   # 单色
.venv/Scripts/python.exe -m beverage_color_db.cli match --color "#E60012,#004B93"  # 多色联合

# 4) 生成合成图：wrap = 把饮料包装穿上身（默认）/ print = 平涂主色 + 程序字样
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo examples/portrait_sample.png --compose
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo my.jpg --compose --brand kangshifu --out my.png
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo my.jpg --compose --style print --out flat.png
.venv/Scripts/python.exe -m beverage_color_db.cli serve                      # 浏览器上传界面
```

**目录命名**：文件夹统一为 `key_中文名`，例如 `coca_cola_可口可乐`、`genki_forest_元气森林`、`tea_pi_茶π`，方便直接在文件管理器里认出品牌。数据库里的品牌 key **不变**（`--brands`、`config/brands.yaml`、匹配结果里仍用 key）。中文后缀可以自由改（例如 `powerade_水动乐`），只要保留前面的 `key_` 前缀就能识别；旧版纯英文目录会在 `init` / `scan` 时自动改名并同步更新数据库路径，不会丢图。

**目录增删由使用者决定**：`scan` / `init` 只在一个品牌目录都没有的全新安装时才自动建全套目录；手动删掉的目录不会被重建，需要恢复时自己新建 `key_中文名` 目录再放图。从 `config/brands.yaml` 移除的品牌会在 `init` / `scan` 时同步从数据库删除（当前清单 55 个）。

**档案来源（`brand_color_profile.origin`）**

| 来源 | 含义 | 数量（当前库） |
| --- | --- | --- |
| `images` | 由图片实测提色 | 55 |
| `reference` | 尚未放图，用 `config/seed_colors.yaml` 的人工占位色，`confidence=0.25` | 0（配置保留，随时可补） |

- 放图 + `scan` → 自动升级为实测档案；删图 + `scan` → 自动清理失效记录并回退占位档案。
- **用户手动放的图视为权威**：某品牌一旦存在 `source='user'` 的图，只用这些图建档，忽略自动采集图。
- 匹配结果明确标注档案来源；占位档案额外加 6 个 ΔE 惩罚，近似色差下实测档案优先。
- 占位色只是凭常见包装的粗略估计，不是实测值；不认可可直接改 `config/seed_colors.yaml` 或放图覆盖。

**多色联合匹配（`matcher.py`）**：每个品牌的 `color_vector` 是 2~5 色色板 —— 把每张图的 Top-N 颜色特征按「图内占比 × 图片质量」聚合、相近色（ΔE<12）合并，首位固定为权威主色 `dominant_hex`。匹配时双向加权：

- 衣服色板每个色 → 品牌色板最近色，按衣服占比加权（占联合分 75%）；
- 品牌色板每个色 → 衣服色板最近色，按包装占比加权（占 25%）；
- 合成 `joint_delta_e` 决定排序，主色 `delta_e` 只展示不参与排序（权重在 `config.py` 的 `MATCH_*` 可调）。

结果页逐色展示「衣服色 → 命中的包装色 + ΔE」。`match --color` 支持逗号分隔多色（默认等权，`--weights "0.8,0.2"` 可调），`match --photo` 自动使用识别出的全部衣服色，网页也可直接输入多个颜色。例：红+蓝 → 百事可乐，红+蓝偏蓝 → 动乐，单色红 → 王老吉。

**衣服区域分割与取色（`cloth.py` + `portrait.py`）**：不再把人当矩形。全图做 Lab 色调聚类，用四边框量化直方图估计背景主色、用 RGB+YCbCr 肤色判据当种子，把色调簇分成「背景 / 皮肤 / 衣服 / 印花」；取提示区里占比最大的衣服簇并合并色度相近的高光/阴影簇，再检测领口——领口以上只保留左右肩的连续布料段（V 领、脖子、脸不会被填），领口以下按每行衣服左右边界整段填充，因此「LAKERS」「24」这类白色号码与深色描边连同网眼全部被并入衣服区域。最后封口、取最大连通域、补底边填洞、扣除背景色；再在轮廓外一圈窄带里按色调把领口/肩线的金色滚边、深紫描边、白色侧条补回衣服（背景与深色皮肤始终排除，避免换色后留一条原色细线），最后羽化得到贴合身体轮廓的软蒙版。色板只在这个蒙版内用 Lab 加权 KMeans 提取。

`--box 0.2,0.3,0.8,0.9`（默认 `(0.25,0.28,0.75,0.88)`）现在只是「去哪里找衣服颜色」的**提示区**，不限制衣服形状；`report/match_result.html` 与网页调试图会画出分割出的衣服轮廓与色板。分割失败或覆盖异常时自动回退到旧的「矩形 ROI + Lab 距离」软蒙版，并在结果里标出 `fallback_mask`。

**合成图（`compose.py` + `wrap.py`）**：`match --photo ... --compose` 生成一张新 PNG（默认 `report/composite.png`），两种风格用 `--style wrap|print` 或网页「合成效果」下拉切换。

**`wrap`（默认，把饮料包装真的穿上身）**——**衣服上不会出现任何由程序绘制或打印的品牌字**：中文英文艺术字、logo、渐变与图案全部来自品牌目录里那张产品图；白色罐体/透明瓶身的「瓶体白」也属于包装本身，同样原样保留。

- **包装纹理提取**：`wrap.py` 找出与画面边框连通的白底/黑底 → 用「同一行瓶身主色」补成上黄下橙的自然渐变（不会留下白色竖带）→ 按彩色主体裁出标签、切掉瓶身两侧反光边 → 按衣服宽高比截取标签窗口（主字落在胸口偏上）→ 下摆渐入整张标签的压缩版，把橙红底纹与柠檬片一起带上身 → 轻微胸廓曲面形变（三次插值）+ 放大前后的「笔画重绘」。
- **逐品牌单独调参**：55 张包装图构图差别极大（45 张是白底、产品只占中间三成；还有横版多品、纯 logo、深色瓶），统一参数必然让衣服白花花一片。`config/brand_wrap.yaml` 为**每个品牌**记录独立参数：`crop` 先把源图裁到产品主体、`focus` 再把标签裁到主字所在的窗口（上/中/下因品牌而异）、`tint` 把浅色瓶身染成品牌色调而保留深色文字、`bg_mix` 补品牌色底、`bright/contrast/boost` 补偿过暗或过淡、`zoom/anchor/text_pos` 调字号与位置。缺省字段才回落到 `config.py` 的 `WRAP_*` 默认值；康师傅冰红茶是效果基准，条目留空 `{}`，调优前后像素级未变。裁剪后短边不足 160px 的小图会先等比放大再处理，不会因尺寸门槛直接回退。55 张最终效果见 `report/brand_gallery.html`（可切换早期版本 / 并排对比）。
- **V 领吞字与低分辨率糊字（第二轮全量复检）**：有些包装主字是**竖排四字**（六个核桃），跨度比整件衣服还高，V 领凹口又会吃掉上部约 35px；有些源图只有 2KB、主字每字十几像素（娃哈哈），多级放大后糊成像素块。这类情况只用**包装原图本身**解决，绝不补印刷体：`word_y` 把贴图落点与「窗口取景用的 `text_pos`」解耦，`focus` 取到主字所在窗口，`zoom` 控制字大小；`crisp`（配 `crisp_sharp` / `crisp_sigma`）把高饱和笔画做颜色归位 + 细半径锐化，`curve_order: 3` 用三次插值做胸廓形变减少重采样模糊。六个核桃取包装上部竖排四字窗口并下移落点，四字完整；娃哈哈靠重锐化保留原图红字。全部 55 张逐张目视复检，最终效果见 `report/brands/<key>.png` 与 `report/brand_gallery.html`。
- **白色瓶体不是背景**：白色罐体（六个核桃）/ 透明瓶身在白底商品图里与摄影棚白底连成一片，只看「是否与边框连通」会连瓶身白一起补成品牌色、或再被 `tint` 染掉。`keep_body_white: 1` 用彩色像素（蓝标、核桃图案、logo）的行列投影求出瓶体外接框，**框内的白属于瓶体、保持白色**，只有框外的白底才参与补色；同时跳过该品牌的 `tint` 染色与两侧反光裁切。目前仅六个核桃启用，其它品牌像素不受影响。
- **清晰度优化（第三轮，两层一起做）**：糊有两个来源，只锐化小图救不回来。① **输出分辨率**：测试人像只有 171×234、衣服框 119×148，衣服上的字总共才十几像素高，网页一拉伸必然糊。`compose._ensure_resolution()` 在输出短边低于 `COMPOSE_MIN_SHORT_EDGE`（默认 480）时，先把整幅图 Lanczos 放大并做边缘引导的轻锐化，**再**跑衣服分割 / 贴图 / 光照，衣服框同步放大到约 324×410；实测放大前后衣服 mask 硬边 IoU 0.97、软边平均差 0.013，构图不变。用户上传高清照片时该步完全跳过、输出保持原尺寸。② **贴图笔画重绘**：55 张源图里 47 张短边 < 300px，标签裁完常只有 70~160px 宽，多级 Lanczos 会把笔画低通成软边。`wrap._upscale()` 在放大前先补高频，`wrap._restore_detail()` 在放大后做局部对比回拉、笔画附近的颜色归位、边缘掩码引导的细半径 USM；颜色归位只在边缘邻域生效，避免把平坦白底的 JPEG 噪点染成彩色斑点。胸廓形变默认改三次插值（`WRAP_CURVE_ORDER = 3`）。全部是 numpy/scipy/Pillow 运算，**不新增依赖、不生成任何新文字**。强度由 `WRAP_DETAIL`（实测 1.2 最清楚且干净，1.4 开始出白边）控制，设 0 可回到旧观感。
- **自动落点搜索**：调参时用衣服 mask 计算「包装主视觉有多少落在有布料的区域」，对每个品牌扫 0.30~0.70 的落点取最优，避免逐条猜位置。该指标对「竖排字跨度大于可用布料」与「源图糊」无效，这两类改走上面的取景 / 重锐化路径。
- **光照调制**：`compose.py` 从原图 Lab 里提取**大尺度明度场**（先用印花判据剔除号码/字母，再大半径平滑），乘到纹理上，于是贴图有肩部高光、腋下阴影与布料褶皱，而不是一张平贴的纸。最后按 `cloth.segment()` 的软边 mask 一次性覆盖，原白色号码 / 深色描边**彻底消失**，脸、头发、手臂、V 领与背景完全不动。
- **自动回退**：包装图缺失、极端小图（短边 < 40px）、几乎没有彩色主体或裁不出标签时，自动改用 `print`，并在结果里给出 `wrap_fallback_reason`。目前 55 个品牌全部成功构建贴图，0 回退。
- 参考效果：`report/_ref_target.webp`（科比球衣换成康师傅冰红茶包装）；本项目本地复现见 `report/composite_wrap_kangshifu.png`。源图分辨率低时文字细节仍有物理上限（笔画信息本来就不存在），但小图自动高清化 + 笔画重绘已让同样显示尺寸下的字明显更实；真正的上限仍需换高清正面包装图或 AI 超分模型。

**`print`（旧版，平涂印花）**：衣服 100% 换成品牌权威主色 `dominant_hex`，抑制印花亮度痕迹避免「幽灵号码」；再画圆形徽章、包装色板、按品类的气泡·水滴·茶叶·咖啡豆·闪电·水果元素，以及一枚产品图圆形贴片。**默认同样不绘制任何程序文字**（中文名 / 英文名 / 标语 / 徽章首字都由 `config.COMPOSE_PRINT_TEXT = False` 关掉；确实想要印花字样时把它改成 `True` 即可）。所有印花乘上衣服 mask，不会印到皮肤或背景上。联合色差 ΔE 属于匹配指标，只放在报告页与接口数据里。

- **入口**：CLI `--compose [--brand key] [--style wrap|print] [--out path]`；网页上传照片后自动显示合成图与 PNG 下载，每张品牌卡片都有「换成这个品牌生成」按钮（走 `/api/compose`）。纯颜色查询没有照片，不生成合成图。
- 全局默认参数在 `config.py` 的 `CLOTH_*`、`COMPOSE_*`（含 `COMPOSE_STYLE`、`COMPOSE_PRINT_TEXT`、`COMPOSE_MIN_SHORT_EDGE` / `COMPOSE_UPSCALE_SHARP` 输出高清化）与 `WRAP_*`（背景补色、标签裁切、下摆混合、曲面形变、`WRAP_DETAIL_*` 笔画重绘、`WRAP_CURVE_ORDER`、光照强度）；合成结果里会带 `output_scale` 与 `source_size` 说明是否放大过。**单品牌覆盖在 `config/brand_wrap.yaml`**，改完不用动代码；网页服务进程会缓存参数表，改完**重启一次 `serve`** 即可生效，CLI 每次都是新进程、自动读最新配置。`wrap.py` 已彻底移除程序补字逻辑，`brand_wrap.yaml` 里也不再有 `overlay_*` 字段。

**本地网页**：`serve` 只监听 `127.0.0.1:8765`，图片在浏览器里转成 base64 后 POST 到本地接口，不经过任何外部服务；结果同时写到 `report/match_result.json` / `.html`，合成图写到 `report/composite.png`。

- **照片可随时换**：上传后出现「移除照片 / 换一张」两个按钮。移除会清空预览、禁用匹配按钮、隐藏上次结果；`input.value` 每次选完即清空，因此**再选同一张图或换一张新图**都会重新触发加载，不会卡在上一张。
- **任选饮料给人像穿上**：左栏「选择饮料」下拉列出全部 55 个品牌（中文名 / 英文名 / 主色 HEX），支持按名称、key、品类、地区搜索，选中后显示主色色块与包装色板，还有「随便来一个」随机按钮。点「用这个饮料生成新图」会带 `key` 走 `/api/match`，一次返回识别结果 + Top-N 推荐 + **指定品牌**的合成图；不在 Top-N 里的品牌同样可用。若所选品牌恰好进入 Top-N，卡片上会标「已选用」。
- **合成效果可切换**：左栏「合成效果」下拉在 `wrap`（包装贴图，默认）与 `print`（平涂印花）之间切换；开始匹配、指定饮料、Top-N 卡片重生成三条路径都会带上当前风格。合成图下方会显示实际生效风格、所用包装图文件名，以及自动回退平涂印花的原因。
- 接口：`GET /api/stats`、`GET /api/brands`（全量品牌 + 主色 + 微型色板 + 中文品类/地区）、`POST /api/match`（照片/多色匹配，可选 `key` 指定合成用品牌、可选 `style`）、`POST /api/compose`（只重生成合成图，可选 `key` / `style`）。`key` 不在品牌清单、`style` 不是 `wrap|print` 时返回 400；合成结果含 `compose_style`、`wrap_source`、`wrap_fallback_reason`。

---

## 一、图源方案（v2，2026-11-10 重做）

> **为什么要重做**：v1 把 Wikimedia Commons 当主源，结果 163 张里 **162 张来自 Commons**，
> 86 张背景杂乱、149 张近白像素不足 10%。Commons 是百科图片库，产出的是货架、混装、
> 实拍场景，本来就不产「标准产品正面图」。调算法只是给错误数据打补丁，所以 v2 **换源**。

| 图源 | 定位 | 实测 | 授权 |
| --- | --- | --- | --- |
| **Open Food Facts** | **主源**：标准产品正面包装图 | `/api/v2/search?brands_tags=` 恢复 200，coca-cola 1417 / sprite 205 / red-bull 425 条，几乎都带 `image_front_url`；`/api/v2/product/{barcode}.json` 稳定 200 | 图片 CC BY-SA 3.0，数据库 ODbL |
| **品牌官网公开产品页** | 国内品牌兜底（按 robots） | OFF 对王老吉 / 元气森林 / 东鹏 / 蜜雪收录为 0；实测 10 个官网可达，抓取前读取并遵守 `robots.txt` | 版权归品牌方，仅记录来源页，不分发二进制 |
| **Wikimedia Commons** | 最后兜底，且必须通过最严质检 | API 稳定 200，但内容类型不匹配；已降级 | 逐张 CC / PD |
| **本地 `data/inbox/`** | 用户自备干净素材 | 无网络依赖 | USER_PROVIDED |
| 手工 URL | 默认关闭 | `config/manual_urls.yaml` | USER_PROVIDED |

未启用的路径：Openverse / HuggingFace 在当前网络返回代理 502；`search_terms` 中文全文检索在 OFF 被忽略（返回全库 count）；OFF 的 `cgi/search.pl`、`/brand/*.json` 仍 503。

**OFF 限流很严**：`page_size > 5` 或高频请求立刻 503，三个镜像域名同样不稳。对策是**单并发 + 小分页(5) + 域名 failover + 指数退避 + 检索结果缓存 7 天**（`data/cache/`）。国内品牌实测零收录，默认跳过 OFF 检索（`OFF_SKIP_CN_SEARCH`），把限流额度留给海外品牌。

### 质量策略：前移到图源，而不是事后靠算法补救

1. **换源**：用 OFF 人工标注的 `image_front_url`（产品正面照）替代 Commons 百科场景照。
2. **质检闸门**（`quality.py`，宁缺毋滥）：拒空白图、超大 banner / 宣传片截图、超宽横幅、颜色过散的拼贴图；Commons 还必须满足纯色/白底背景 + 颜色集中。
3. **择优录取**：每品牌先下载 `limit × 3` 个候选，按「主色集中度 + 主色饱和度 + 颜色不散 + 图源可信度」打分，**只保留分数最高的 `limit` 张**，其余删文件与 DB 行。从「先到先得」改成「择优录取」。
4. **复核页**：`report` 会生成 `report/gallery.html`，逐张展示最终入库图 + 图源 + 授权 + 质量分，缩略图内联可离线打开。

> 标定数据：Commons 干净图 `corner_std 5.9 / dominant 0.465`，场景照 `61.3 / 0.145`；
> 但 OFF 红牛标准罐 `corner_std 53.3`、雪碧瓶 `89.2` —— 它们的**四角就是包装**，
> 用「背景一致性」判据反而会误杀好图。所以质检按图源分层，OFF 正面图走语义可信策略。

---

## 二、快速开始

环境：Python 3.13（本机独立 `pip` 指向另一个 3.11，务必用 `python -m venv` + venv 内的 python）。

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

# 1) 小样本冒烟（建议先跑，验证网络与图源）
.venv/Scripts/python.exe -m beverage_color_db.cli all --brands coca_cola,sprite,red_bull --limit 3

# 2) 全量采集 55 个品牌（约十几分钟，OFF 限流会退避）
.venv/Scripts/python.exe -m beverage_color_db.cli all --limit 5

# 3) 单品牌补图 / 只重算，不必全量重跑
.venv/Scripts/python.exe -m beverage_color_db.cli fetch --brands wanglaoji --source local --limit 5
.venv/Scripts/python.exe -m beverage_color_db.cli ingest --force
.venv/Scripts/python.exe -m beverage_color_db.cli report

# 4) 第二阶段：手动填图 / 人像照片匹配 / 本地网页
.venv/Scripts/python.exe -m beverage_color_db.cli scan
.venv/Scripts/python.exe -m beverage_color_db.cli match --color "#E60012" --top 8
.venv/Scripts/python.exe -m beverage_color_db.cli match --color "#E60012,#004B93" --weights "0.8,0.2" --top 8
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo examples/portrait_sample.png --box 0.25,0.28,0.75,0.88
.venv/Scripts/python.exe -m beverage_color_db.cli match --photo examples/portrait_sample.png --compose --brand pepsi
.venv/Scripts/python.exe -m beverage_color_db.cli serve --port 8765
```

| 命令 | 作用 |
| --- | --- |
| `init` | 建表 + 写入品牌清单 + 首次安装时生成 `data/images/<key>_<中文名>/` 目录 + 图片实测/占位档案（不重建使用者删掉的目录） |
| `scan [--brands k1,k2] [--remove-bg] [--force]` | **采信模式**：扫描手动填图，无条件入库、提色、重建品牌档案 |
| `fetch --brands k1,k2 --source off,official,commons,local,manual --limit N` | 自动采集/下载（旧链路，保留；主线已改为手动放图） |
| `ingest [--force]` | 提主色、重建品牌主色画像 |
| `reindex-bg` | 就地重算 `bg_score` |
| `reconcile [--prune]` | 核对磁盘与 SQLite；默认只报告，`--prune` 删除无授权元数据的孤儿图片 |
| `report` | 生成 `quality.md` / `quality.json`、`ATTRIBUTION.md`、`gallery.html` |
| `match --color "#RRGGBB,#RRGGBB" [--weights w1,w2]` | 单色或多色联合匹配（默认等权） |
| `match --photo path [--box x1,y1,x2,y2] [--top N]` | 人像照片 → 识别衣服色板 → 多色联合匹配，生成结果页 |
| `match --photo path --compose [--brand key] [--style wrap\|print] [--out png]` | 追加生成合成图：默认包装贴图，可切平涂印花 |
| `serve [--port N] [--lan] [--host H]` | 本地上传网页（标准库 HTTP）；默认仅 `127.0.0.1`，`--lan` 开放局域网并打印手机地址 |
| `all` | init + fetch + ingest + report 一条龙（自动采集链路） |

### 在手机上打开（同一 Wi-Fi）

默认 `serve` 只监听 `127.0.0.1`，手机看不到。**有管理员权限**时最省事：

```powershell
# 管理员 PowerShell，放行一次 8765 入站
New-NetFirewallRule -DisplayName "Beverage 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```
```bash
.venv/Scripts/python.exe -m beverage_color_db.cli serve --lan --port 8765
```
启动后会打印 `手机（同一 Wi-Fi）打开： http://<局域网IP>:8765/`。

**没有管理员权限**（本机当前采用）：系统只允许已登记的程序接收入站连接，所以用系统**已放行的 Python 解释器**跑一个纯标准库 TCP 转发，应用本身只听回环，两个进程都要起：

```bash
# 终端 A：应用（回环 8766）
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, r'<项目绝对路径>'); from beverage_color_db import web; web.serve('127.0.0.1', 8766)"
# 终端 B：局域网转发（0.0.0.0:8765 → 127.0.0.1:8766），换成你机器上已放行的 python.exe
E:/anaconda3/envs/myenv/python.exe scripts/lan_proxy.py --listen-port 8765 --target-port 8766
```
手机与电脑都访问 `http://<局域网IP>:8765/`（本机当前 `http://10.21.196.178:8765/`）。扫码页与二维码：`report/phone_access.html`、`report/phone_qr.png`，IP 变了就重跑 `scripts/make_phone_qr.py --port 8765`（需要可选依赖 `qrcode`）。

**手机端适配**：输入框/下拉框字号 ≥16px 防 iOS 聚焦自动缩放；上传前浏览器把长边 >1600px 或 data URL >6MB 的照片缩到 1600px 并转 JPEG（顺带把 iPhone 的 HEIC 转成通用格式），服务端上限由 12MB 提到 24MB 兜底；合成图可长按保存到相册；页面本来就有 viewport 与 820px 断点，另加 480px 窄屏样式。

**打不开时**：① 手机与电脑必须同一 Wi-Fi，校园网/公司网常开「AP 隔离」禁止设备互访，可改用电脑开个人热点让手机连；② 电脑别休眠、服务别关（本机 `http://127.0.0.1:8765/` 能打开说明正常）；③ DHCP 换 IP 后看启动打印的新地址并重生成二维码；④ 照片只在本机内存/报告里处理，**不会上传到公网**，用完关掉两个进程即下线。

---

## 三、目录与数据模型

```
config/brands.yaml          55 个品牌（国内 29 / 海外 26），含别名、检索词、条码、参考主色
config/seed_colors.yaml     55 个品牌的人工占位主色（无图时建种子档案，放图后自动失效）
config/official_sites.yaml  国内品牌官网公开页清单（robots 抓取白名单）
config/manual_urls.yaml     手工 URL 兜底（默认关闭）
beverage_color_db/
  config.py                 路径 / 限速 / 质检阈值 / 缓存策略
  sources/                  off（品牌检索）/ official_site（robots）/ commons / local / manual
  quality.py                质检闸门 + 包装图质量分（自动采集链路用）
  fetch.py                  限速下载、闸门、pHash 去重、择优保留
  color.py                  pHash、sRGB↔Lab、KMeans 提色、CIEDE2000、纯色背景自动判别
  scan.py                   **采信模式**：手动填图入库 + 提色 + 档案升级/回退
  cloth.py                  **按色调分割衣服轮廓**：聚类 + 背景/肤色排除 + 领口检测 + 滚边补全 + 填洞羽化
  portrait.py               **人像衣服取色**：在分割蒙版内做 Lab KMeans + 轮廓调试图
  matcher.py                **多色联合匹配 + 结果报告**（JSON + 单文件 HTML）
  wrap.py                   **包装贴图纹理**：去瓶外白底（保留瓶体白）、补渐变、裁标签、主视觉窗口、染色增强、落点避让、重锐化、放大前后笔画重绘、三次插值曲面形变；不打印任何品牌字
  config/brand_wrap.yaml    **55 个品牌逐个调过的贴图参数**（裁剪框 / 主字窗口 / 染色 / 落点 / 重锐化 / 保留瓶体白）
  compose.py                **两种合成风格 + 小图自动高清化**：wrap 包装贴图（含光照调制）/ print 平涂主色 + 图案元素（默认无程序文字）
  web.py / web/index.html   **本地上传界面**（可清除重传 + 任选饮料 + 效果风格切换；标准库 http.server + 原生 JS）
  ingest.py                 提色入库、品牌主色画像
  report.py                 质检报告 + 署名清单 + gallery.html
  cli.py                    命令行入口
examples/portrait_sample.png 合成人像自测图（红衣，无版权问题）
data/images/<key>_<中文名>/    **手动放图的目录**；自动采集图也在这里（均不入库 GitHub）
data/cache/                 OFF 检索结果 / 官网页面 / robots.txt 缓存（不入库）
data/catalog.db             SQLite（不入库 GitHub）
report/gallery.html         逐张图片可视化复核页
report/match_result.html    人像匹配结果页（含上传照片，**不入库 GitHub**）
report/composite.png        合成图（含上传照片，**不入库 GitHub**；默认包装贴图，网页操作会覆盖）
report/composite_wrap_*.png 各品牌的包装贴图对照图（含上传照片，**不入库 GitHub**）
report/brands/<key>.png     55 个品牌逐品牌调优后的最终效果（含上传照片，**不入库 GitHub**）
report/brand_gallery.html   55 品牌最终效果总览（可切换早期版本 / 并排对比，内联人像，**不入库 GitHub**）
ATTRIBUTION.md              逐张署名清单（**需要提交**）
```

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| `images` | `path, source, source_url, source_page, license, attribution, w/h, phash, bg_score, trust, quality_json, status` | 图片元数据 + 合规署名 + 质检指标 |
| `color_features` | `image_id, color_rank, hex, rgb_*, lab_*, ratio, score, method` | 每张图 Top-N 主色 |
| `brand_color_profile` | `dominant_hex, lab_*, color_vector(JSON), confidence, sample_count, origin` | **第二阶段匹配入口**；`origin=images/reference` 区分实测与占位 |
| `fetch_log` | `brand_key, source, url, ok, message` | 拒收/失败原因可追溯 |

**提色分两路**（`color.py`）：

- `kmeans-lab-v2-front`（OFF / 官网 / 本地正面图）：包装占满画面，**不剔除背景**（四边就是包装），只做中心加权 + 温和压低近黑像素 → 按真实占比取主色。若沿用去背景逻辑会把红牛的深蓝抹成棕灰。
- `kmeans-lab-v2-bg`（Commons 兜底）：中心裁剪 78% → 四边估计背景并降权 → 饱和度/亮度加权 → Lab KMeans。

品牌 `dominant_hex` 由**质量分最高的那张图的 Top-1 色**决定；`color_vector` 则用该品牌所有图的 Top-N 颜色特征聚合成 2~5 色色板（图内占比 × 图片质量加权、相近色 ΔE<12 合并），供多色联合匹配使用。若主色与人工 `reference_hex` 偏差过大、而同品牌另一张明显更接近（ΔE≤15），才做一次择优校正；参考色不参与采集，也不参与第二阶段匹配算法。

---

## 四、自动采集阶段结果（2026-11-10 历史记录，当时清单 60 个品牌；现已被用户手动填图替换）

全量 `all --limit 5` 跑完当时 60 个品牌，约 19 分钟（主要耗时在 OFF 503 退避与官网限速）：

| 指标 | v1（Commons 主源） | v2（本次重做） |
| --- | --- | --- |
| **最终图片 / 有图品牌** | 163 / 39 | **135 / 36** |
| **OFF 标准正面包装图** | **1 张** | **96 张（71%）** |
| Commons 百科场景照 | **162 张（99%）** | 24 张（须过最严质检） |
| 官网公开产品页 | 0 | 15 张 |
| 质检闸门拒收 | 无此机制 | **471 张**（另有 77 张尺寸/MIME 不合格、18 张 pHash 重复） |
| 择优淘汰 | 先到先得 | 126 张（只留每品牌分最高的 5 张） |
| 干净图占比（bg_score≥0.8） | 10 / 163（6%） | 41 / 135（30%） |
| 参考色告警 | 9 / 39 | **6 / 36** |
| 平均图源可信度 | — | 0.953 |

> 注：`bg_score`（背景纯净度）在新方案下**不再作为质量判据** —— OFF 正面图四角就是包装，该指标天然偏低，用它评判会误杀好图。真正的改进证据是图源构成从「99% 百科场景照」变成「71% 标准产品正面图」。

采集链路：1041 个候选 → 下载 261 张 → 质检拒收 566 张 → 择优淘汰 126 张 → 最终 135 张 / 660 条颜色特征 / 36 个品牌主色画像。
磁盘与 SQLite 完全一致（`reconcile`：135 / 135，0 孤儿、0 缺失）。

主色抽样：可口可乐 `#FA120E`、雪碧 `#20A03B`、红牛 `#1A35AF`、七喜 `#159328`、魔爪 `#80BA53`、动乐 `#159FD3`、健力宝 `#277D3E`；`match #E60012` 首位可口可乐（ΔE 4.93）。
仍有部分品牌主色偏白/灰（依云、佳得乐、百事、农夫山泉、百岁山等）—— 它们的产品图本身是白底或浅色包装，属于已知限制，见第六节。

缺图品牌 24 个（多为国内小众品牌与部分官网为 SPA 的品牌），清单见 `report/quality.md`。

---

## 五、发布到 GitHub 前必读（授权）

1. **不要把图片二进制推到 GitHub**。`.gitignore` 已排除 `data/images/`、`data/cache/`、`data/*.db`。仓库只保留代码 + 清单 + 生成器。
2. **提交 `ATTRIBUTION.md`**，逐张列出来源、授权、作者、来源页，满足 CC BY-SA 署名要求。
3. OFF 图片 **CC BY-SA 3.0**、数据库 **ODbL**：需署名 + 相同方式共享。本项目只取图片 URL 与品牌名，未复制其产品数据库。
4. **官网图片（OFFICIAL-SITE）版权归品牌方所有**：本项目只记录来源页、不分发二进制，仅作非商用娱乐研究参考。抓取遵守 robots.txt、可识别 UA、每域名 1.5s 限速。若改变用途请重新核对每张图授权与商标使用规范。
5. `USER_PROVIDED` 授权由提供者自行确认，本项目不作背书。
6. 发布前把 `beverage_color_db/config.py` 的 `USER_AGENT` 改成你的真实仓库地址。
7. **`report/match_result.html` / `.json` / `composite.png` 内联或包含使用者上传的人像照片**，属于私有数据，`.gitignore` 已排除；`examples/portrait_sample.png` 是本项目自己画的合成图，可安全提交。合成图里的品牌名、字样与包装贴片仅作非商用娱乐，不要当官方物料使用或公开分发。
8. **仓库不随附任何图片二进制**。`data/images/`、`data/images_hd/`、`data/wardrobe/`（36 件衣橱资产）、`models/diffusion/`（4.0 GB 实验权重）均已由 `.gitignore` 排除；`report/` 只保留 3 张确认无人脸或五官不可辨认的展示图，其余含人像的对比图一律不入库。**因此本文档中引用的部分效果图在 GitHub 页面上会显示为裂图，这是刻意为之，不是仓库损坏**。clone 后按「快速开始」放入自己的图片，跑 `scan` 与 `scripts/import_wardrobe.py` 即可在本地重建全部素材与效果图。

---

## 六、已知限制 / 第二阶段路线

- **自动质检有上限**：纯颜色统计无法 100% 区分「产品图 / 宣传图 / 桌面随手拍」。限量版包装、暗背景图仍可能混入。用 `report/gallery.html` 逐张复核；删除不合适的文件后运行 `reconcile` + `ingest --force` 即可。
> 以下为 2026-11-10 自动采集阶段的结论，现已不适用：用户已手动填满 55 个品牌，缺图品牌已从清单移除。

- **国内品牌覆盖曾有限（历史）**：OFF 零收录，官网只对约 10 个可达域名生效，部分官网是 SPA 或只提供 banner，最终国内 9 个品牌有图、24 个品牌缺图。**补图主线已改为**放进 `data/images/<key>_<中文名>/` 后跑 `scan`（采信模式）；旧的 `fetch --source local` → `ingest --force` 链路仍可用。缺图品牌现在由 `seed_colors.yaml` 提供占位档案，因此匹配链路不会因为缺图而断掉。`reference_hex` 仅用于质检告警与画像择优校正，不参与采集与匹配算法。
- **部分品牌主色偏白/灰**：白底商品图或浅色包装（依云、佳得乐、百事、农夫山泉、百岁山等）会让 Top-1 色落在中性色上。已做近白降权，但完全自动仍有上限；可在 `gallery.html` 复核后手工补图，或用 `color_vector` 的多色信息辅助。
- **OFF 服务不稳定**：503 时自动退避重试，检索结果缓存 7 天；清 `data/cache/` 可强制刷新。
- **纯颜色无法唯一确定品牌**：可口可乐、王老吉、加多宝、农夫山泉都是红色。第二阶段需加品类、包装形状、OCR 或用户偏好消歧；`match` 目前只是色差排序演示。
- **衣服分割已升级为人体语义解析**：默认走 `human.py` 的 SegFormer-B2 模型（详见第九节），由模型给出衣服整体范围，再由 `cloth.py` 的色调逻辑补号码/印花/滚边并羽化；模型不可信时自动退回旧的「色调聚类 + 领口检测 + 逐行包络」。V 领、无袖、多色拼接、复杂背景比旧版明显更准，但它仍是离线解析 + 几何/颜色变换，不是生成式 AI 换装；多人重叠、极端侧身、透明/反光材质仍可能漏袖子或染到配饰。结果里的 `cloth_coverage`、`cloth_method`、`cloth_engine`、`fallback_mask` 会说明蒙版是否可靠，必要时用 `--engine tone` 切回旧算法，或调 `--box` / `CLOTH_*`。
- **衣服主色提取是启发式**：色板只在分割蒙版内用 Lab KMeans 提取，印花/号码会作为小占比色一起进入联合匹配。黑白灰衣服、彩色配饰抢主色、极端光照仍可能偏差；`report/match_result.html` 会画出分割轮廓与色板，先目视确认再采信排名。
- **包装贴图不是 AI 换装模型**：`wrap.py` 是纯本地几何/颜色变换，能复刻包装配色、图案、文字与衣服光影，但不会生成随布料自然起伏的立体艺术字，边缘也不如 AI 图生图 / PS 精修自然。源图分辨率低（例如康师傅 270×270、佳得乐 209×234）时笔画信息本身就不存在，已用小图自动高清化 + 笔画重绘缓解，但无法凭空还原印刷体细节；想再上一个台阶只能换高清正面包装图，或接入离线超分模型（本程序未分发超分权重；已分发的 52MB 权重是第九节的人体解析模型，用途不同）。主字位置不再依赖「默认 32%」这一假设：`config/brand_wrap.yaml` 已为 55 个品牌逐个标注裁剪框与主视觉窗口，源文件构图特别（白底、横版多品、主字贴底、纯 logo）的品牌都单独调过，效果见 `report/brand_gallery.html`；换用新包装图时按同样格式加一条即可。极端低质量图会自动回退 `print`，不会出一张破图。**可读性兜底只用包装原图**：早期版本曾用程序印刷体在衣服上补品牌名，观感像水印，现已彻底删除该逻辑与全部 `overlay_*` 配置，改用 `focus` / `word_y` / `zoom` 取到包装自带主字 + `crisp` 重锐化；源图实在没有可用主字时，衣服就只有配色与图案，不会再出现假字。白色罐体也按 `keep_body_white` 保留，不会被当成白底染掉。

---

## 七、一键启动与打包交付（2026-11-11）

解压版工程不需要手动敲命令，根目录已带引导脚本：

| 文件 | 用途 |
| --- | --- |
| `start.bat` | Windows 双击启动：自动建 `.venv` → 装依赖（首次需联网一次）→ 打开 `http://127.0.0.1:8765/` |
| `start_lan.bat` | 监听 `0.0.0.0`，手机直连 |
| `start_mobile.bat` | 手机访问「转发模式」，`0.0.0.0:8765 → 127.0.0.1:8766`，绕开 Windows 防火墙按解释器路径放行的问题（推荐） |
| `start.sh` / `start.py` | macOS / Linux 与命令行入口；`start.py --check` 自检，`--cli ...` 透传全部 CLI 子命令 |
| `START_HERE.txt` | 给使用者的中文快速上手说明 |
| `scripts/build_package.py` | 一键重新打包：导出独立数据库 + 逐文件 SHA256 清单 + 两个 zip |

```bash
# 重新打包（产物写到项目上一级目录）
.venv/Scripts/python.exe scripts/build_package.py
# beverage-color-db_portable.zip         运行必需：程序 + 数据库 + 55 张图 + 模型 + 配置 + 前端
# beverage-color-db_samples.zip          可选：生成的成果/对比图（含测试人像）
# SHA256SUMS.txt                        两个 zip 与 饮料换装.exe 的校验值（固定文件名，重打包不会变）
```

打包要点：`catalog.db` 通过 SQLite `backup()` 导出并切回 `DELETE` 日志模式，单个文件即可打开，不依赖 `-wal` / `-shm`；`.venv`（277MB 且绑定本机 Python 路径）、`__pycache__`、临时缓存、含人像的结果页一律不打包。已实测在全新解压目录中通过 `compileall`、55 品牌/55 图/55 档案/271 色特征核对、多色匹配、人像包装贴图合成、网页服务与 `/api/stats`、`/api/brands`、`/api/match`、`/api/compose` 全部接口。

---

## 八、Windows 单文件 exe（2026-11-11）

`app_main.py` 是冻结环境入口，`scripts/build_exe.py` 用 PyInstaller 生成 `饮料换装.exe`（143.7MB，Windows x64，无需 Python、无需联网；内含 52MB 语义模型、26MB 深度模型、55 张离线超分素材与 onnxruntime）。

**核心处理**：PyInstaller onefile 把资源解压到临时目录 `sys._MEIPASS`，退出即删，数据库和结果图不能写在那儿。因此 `app_main.py` 在**导入任何业务模块之前**：① 把内置的 `config/`、`data/`、`web/`、`examples/`、`models/` 以「只补缺失、绝不覆盖」的方式释放到 exe 同目录的 `饮料换装_数据/`（不可写时回退 `%LOCALAPPDATA%\DrinkWrapData`）；② 遍历 `config` 模块里所有 `Path` 常量，把位于原 ROOT 下的路径整体重定向到该目录。`web.INDEX_FILE`、`wrap._OVERRIDE_FILE` 这类 import 期常量因此也能正确指向新位置，业务代码零改动。

```bash
.venv/Scripts/python.exe -m pip install pyinstaller==6.22.3
.venv/Scripts/python.exe scripts/build_exe.py
# -> 工作区根目录 饮料换装.exe
```

打包要点：`data/catalog.db` 用 SQLite `backup()` 导出成独立单文件；显式收集 `sklearn.utils._typedefs` / `_heap` / `_sorting` / `sklearn.neighbors._partition_nodes` 与 `qrcode.image.pil`；用 `--collect-binaries onnxruntime` 收集动态加载的推理 DLL（否则 exe 里 `import onnxruntime` 会失败并静默降级成色调引擎）；排除 `aiohttp`（exe 只走 web → matcher/compose → cloth/wrap，运行时不 import 采集链路）、`tkinter`、`matplotlib` 等以控制体积；图标由 Pillow 现场绘制。exe 支持 `--lan` / `--port`（占用时自动往上找空闲端口）/ `--no-browser` / `--check` / `--data-dir`；`--lan` 会生成局域网二维码页。若旧的 `饮料换装.exe` 仍在运行，Windows 会锁住目标文件，此时脚本**不判构建失败**：dist 里的新 exe 完整可用，脚本把同一份文件另存为 `饮料换装_新版.exe`，`build_package.py` 也会把它一并写进 SHA 清单；关掉旧的黑色控制台窗口后改名替换即可。

**实测**（全新空目录、只放一个 exe，无 Python 无源码）：`--check` 约 16 秒，明确报告 `语义模型 存在=True 推理库=True 可加载=True`；首次释放 69 个资源文件（含 52.2MB 模型）；服务约 12 秒可访问；首页、`/api/stats`、`/api/brands`、`/api/match`、`/api/compose`（六个核桃 wrap、无回退、480×657）全部通过，且 `/api/match` 与合成结果里的 `engine` / `cloth_engine` 均为 `model`（语义模型而非静默降级）；`/api/match` 约 7.5 秒、`/api/compose` 约 3.1 秒。源码主包解压副本同样实测 `human-parse-v1 / engine=model`。注意未签名 exe 会触发 SmartScreen，需「更多信息 → 仍要运行」。

---

## 九、衣服分割升级：人体语义解析（2026-11-11 更新）

### 为什么要换

原来的 `cloth.py` 是「Lab 色调聚类 + 几何先验」启发式：先找一个主色种子，再假设**每一行衣服都是一条连续横带**，把左右边界之间整段填满。遇到多色拼接、衣服与背景同色、复杂背景、侧身、袖子分离时，常常只认出胸前一块。

现在默认走 C 档：用 **SegFormer-B2 人体解析模型**（ATR 数据集微调，18 类标签：Background / Hair / Upper-clothes / Skirt / Pants / Dress / Face / Arms / Bag / Scarf ……）先给出「哪里是衣服」的整体范围，再交给原来的色调逻辑处理号码、印花、滚边与羽化。模型不可信时自动退回旧算法。

### 模型与本地推理

| 项 | 值 |
| --- | --- |
| 上游 | `mattmdjaga/segformer_b2_clothes`（Apache-2.0，见 `ATTRIBUTION.md`） |
| 权重 | `models/segformer_clothes.onnx`，52.2MB，int8 动态量化（仅 MatMul；全算子量化会生成 ORT CPU 不支持的 `ConvInteger`） |
| 输入 / 输出 | 512×512 RGB（ImageNet 归一化）→ 18×128×128 logits |
| 推理 | `onnxruntime` CPU，纯本地，不联网、不上传照片；单张约 1–2 秒 |
| 获取 | `.venv/Scripts/python.exe scripts/fetch_model.py`（国内走 hf-mirror；自动量化 + 前向自检） |

`human.py` 惰性加载会话并缓存最近一次推理：`analyze` / `compose` / `debug_image` 会以不同分辨率反复调用同一张照片，用 24×24 缩略图相关系数（`HUMAN_CACHE_SIMILARITY`）判定为同一张时复用低分辨率类别图，只做一次推理。

### 流水线与保险

1. 模型 `Upper-clothes / Skirt / Dress` → 语义上衣区；
2. `binary_fill_holes` 把号码、字母、网眼、印花并入衣服，底边补两行处理压到画面边缘的号码；
3. 在衣服区域取占比最大的 3 个 Lab 颜色中心，沿轮廓窄带补回被模型判成手臂/高光的像素；
4. 连通域清理：只保留主体 + 与之相邻且足够大的域（挡住 128×128 上采样在背景上撒的碎块）；
5. 扣除模型背景与原 RGB+YCbCr 肤色，形态学平滑，窄高斯羽化成 0~1 软蒙版。

**自动降级**（结果 `semantic_note` 会写明原因）：模型权重或 `onnxruntime` 缺失、推理异常、模型判出的人像占画面 <12%（插画 / 纯色示意图 / 远景）、上衣占比异常、或上衣占人像比例 <15%。降级后 `engine=tone`，返回字段结构一致，`compose` / `portrait` / 网页都无需特殊处理。

### 三种引擎

| 位置 | 用法 |
| --- | --- |
| 网页 | 「识别参数 → 衣服分割引擎」：自动（推荐）/ 语义模型 / 色调启发式 |
| CLI | `match --photo ... --engine auto\|model\|tone` |
| 全局默认 | `config.HUMAN_SEGMENT_PRIORITY`（`auto` / `model` / `tone`） |

结果里 `engine` 是**实际生效**的引擎，`method` 为 `human-parse-v1` 或 `cloth-tone-v2`；合成图信息含 `cloth_engine`；`/api/stats` 返回 `human` 字段（模型是否存在、推理库是否就绪、体积）。

### 实测

- 真实球衣照（171×234）：`human-parse-v1`，覆盖 32.7%，V 领/肩线/号码完整，背景无误染；结果图 480×657、wrap 无回退。
- 纯色矩形示意图（400×600）：模型只认出 7.4% 人像 → 自动降级 `cloth-tone-v2`，覆盖 25.0%，整块红上衣正确识别。
- `compileall`、前端 JS 语法、CLI 三引擎、网页 `/api/stats` `/api/match` `/api/compose` 三引擎 + 六个核桃贴图合成，全部通过。

### 已知限制

- 模型在**卡通 / 合成示意图 / 极远景**上会失效，靠自动降级兜底。
- 128×128 类别图上采样后边缘仍有几像素锯齿；可用 `HUMAN_COMPONENT_*` / `HUMAN_TONE_DILATE` 微调。
- 整块区域生长默认关闭（`HUMAN_REGION_GROW=False`）：它能补漏判的袖子，但实测也会把背景碎块放大成竖条，需要时再显式打开。
- 这仍是「按语义区域做几何/颜色变换」，不是生成式 AI 换装。

## 十、包装贴图升级：连续纹理 + 逐行包络（2026-11-12）

### 问题

旧 `wrap.py` 是**双层结构**：先把整张标签高斯模糊成一片渐变底，再把等比放大的清晰标签窗口用 `canvas[dy1:dy2, dx1:dx2] = big[...]` 硬贴在胸口，四条边没有羽化。于是衣服上出现一条水平硬边（实测 y=408 上下行间 RGB 平均跳变 **22.24**）和两侧竖条，看起来像「花纹换了一版」。标签左右留白又用逐行中位色补，放大后是扫描线式横纹。

### 现在的实现

`build_texture(key, shape, bbox, cloth_mask=None)` 默认走 `WRAP_CONTINUOUS`：

1. `_mirror_sides` 用镜像延展代替逐行中位色补边，消除两侧横条；
2. `_continuous_texture` 只把标签**等比放大一次、笔画重绘一次**，整张画布由这一张图连续采样（`ndi.map_coordinates`, `order=3`）；坐标超出标签范围用 `WRAP_EXTEND_MODE=nearest` 把边缘行/列平推出去。函数值连续、不会出现镜像反射那种上下颠倒的重复文字；
3. `_envelope_profile` 从衣服硬蒙版求逐行中心与半宽（纵向高斯平滑、`WRAP_ENVELOPE_RANGE` 限幅），横向坐标据此轻度收放 —— 肩线 / V 领处花纹跟着衣服走，不再是一个轴对齐矩形横穿身体；
4. 几何（`tw / th / win_h / y0 / y_word / word_y`）沿用原 `_label_window`，**55 个品牌逐个调好的 `zoom / word_y / focus` 完全不变**；`keep_body_white`、`crisp` 等也照常生效。

`compose.compose()` 把 `seg["hard"]` 作为 `cloth_mask` 传入；脚本单独调用时不传也能生成无接缝纹理，只是不做轮廓收放。逐品牌在 `config/brand_wrap.yaml` 写 `continuous: 0` 可单独退回旧路径（`config.WRAP_CONTINUOUS=False` 全局退回）。

| 旋钮 | 默认 | 作用 |
| --- | --- | --- |
| `WRAP_CONTINUOUS` / `continuous` | `True` | 连续纹理总开关 |
| `WRAP_EXTEND_MODE` / `extend_mode` | `nearest` | `nearest` 平推（接缝 0.03）/ `reflect` 镜像（接缝 7.86，有倒字） |
| `WRAP_SIDE_MIRROR` / `side_mirror` | `True` | 左右镜像补边；关掉回到逐行中位色 |
| `WRAP_ENVELOPE` / `envelope` | `0.55` | 逐行包络强度，0 = 纯平面 |
| `WRAP_ENVELOPE_SMOOTH` | `0.02` | 包络纵向平滑半径（相对画布高） |
| `WRAP_ENVELOPE_RANGE` | `0.28` | 单行最大压缩 / 拉伸 ±28% |

### 实测（真实球衣照，480×657，六个核桃）

| 指标 | 旧 | 新 |
| --- | --- | --- |
| y=408 接缝行间跳变 | 22.24 | **0.03** |
| 衣服框内逐行跳变 p90 | 8.42 | 7.26 |
| 55 品牌「孤立硬边尖峰」中位数 | 3.95 | **2.05**（45/55 下降，最差 34.12 → 10.81） |
| 55 品牌贴图成功 / 回退 | — | **55 / 0** |

对比图：`report/wrap_continuity_before_after.png`、`report/_diag_extend_mode.png`（三品牌 × 两种延展方式）、`report/_diag_continuous_seam_zoom.png`。全量回归还覆盖 `compileall`、逐行包络平滑、白色罐体保留、无程序印刷字、CLI 三品牌合成、网页 `/api/stats` `/api/brands` `/api/match` `/api/compose`（`engine=model`、wrap 无回退），共 28 项全通过。

### 边界

`nearest` 平推会让标签边界之外的区域变成同一色带的竖直延展（不再是清晰图案），这是「宁可没有花纹也不要硬接缝」的取舍；10 个高对比包装品牌的孤立尖峰轻微上升（≤1.01，绝对值仍 <5.3），来源是包装自身笔画边缘，不是接缝。想要更多图案重复可改 `extend_mode: reflect`，但会带回镜像倒字。

## 十一、原球衣字样残留修复：衣服轮廓内实心覆盖（2026-11-12）

### 现象

黄色球衣换装后，包装白色艺术字里仍混着 `LAKERS` / `24` 的橙黄碎斑与斜线，看起来像「原来的字没盖干净」。

### 根因

`human-parse-v1` 会把白色号码、字母和深色描边判成 Background / 非上衣；分割阶段后续「减去背景 / 减去皮肤」会在衣服中间挖出封闭洞，`_soft_from_hard` 的窄羽化又让洞缘 alpha 落在 0~1，于是 `arr*(1-m) + target*m` 把原字样以**半透明淡影**混进包装贴图。实测字样区 mask 均值仅 0.899、`mask<0.5` 占 10.4%；而光照场在字样区与布料区的明度差是 -0.001 —— 不是明暗「幽灵轮廓」，是覆盖权重本身漏了。

### 实现

`compose._solid_cover_mask()` 对 `hard` 做 `binary_fill_holes`（底部补两行，让压到画面下缘的号码也成为封闭洞），把衣服轮廓内部的 alpha 强制为 1，只在**外轮廓**保留原有羽化。V 领 / 袖口 / 手臂 / 背景都是与画面外部连通的开放缺口，`binary_fill_holes` 不会填，所以脸、脖子、皮肤保持原样。`wrap` 与 `print` 两种风格、三个引擎共用；包络形变与光照场仍用原始 mask，55 个品牌逐个调好的贴图几何完全不变。

* 开关：`config.COMPOSE_FILL_CLOTH_HOLES`（默认 `True`；设为 `False` 逐像素回到旧行为）
* 观测：CLI 与 `/api/compose` 结果新增 `cloth_holes_filled`
* 实测（480×657 真实球衣）：补回 9,161 px，全图仅 2.69% 像素变化、脸部 0 改动；回归 15/15 通过（55 品牌连续贴图、wrap/print × model/tone、开关回退、无程序印刷字）
* 对比图：`report/lettering_before_after.png`

## 十二、衣服边缘锯齿修复：亚像素轮廓 + 抗锯齿过渡（2026-11-12）

### 现象与根因

肩带、袖口与背景交界处有一格一格的方块阶梯。根因不在 alpha 混合，而在 `human.parse()`：模型输出是 128×128 类别图，用 `NEAREST` 直接放大到 480×657，一个类别块约 3.7px；`cloth._soft_from_hard` 的羽化又极窄（最外 1px 环带的 mask 均值 0.993，几乎硬切），于是阶梯被原样印到合成图上。旁边那份双线性上采样的 `upper_prob` 只用于软融合，没参与轮廓生成。

### 实现

* `human._smooth_labels()`：类别图 → one-hot → **双线性上采样 → argmax**，得到亚像素边界；`upper / background / skin / person` 等字段一起平滑，避免后续「减去背景/皮肤」把阶梯带回来。开关 `config.HUMAN_SMOOTH_LABELS`（默认 `True`）。
* `compose._solid_cover_mask()`：外轮廓改用有符号距离场的亚像素 alpha，在 `COMPOSE_EDGE_FEATHER=1.2` px 内做 0→1 渐变；衣服内部（含号码/字母洞）仍强制完全覆盖，所以第十一节的字样修复不会被羽化破坏。设为 `0` 回到硬边。
* 只改覆盖权重，包络形变与光照场仍用原始 mask，55 品牌逐个调好的取景不变。

### 实测

轮廓逐行跳变：左边界 `|dx|` 0.58 → **0.51**、>2px 比例 5.4% → **3.7%**；右边界 1.07 → **1.00**、6.8% → **5.9%**；衣服覆盖率 0.3256 → 0.3271（几乎不变）。回归 **16/16** 通过（55 品牌贴图、wrap/print × model/tone、开关回退、脸部 0 改动、99%+ 改动落在轮廓 4px 邻域内）。对比图：`report/edge_before_after.png`、`report/edge_zoom_before_after.png`。

### 边界

`nearest` 平推在标签左右边界之外仍是同色带，几何平滑后肩带外侧可能出现一条很细的橙色竖边 —— 这是第十节记录过的平推限制，不是锯齿。要彻底去掉需扩展侧边留白或改延展方式，会动到 55 品牌已调好的取景。

## 十三、渲染层 AI 化：高清素材 + 神经形变场（2026-11-12）

### 原则：AI 只输出坐标，不输出像素

本项目不是通用虚拟试衣，核心是**让使用者看清衣服上是哪一款饮料**。2026 年的 VTON 研究（FitDiT、PG-VTON、SIFT-VTON）仍把「小字 / logo 保真」当作主要难题，扩散式重绘会把「芬达」变成乱码；而且项目要求 120MB 离线 exe、纯 CPU、几秒出图、55 品牌逐项可调且可回归。所以渲染层 AI 化的边界是：

> 深度模型预测 **2D 采样坐标场**，`ndi.map_coordinates` 仍从**原包装标签**取像素；品牌字样不参与任何生成。

`wrap.py::_continuous_texture` 里的 `coords = np.stack([sy, sx])` 就是这个咽喉。原先 `sy` 是常数平移、`sx` 只按 `_envelope_profile()` 逐行宽度收放（1D），所以花纹是平贴的；现在由 AI 视差场 + 轮廓柱面场叠加成 2D 形变。

### R2：55 张源图离线超分

`scripts/upscale_brands.py` 用 4.8MB 的 `RealESR_General_x4` ONNX 把 55 张包装图预处理成 `data/images_hd/`。**只注入模型学到的高频残差**，低频结构严格取自原图：

```
base   = Lanczos(原图, 4x 尺寸)          # 字形结构
detail = SR(feed) - Lanczos(down(SR))    # 模型新增高频笔画
out    = clip(base + 0.7 * detail)
```

每张把结果下采样回原尺寸做 PSNR 校验，低于 34dB 自动降 alpha。实测 **min 36.72 / mean 42.64 dB**，55 张合计仅 **2.8MB**，运行时零成本。`wrap.brand_image_path()` 优先读 HD；删掉 `data/images_hd/` 或设 `WRAP_USE_HD_SOURCE=False` 即逐像素回到原素材。源图一个字节都没改。

### R3：深度驱动的 2D warp 场

`depth.py` 加载 26MB int8 的 Depth Anything V2 Small（Apache-2.0），输出与画面同尺寸的 0~1 视差场；`wrap._warp_offsets()` 把它转成 `(dy, dx)` 采样偏移：

* **只用低通后的大尺度形体**（`WRAP_DEPTH_SMOOTH=0.12`）：胸口整体比肩部近，视差平移让图案绕身体弯曲。柱面分量由衣服轮廓距离场构造，补足立体弧线。
* 三道保护：轮廓边界淡出、主字区按 `word_y` 高斯降幅（`WRAP_DEPTH_TEXT_PROTECT=0.55`）、单点位移钳制（`WRAP_DEPTH_MAX_SHIFT=8px`）。
* 模型缺失 / 推理失败自动降级到旧的 1D 包络，`/api/compose` 返回 `depth_warp=false`，不崩、不改构图。逐品牌可在 `brand_wrap.yaml` 写 `depth_warp: 0` 单独回退。

**三个踩坑**（都已写进回归）：

1. 原图明度高频想补布料质感，但**号码/字母的轮廓线本身就是高频**，减低频也去不掉 → `WRAP_DEPTH_DETAIL_SCALE` 默认关闭。
2. 单目深度模型会把球衣印花**误判成表面凹凸**，对深度做高通会把 LAKERS/24 印进 warp 场 → 改用低通趋势。
3. 真正的污染源是 `seg["hard"]` 里被语义分割挖出的**字样形状空洞**：`distance_transform_edt` 在洞内为 0，边界淡出把该处位移直接归零 → 先 `binary_fill_holes` 且**底部补两行**（压到画面下缘的号码不是封闭洞），几何场才连续。

### 实测与开关

39 项回归全绿（原 16 项 + R3 专项 23 项）：55/55 贴图成功、55/55 走 warp 场、字样洞区域位移比为 1.29（不再归零）、warp 场梯度 p99 = 1.63（无尖峰）、主字区位移 2.05px 低于整体 4.18px、脸部零改动、模型缺失自动降级。6 个重点品牌（康师傅 / 芬达 / 六个核桃 / 娃哈哈 / 雪碧 / 可口可乐）抽查无跑版、白罐体保留。

| 开关 | 默认 | 作用 |
| --- | --- | --- |
| `WRAP_USE_HD_SOURCE` | `True` | 优先读离线超分素材 |
| `WRAP_DEPTH_WARP` | `True` | 启用 AI 形变坐标场 |
| `WRAP_DEPTH_SCALE` / `CYL_SCALE` | 7 / 4 px | 视差与柱面幅度 |
| `WRAP_DEPTH_MAX_SHIFT` | 8 px | 位移上限 |
| `WRAP_DEPTH_DETAIL_SCALE` | `0` | 明度高频通道（默认关闭，见踩坑 1） |

结果新增字段：`depth_warp`、`wrap_hd_source`；`/api/stats` 新增 `depth`、`hd_sources`。对比图 `report/r3_triple.png`、`r3_triple_zoom.png`、`r3_warp_magnitude.png`、`r3_brands_grid.png`。

**已知限制**：Depth Anything V2 Small 在这类人像上输出很平滑，只有胸廓级起伏、没有布料褶皱级细节，所以 R3 是「大尺度立体弯曲」而非「褶皱模拟」。要褶皱级效果需要法线/材质估计或生成式模型，前者会增大包体、后者会破坏文字保真，暂不做。

---

## 十四、袖子 / 上臂排除（语义层的边界）

**语义模型能区分「裸露的手臂」，但不能区分「穿着袖子的手臂」。** ATR 18 类里只有
`Left-arm(14)` / `Right-arm(15)`（皮肤），没有「袖子」类；穿长袖外套时，模型把整件
外套（含两只袖子）统一判成 `Upper-clothes(4)`。实测基准 01（黑皮夹克人像）：

| 量 | 值 |
| --- | --- |
| `Left/Right-arm` 类像素 | ≈0（模型完全没判出裸臂） |
| `Upper-clothes` 占比 | 37.61%（整件夹克含袖） |
| 衣服 mask 落在左右侧翼/袖臂区 | **40.0%** |
| 只扣 `arm` 标签能去掉 | 274 px（0.2%，几乎无效） |

科比无袖球衣则相反：模型明确判出 15.4% 的裸臂，而其中只有 5.47% 被覆盖 —— 也就是说
**裸臂本来就没被贴到**，用户看到的「图案延伸到手臂」其实是**袖子**。

另有一处历史设计会加重问题：`cloth._semantic_expand()` 的注释写明「模型会把袖子判成
Left/Right-arm，这里把它们补回来」，且 `allowed` 不排除语义 arm 类，因此区域生长会主动
把袖子并回衣服范围。

**实现**：`cloth._trim_sleeves()` 在所有扩张/减法**之后**，沿每行 `info["person"]` 的
左右边界按同一比例向内收窄，边界随肩线与躯干外形弯曲（中值滤波消除逐行抖动），
而不是切一条竖直硬线。

| 常量 | 默认 | 说明 |
| --- | --- | --- |
| `CLOTH_SLEEVE_TRIM` | `0.18` | 每行内缩比例；`0` = 关闭，回到「整件上衣含袖子一起换装」 |
| `CLOTH_SLEEVE_TRIM_SMOOTH` | `11` | 边界沿 y 的中值滤波窗口（行） |

只在语义模型路径生效（需要 `person` 轮廓）；`tone` 引擎与 `print` 风格不变。
`CLOTH_SLEEVE_TRIM=0` 时与改动前**逐像素一致**（差 >3 的像素 0 个）。
结果新增 `cloth_sleeves_trimmed`（被排除的像素数）。

**方案对比**（`report/baseline/_diag_arm_variants_*.png`）：扣 arm 标签 0.2% 无效；
固定竖直硬切有效但留下笔直裁切线；逐行收窄 18% 最佳；收 26% 会切断包装主字；
「每行只留主干连续段」会把无袖球衣切碎并露出原号码。39 项回归全绿。

**收窄后脖子出现白块：根因与修复**（用户反馈基准 01 第二张）：收窄让衣服 `bbox`
从 347px 变 222px，而 `wrap` 的贴图取景直接由这个 bbox 推导 —— `tw = bw ×
WRAP_FILL_W` 变小 → `win_h = lw × th / tw` 从 814 涨到 1197 → 取景窗口上沿
`y0` 被 clip 到 0 → 标签最顶部的**罐口银白金属边**进入画面，领口处的采样行
从 `full` 的第 118 行跳到第 9 行，于是脖子下方出现一整块纯白、左肩出现银灰高光。
这不是遮罩漏盖，也不是原图残留（同点原图 RGB 115/125/117、贴图 canvas 为黑）。

修法：**袖子排除只改覆盖遮罩，不改贴图取景**。`cloth._semantic_segment()` 在收窄前
另存 `wrap_bbox` / `wrap_hard`，`compose` 用它们调 `build_texture()`；`mask` / `hard`
仍用收窄后的版本做最终覆盖。这样 55 个品牌逐个调好的缩放、主字位置与取景完全
不受袖子开关影响，罐顶银白边不再进入领口。
`report/_smoke_neck_white.py` 固化判据：开关两版传给 build_texture 的 bbox 相同、
躯干中央平均差 0.603、侧翼差 11.59（袖子仍排除）、领口新增白块 **0 px**；
39 项回归全绿，`CLOTH_SLEEVE_TRIM=0` 仍逐像素回退。
对比图 `report/neck_white_before_after.png`（左修复前 / 右修复后）。

对比图：`report/baseline/sleeves_monster_before_after.png`、
`sleeves_kobe_before_after.png`、`_diag_arm_monster.png`、`_diag_arm_kobe.png`。

---

## 十五、基准衣服迁移：识别切割 → 拉伸变形 → 覆盖（2026-11-13）

**为什么换范式**：连续多轮不满意的点（整罐标签铺满、logo 重复 3~4 条、领口消失、
图案不随褶皱透视变形）都源于「把一张矩形商品标签贴到 mask 上」。基准成品图里
已经包含真实布料、随胸廓的透视、单个居中 logo 与大面积负空间，直接把**衣服本身**
切下来做几何迁移，比让程序凭规则生成版式更接近目标。

**实证排除的两种错误用法**（`report/_exp_baseline_as_texture.png`）：把整张基准图当
贴图会把别人的脸/肩膀/蓝背景贴到胸口；只裁衣服**外接矩形**仍会带进 V 领、皮肤与
残缺字样。关键是必须用语义 mask **精确切割**，而不是矩形。

**三段流程**

1. **识别与切割（离线，只跑一次）** — `scripts/build_garments.py` 读
   `report/baseline/samples/` 的 8 张基准，用 SegFormer 精确分割衣服，
   `beverage_color_db/garment.py::row_profile()` 求逐行轮廓、`row_fill()` 把每行
   补成「只含衣服像素」的条带。两道保护：有效行按 `行宽 >= 峰值宽度 × 0.45`
   筛选并取最长连续段（排除领口尖角行/下摆碎行，否则目标领口会采到源背景）；
   无有效行的行复制最近条带行而不是保留背景。产物落到 `data/garments/<id>/`：
   `strip.png`（条带）、`profile.npz`（逐行 center/half/y0/y1）、`mask.png`、`meta.json`。
   共 8 件、约 2.6MB。
2. **拉伸变形（运行时，无模型）** — `garment.build_texture()` 用源/目标两套逐行
   轮廓做归一化映射：纵向 `v=(y-y0d)/(y1d-y0d) → sy=y0s+v·(y1s-y0s)`；
   横向 `u=(x-center_d(y))/half_d(y) ∈[-1,1] → sx=center_s(sy)+u·half_s(sy)`；
   三次插值从 strip 采样出整幅纹理。图案因此随目标肩线、领口、躯干弧度变形。
3. **覆盖** — `compose.py` 新增 `style=garment`，与 `wrap` **共用同一套**光照场、
   覆盖遮罩、软边羽化与下载链路；只替换纹理来源。资产缺失/损坏自动回退 `wrap`。

**参数**（`config.py`，改后需重跑 `scripts/build_garments.py`）：
`GARMENT_WIDTH_RATIO=0.45` 有效行筛选阈值；`GARMENT_SHRINK_LOCAL=0.30`
（local_print 档向内收窄，排除两侧外套）、`GARMENT_SHRINK_FULL=0.0`（full_wrap
档不收窄，保住满版主字）。

**接口与用法**
- 网页「合成效果」→「基准衣服迁移」，下拉选 8 件基准之一 →
  「用这件基准衣服生成新图」。
- `GET /api/garments` 返回资产清单；`POST /api/match` / `/api/compose` 支持
  `style=garment` + `garment_id`；不传 id 时自动取首件。
- `/api/stats` 增加 `garments`；合成结果新增 `garment_id` / `garment_meta` /
  `garment_warp_rows` / `compose_style_label="基准衣服迁移"`。

**验收**
- `report/_smoke_garment.py` **14/14**：`style=garment` 与实验样张平均像素差
  **0.001**（strip 经 uint8 量化）、8 件资产全可出图、躯干外（排除 4px 羽化带）
  **差 0.0000**、资产缺失回退 wrap **差 0.0000**。
- `report/_smoke_garment_api.py` **8/8**：清单/统计/match/compose/缺省 id/未知 id。
- 原 **39 项回归 39/39** 全绿；改动前后 `wrap` 成图逐像素一致（不同像素 0）。
- 宫格 `report/_smoke_garment_grid.png`：王老吉（纯红底 + 单个竖排字）与
  魔爪（黑底 + 单个绿爪痕）已命中 `local_print` 版式；西装外套、白衬衫领、
  脸、手、讲台 8/8 保持原样，无别人的脸/背景/水印混入。

**已知限制**：目标端仍用语义模型的 Upper-clothes mask，多层着装（西装 + 衬衫 +
V 领背心）会被判成单块梯形，覆盖边界仍是笔直斜线，`full_wrap` 档因此改善有限；
源端若把外套也判进 Upper-clothes，会带入少量外套反光。这两处属于**目标端分割
语义**问题，不是形变算法问题，后续需要「外层外套不换、只换内层」的层级判断。
另：`report/_smoke_garment_api.py` 会把 `MATCH_RESULT_JSON/HTML` 重定向到
`report/_api_smoke/`，避免 `/api/match` 覆盖回归脚本依赖的 `match_result.json`。

---

## 十六、衣橱重构：饮料库整体换成「已换装人像」（2026-11-16）

**这次换的是库的语义**。原来的库是 55 个品牌 × 饮料包装图，换装靠「把包装图贴到
衣服上」；现在的库是 **36 件已经换好装的人像实拍**（球星/名人/素人穿着印好图案的
球衣或背心），换装靠「把基准人像上的那件衣服整件切下来，拉伸变形后套到输入人像
身上」。渲染内核复用第十五节的 `garment` 引擎，所以运行时依旧**不加载任何生成
模型**：分割模型只离线跑一次，出图靠几何映射。

### 16.1 素材端的衣服提取（和贴图档不同的分割）

直接拿贴图档的 `cloth.segment()` 切素材会翻车，实证两个典型：

- 金色 T 恤 / 白底球衣：`exclude_skin` 把大面积高亮度布料当皮肤啃掉一圈；
- 白背心 + 浅色背景：模型把整件判成 Background，只剩胸标一小块。

所以素材端单独一套 `wardrobe.material_mask()`：

1. `human.parse()` 取 `upper_prob` 概率场，按 `0.5 / 0.35 / 0.22 / 0.14` 逐级降阈，
   取面积够的第一档（避免模型只在强置信区域给分时面积塌掉）；
2. 对衣服像素做 Lab 主色聚类（`k<=6`，占比 ≥6% 的簇留下），用「到最近主色的
   距离 < 14」把同色但被阴影压暗的边缘吃回来，共迭代 3 轮；
3. 保护类（脸/头发/四肢/下装）先膨胀 2px 做硬排除；
4. 关掉开襟夹克两侧透出的背景：`border_background()` 从边框采样底色，
   **填洞时绕开背景色**（否则开襟处的蓝背景会被当成"洞"填进衣服里，条带两侧
   留一条蓝竖带）；
5. 平滑后再减一次背景 —— `_smooth_edges()` 会按逐行 `left..right` 重建，
   把手臂与躯干之间那条缝又填回来；
6. 采样蒙版内缩 1px（最外圈是「布料 × 背景」的抗锯齿混色），条带边缘因此
   由内部布料插值得到，不会自带背景色。

### 16.2 条带化：为什么不能填洞

`garment.row_fill()` 本身就是「逐行只看 `[left,right]`、区间内用有效像素水平
插值、区间外平推边缘值」。所以**条带端不能再 `binary_fill_holes`**：填洞会把
领口、开襟这些"洞"标成有效像素，肤色与底色就被采进纹理。正确做法是不填洞，
让 `row_fill` 去插值。

领口洞另外单独处理：`_silhouette()` 只用于生成 alpha（覆盖范围），条带用带洞
蒙版。另外顶部 15% 高度做了一次「必须接近衣服自身色板」的过滤，因为素材人像
的脖子会伸进衣服 mask 顶部，形成一条肉色横带（魔爪黑皮衣实测最明显）。

### 16.3 资产与导入

`.venv/Scripts/python.exe scripts/import_wardrobe.py`

- 读 `<你的目录>/饮料/<品牌>/`（默认 `~/Desktop/饮料/`，可用 `--src` 覆盖），每个文件夹一张图，自动 `slugify`
  成英文 id（`王老吉 → wanglaoji`）；
- 短边 < 260px 的素材做 4x 超分（原图普遍 176×176 ~ 270×360，直接拉大到
  人像尺寸会糊成一片），再按 `WARDROBE_MAX_SIDE=760` 压回并转 WebP；
- 产物 `data/wardrobe/<id>/`：`strip.webp`（衣服条带）、`mask.png`（衣服蒙版）、
  `profile.npz`（逐行 center/half/有效行）、`meta.json`（中文名、色板、
  超分信息、源尺寸）、`thumb.jpg`（网页列表缩略图）。36 件共 2.8MB。
- 导入会清空旧的 `data/garments`（`--keep-old` 可保留）。

### 16.4 覆盖版型：谁决定轮廓

`compose.py` 里 `style=garment` 的覆盖范围由 `COMPOSE_GARMENT_SHAPE` 决定：

- `target`（默认）：覆盖 `hard`（目标人像自己的衣服范围），边缘干净、不漏原色；
- `source`：覆盖 `hard ∩ 源剪影(alpha)`，版型更接近基准，但基准比目标窄时
  两侧会露原色竖带，需要更大外扩去盖。

换装档额外两个开关（贴图档不受影响）：

- `sleeve_trim=0.0` —— 整件替换，收窄会在两肩留下原色条；
- `edge_grow=CLOTH_EDGE_GROW(4)` —— 轮廓外按彩度/中性色迭代吞掉「阴影 + 白侧条」
  过渡带（最多 3 轮），否则衣服边缘会留一圈原色描边。扩张后**再跑一遍轮廓平滑**，
  因为逐像素扩张会让边界重新毛糙（成图上是"撕纸边"）。

资产不存在时 `garment.has_asset()` 为假，分割参数回退成贴图档的，保证
「缺资产 → wrap」的回退结果与直接 `style=wrap` 逐像素一致。

### 16.5 网页与接口

`web/wardrobe.html` 取代首页：左侧上传人像（拖拽/点击），右侧 36 件衣橱网格，
上传后自动按色板排序并选中 Top1（前三名带 `TOPn` 角标），底部固定操作条。

- `GET /api/wardrobe` → `{items:[{id,name_cn,hex,palette,size,thumb_url}]}`
- `GET /api/thumb/<id>` → 缩略图（JPEG）
- `POST /api/wd/match {image, top}` → `{query, matches:[{id,score,joint_delta_e,rank}]}`
- `POST /api/wd/compose {image, garment}` → `{composite:{preview,download,compose_style,garment_id,size}}`

### 16.6 广告横幅条裁剪（2026-11-16 补）

**现象**：魔爪 → 红牛 时，目标人像的胯部被印上一条横贯全身的深蓝带子
「红牛维生素功能饮料」，左右还伸出身体轮廓之外。

**根因**：红牛那张基准人像在衣服下摆位置压了一条广告横幅（设计元素，不是布料）。
语义模型把它判成上衣的一部分，`_fill_holes` 又把它封进蒙版，于是它进了条带，
`warp_rows` 的源端下界也落在横幅下面 —— 换装时自然被一起画到目标身上。

**实现**：`wardrobe.drop_ad_banner()`，在建资产时先裁再算轮廓。判据用**色度**
而不是全色差：横幅是纯色底 + 一排白字，行内明度跳得很厉害（逐行中位色不可靠），
但底色的色相与整件衣服完全不是一回事。

- 取整件衣服的色度中心（`a*`/`b*` 中位数，明度不参与）；
- 逐行统计「色度离中心 > 30」的像素占比，横贯整行的横幅接近 1.0，印花只占行宽一部分；
- 这条带要**上下都被正常衣服夹住**（两端各回落到 <0.2）、高度占衣服的 3%~20%
  —— 球衣下摆那种「一路到底的纯色」不会被误判；
- 命中后从带起点整段截断，条带下缘落在横幅上沿那道真实衣摆上。

全库 36 件实测只命中红牛一件（`meta.json` 里记 `banner_trim.rows=[612,676]`），
其余 35 件的蒙版逐像素未变；重建后 `src_rows` 从 `[128,712]` 变成 `[128,611]`。
`scripts/import_wardrobe.py` 加了 `--only <品牌>`，只重建受影响的那件。

### 16.7 臂-身缝隙保护（2026-11-16 补）

**现象**：给黄仁勋（双臂垂放的黑皮衣照）换装后，手臂两侧那条背景缝被布料填满，
手臂和躯干糊成一整块，人像看着"胖"了一圈。

**根因**：三层叠加。

1. 语义分割在 128×128 网格上做，缝上方只有 1~2px、下方张到十几像素，模型
   直接把整条缝判成 `upper` / `person`，缝在掩版里根本不存在；
2. `_semantic_segment` 为了让「压到画面下缘的号码」也能成为封闭洞，先补两行白
   再 `binary_fill_holes` —— 这条缝通向下缘，补底等于给它封了口，于是被整条填满；
3. `_resmooth` 的逐行 left..right 轮廓重建、`edge_grow` 的外扩、以及 garment 分支
   重算 `cover_mask` 时的 `binary_dilation`，每一步都会把缝再抹一次。

**实现**：

- `cloth._bg_gap_mask()`：用图像自身的证据捞回缝 —— 颜色接近背景色 **且** 与画面
  边界连通（缝向上下开口，通到外部背景）。衣服上的蓝色印花虽然也接近背景色，
  但连通块碰不到边界，不会被误吃。取 `CLOTH_BG_DISTANCE` 做 seed，放宽到 2.2 倍
  把投影压暗的像素并进来；块内 seed 占比 < 2% 的整块丢弃。最后用 `(9,1)` 竖向
  结构元做闭/开运算，补小豁口、去孤点，横向宽度不变。
- `cloth._fill_holes_keep_gaps()`：把填洞拆成「真封闭洞」与「补底才封的区域」，
  后者里的背景/肤色像素一律不填。返回的 gap 掩版交给后续阶段当保护。
- `_resmooth()` 与最终兜底各扣一次 `gap`（`_smooth_edges` 会把它填平）；
- `compose._solid_cover_mask(mask, hard, protect=gap)`：填洞时整体扣掉 gap
  （缝在上半段会被两条手臂封成封闭洞，光拦补底那一档不够），并对 gap 做 1px
  亚像素羽化，避免边缘出现像素阶梯；
- garment 分支重算 `cover_mask` 时，外扩前后各扣一次 gap —— 缝只有 4~12px 宽，
  `binary_dilation` 从两侧各吃 2px 就能填平。

占比保护：`_bg_gap_mask` 的结果超过 hard 的 15% 时视为判据跑偏（衣服本身接近
背景色），宁可不扣、保持原样。

### 16.8 字形保真：整件衣服做等比变换（2026-11-16 补）

**现象**：换装后衣服上的字是歪的 —— RedBull 变成瘦高、竖笔画带波浪，中文横排字
被压扁，字距忽宽忽窄。用户的原话：「字还是有点扭曲，最好字是正的」。

**根因**：`build_texture()` 原来是**逐行各向异性**映射：纵向把源 `y0s..y1s` 拉到
目标 `y0d..y1d`，横向把每一行的源 `half` 拉到目标该行的 `half`。红牛 → 黄仁勋
实测纵向 0.905、横向 0.593，**比例差 34%**，字必然被压窄；更糟的是目标行宽逐行
变化（肩宽 → 腰窄），同一根竖笔画在不同行被横向缩放不同，于是抖成波浪。

**实现**（`garment.build_texture`，一次相似变换）：

- 比例 `s = min(s_x, s_y)`，其中 `s_x = 目标中位半宽 / 源中位半宽`、
  `s_y = 目标衣服高 / 源衣服高`。取小值 = **只缩不放**，图案永远完整落在目标
  衣服里，宁可上下留一段纯色也不裁掉文字。
- 纵向：`sy = cy_s + (y - cy_d) / s`。源在目标衣服里的等比高度 `span_s * s`
  不够填满时，上下各取 6% 以源边距压进去补满（`v_fill` 权重从 1 渐变到 0），
  而不是重复同一行 —— 重复行会在下摆接出一条硬边界。
- 横向：`sx = cx_s + (x - cx_d) / s`，**中心与半宽都用全局中位数**。早期版本
  用逐行中心，竖笔画会在行与行之间左右错位，一个字被切成一截一截，看起来
  正是「横向拉宽 / 拖尾」。
- 目标比等比区宽时（目标比源胖），两侧那一段退化成「该行布料主色」的平滑渐变
  （`row_main`，先沿纵向做 σ≈1.2% 高度的高斯平滑，否则逐行主色会印出一条条
  水平亮线）。既不做压缩采样（会出摩尔纹），也不镜像（会出现半个字）。

代价：目标衣服的「版型」不再逐行贴合，肩线/袖型由覆盖掩版（目标 `hard`）决定；
图案大小由衣服宽度决定，与基准图里衣服上的占宽比一致。

**2026-11-17 起默认切回旧观感**：等比变换确实把字做正了，但图案不再跟着身形走，
实看反而不如之前「贴合」的版本自然。`config.GARMENT_TEXTURE_ALIGN` 现默认
`rowwise` —— 逐行各向异性映射（纵向拉满目标高度，横向把每行源半宽拉到目标该行
半宽，中心逐行取），图案完全贴身形，代价是字被横向压窄/拉宽、竖笔画轻微抖动。
改回 `similarity` 就是本节这版。两条路径只换坐标场，覆盖掩版、光照、alpha
剪影完全共用；rowwise 下每一行都被填满，不需要「两侧/上下退化成主色」的补丁。
两种模式的同图对比见 `report/_align_compare.png`（左 similarity / 右 rowwise）。

### 16.8b 手臂溢出与浮岛修复（2026-11-17 补）

**现象**：科比（深棕肤色、黄黑无袖球衣）换上雪碧后，一侧手臂被绿色图案盖住；
和其正（红背心贴红墙）换雪碧后，手臂上浮着两块与主体分离的绿色孤岛。

**根因（两处）**：
1. `cloth.skin_mask` 是 RGB+YCbCr 判据，对深肤色/阴影里的手臂漏检，`hard &= ~skin`
   没把手臂排除；语义模型其实给出了 `Left-arm/Right-arm`（ATR 14/15）类别，
   之前没用。全库验证 `arm ∩ upper` 恒为 0，两者互斥，是可靠信号。
2. 换装档的轮廓外同色扩张 `CLOTH_EDGE_GROW` 会把远处「与衣服同色」的小块
   （手臂旁、背景墙上的红影/黄斑）连进来，形成与主体不相连的碎块。原本的
   `_keep_components` 在扩张**之前**跑，管不到这些后生的孤岛。

**修复**：
- `cloth._semantic_segment`：`exclude_skin` 时把模型 arm 类别并入 skin 一起排除
  （`config.CLOTH_ARM_EXCLUDE`），与肤色判据互补。
- 轮廓外扩张结束后再跑一次 `_keep_components`，剔除远离主体（>8px）且不足
  主体 10% 面积的碎块；袖子/肩带与主块相连，不受影响。

**验证**：三套回归 19/19、16/16、23/23 全绿；两例修复前后见
`report/_fix2_kobe.png`、`report/_fix2_heqi.png`。

**2026-11-17 追加 · 覆盖不完全（暖色衣服）**：红牛穿金色 T 恤换其他饮料时，
大片金色袖子/肩膀露原色 —— `skin_mask` 的 RGB 判据把金色/暖色衣服当成皮肤
扣掉（红牛 upper 区域 63% 被误判，阿萨奶茶 56%、蜜桃乌龙茶 45%；全库中位仅
3.4%）。修复：`config.CLOTH_SKIN_SPARE_UPPER` 让语义模型判为 upper 的像素
从肤色排除中豁免（脸/手臂是独立类别，不在 upper，安全）。红牛分割覆盖率
0.175→0.379（≈upper 0.377），全库随机换装自测（36 件，`report/_at_*.png`）
问题案例从 12 降到 1（唯一残留是红牛资产里手持金罐的极小残影，属素材固有限制）。
`_regress_lettering` 的补洞断言同步改为构造蒙版直接验证机制（真实号码洞
因蒙版变完整而从 2219 降到个位数，旧绝对数量断言失效）。

**2026-11-17 第三轮 · 景田与肩带**：全库换成景田时只出现紫色波纹，根因是蓝衣
配蓝幕：素材端 `border_background` 把整件上衣当背景删掉，模型又把手里那瓶水
判成 Upper-clothes，条带里只剩瓶身与胸前紫字。修复 `wardrobe.material_mask`
（高置信度 upper 从背景掩版豁免、按躯干连通块剔除手持物）并重建景田资产，
现在景田/Game 原字样完整；`compose._ensure_resolution` 同步按长边 1200 收口，
修好极窄长图（外星人电解质水）放大后蒙版与画布尺寸不一致的报错。科比肩头
残留红边是 arm 类别把球衣滚边一起排除了，新增 `CLOTH_ARM_TRIM_*`：只把紧贴
上衣、颜色/概率仍属于衣服的 arm 像素豁免回来，深肤色手臂继续排除。全库
36 张基准 → 景田 + 36 张随机换装宫格目检无异常，三套源码回归与六套 exe
冒烟全绿。

### 16.9 账号水印清除（2026-11-16 补）

**现象**：雪碧换装后，目标人像下摆印着一行白色小字「抖音号：62239612181」。

**根因**：基准人像是从短视频里截的图，画面底部压着平台账号水印。它不是衣服
图案，但语义模型只认「这块像素是不是上衣」，水印在衣服上就一并进了蒙版，
`row_fill` 又把它当布料纹理原样搬进条带。

**难点**：雪碧 logo 的白描边同样是纯白、同样落在衣服下部，一刀切地「抹掉下半
部分的白」会把 logo 一起吃掉。

**实现**：`wardrobe.drop_watermark()`，在 `build_item()` 里 `row_fill` 之后调用。

- **用笔画粗细定位，不用亮度**：对白色掩膜做距离变换，取「到白区边界 ≤ 3.5px」
  的那一圈 —— 水印是 6~8px 宽的细字，整片都是细笔画；logo 白框有 30px 以上的
  实心区，它的内部不算细笔画。白框的**外沿**虽然也细，但它紧贴自己那块厚白，
  用「到厚白区的距离 > 7px」把它摘掉，剩下的就是孤立在绿布上的水印。
- **行带**：细笔画在最厚那一行（数字串中部）向两侧扩，落到「薄到三成以下」
  为止，得到一条 `宽 ≈ 0.75 衣宽、高 ≈ 字高` 的行带。
- **三道门防误伤**：行带下沿必须在衣服下方 10% 以内、与衣服最底边留至少 6px
  （贴边的是下摆描边）、框内白像素占比 4%~28%（水印稀疏，logo 大字能占到三到
  六成）。全库 36 件实测只命中雪碧一件。
- **修复**：行带整块重铺（只补白像素会留一圈「暗字轮廓」）。逐列用上下两侧
  像素纵向插值，端点**只接受绿布**（`G > R+40 且 G > B+30`）—— 水印左段压在
  logo 白尖上，端点取到白会把白往下拖成一条雾带；端点不是绿的列改用邻居列借色。
  之后做一道横向为主的低通（σ=(2,12)）压掉竖条纹，并按离洞边的距离羽化，
  贴边处与外面严丝合缝。

**缩略图同步**：网页衣橱卡片显示的是 `thumb.jpg` —— 整张基准人像缩到 240px，
水印同样在上面。240px 下水印笔画只有一两像素宽，而它又和 logo 的白框糊成一片，
阈值检测无从下手（硬抹会在 logo 上留缺口）。所以缩略图走另一条路：短视频水印
固定压在画面最下缘，直接把最底部 **8.5%** 裁掉，再用衣服下摆的最后十几行拉伸
补回原尺寸（`_shave_bottom()`）—— 人像比例不变，下摆只多出一两个百分点的绿色。

清除只改 `strip.webp` 的颜色、`thumb.jpg` 的底边，不动 `mask.png` / `profile.npz`
（轮廓没变）。`scripts/clean_strip_watermark.py` 可以对**已导出的资产**重跑这一步
—— 原始素材导完通常就删了，没有源图也能修；脚本幂等，清过的条带不会再次命中。
`--thumb` 用于只补缩略图（条带已清、缩略图还是旧的那种情况）。

### 16.10 交付物人脸打码（2026-11-16 补）

**范围**：交付物里出现的脸全部打码，**唯一不打码的是用户上传的照片**。

- 打：`data/wardrobe/*/thumb.jpg`（衣橱卡片、`/api/thumb` 直接展示整张人像）、
  `report/_audit/*.png`（55 张资产审查图）、`report/_newlib_masks/*.png`
  （36 张三联证据图）、`report/` 里的演示合成图与拼版图。
- 不打：用户上传的人像。`compose.py` / `web.py` 从头到尾没有 import
  `privacy`，换装链路拿到的就是原图 —— 打了码等于把功能废掉。

**为什么换检测器**：最初用 `human.py` 的 SegFormer（ATR 第 11 类 `Face`），对
240px 的单人缩略图 36/36 命中，但一上拼版图就露馅 —— 三张并排的人像它只认出
中间那张，而且框把脖子和胸口一起圈进去（打码会糊掉球衣图案）。改用 **YuNet**
（`models/face_detect_yunet.onnx`，230KB，libfacedetection 的轻量人脸检测，
ONNX 推理仍走已有的 onnxruntime）：同一张三联图 3/3 命中，框紧贴五官，
球衣一点没碰。SegFormer 路径留作降级 —— YuNet 文件缺失时顶上。

**框与格子**：

- 只打脸和下巴/耳朵，不打整颗头（美年达的粉色假发、盐汽水的绿发占掉半幅）。
  框按脸外扩 **18%**，下半边再多给 50% 的余量（脖子常被算进脸）。
- 马赛克格子 = `min(脸宽,脸高)/6`（最小 5px），实测 10~18px/格：格子数固定
  的话，脸越大格子越多、五官越清楚。
- 蒙版边界羽化 0.6px，避免在纯色背景上看起来像「贴了块补丁」。
- 单脸图取面积最大的框；多脸图（拼版、演示合成）全打。

**三处接入**（都不会跑到用户图上）：

1. `wardrobe.build_item()` 存 `thumb.jpg` 之前打码，`meta.json` 记
   `privacy: {masked, engine, box, block}` —— 新导入的资产默认就是打码的。
2. `scripts/mask_thumb_faces.py`：改**已有**的 36 件资产（源图导完就删了，
   不能指望重导），`--only` / `--dry-run` / `--force`。
3. `scripts/mask_faces.py`：任意目录/图片，默认 `report examples data/wardrobe`。
   处理过的文件按 mtime+size 记进 `.facemask_state.json`，重跑直接跳过 ——
   人脸检测器对马赛克过的脸有时还能再框一次，重复打会叠出两层格子。

`scripts/build_package.py` 打成果包时默认对所有图再跑一遍（`--no-mask` 关闭），
所以「重新生成了一张带脸的图」不会因为忘了手动处理而漏进交付包。
`report/_probe_masks.py` 也改成先打码再切图。

`strip.webp` / `mask.png` / `profile.npz` 一概不动：条带里只有衣服。

### 16.11 验收（2026-11-16）

- `report/_smoke_garment.py` **19/19**：36 件资产齐全、条带两侧无背景染色、
  36 件全部可出图且不回退、躯干外（脸/背景/讲台）**零改动**、覆盖区不是纯色块、
  覆盖区平均色与资产布料平均色一致、皮肤未被染色、平均 **1.9s/件**、
  资产缺失回退 wrap、匹配分数单调。
- `report/_regress_lettering.py` **16/16**、`report/_regress_r3.py` **23/23** 全绿
  （贴图/平涂链路零回归；「邻域外改动」参照物改成填洞后的剪影，因为字样本身
  就是 hard 里的洞，补洞改的是衣服内部像素）。
- 真实输入六组（`report/_wd_real_sheet.png`）：雪碧→王老吉、康师傅冰红茶→可口可乐、
  魔爪→红牛、红牛→魔爪、伊利→七喜、王老吉→康师傅冰红茶，图案完整、边界贴合。
- 网页接口实测：`/api/wd/match` 2.7s（康师傅冰红茶正确排第一，89.7 分）、
  `/api/wd/compose` 1.3s（480×480 出图）。
- 人脸打码：`scripts/mask_thumb_faces.py` **36/36** 命中（`--force` 重跑一遍
  仍全中，删掉 meta 标记后靠检测也能重建）；`scripts/mask_faces.py` 一轮扫
  **605 张图、打码 502 张**，`report/_audit`（55 张）、`report/_newlib_masks`
  （36 张）、36 张缩略图的复检里 **0 张还能检出完整人脸**；演示图因为马赛克后
  检测器仍会「看到一张脸的轮廓」而保留响应，逐张目检（41 张成果图拼版）确认
  五官不可辨认。`exe` 侧 `/api/thumb/sprite` 与库里打码图字节一致。
- 打包自检：`beverage-color-db_samples.zip` 226 项里 **0 张未打码原图**
  （早期备份目录 `report/_thumb_orig` 已被打包规则排除）。

**已知限制**

1. **多层着装**（西装 + 衬衫 + V 领背心）：目标衣服 mask 是一整块，换装后领口
   层次会被抹平、外套变成"大衬衫"。`_strip_outer_layer()` 能剥外层，但对单色
   球衣会误判（把整件当外套剥掉），所以默认关闭 `CLOTH_LAYER_SPLIT=False`。
2. **比例**：映射是「源衣服逐行拉伸到目标衣服的 bbox」，目标衣服比源宽很多时
   （西装、被讲台切断的下摆）图案会被横向拉扁，这是"拉伸变形"本身的代价。
3. **素材分辨率**：原库图多为 176~270px 的缩略图，4x 超分后仍偏软；换成
   1000px 以上的正面人像素材，纹理清晰度会明显好一档。
4. **臂-身缝的恢复依赖背景色判据**：纯色/双色背景效果最好。背景是复杂场景
   （街景、室内）或与衣服同色时，缝只能恢复一部分，极端情况下会被判据放过
   （宁可留着粘连，也不误挖衣服）。极窄的上段（1~2px）仍会跟随模型判定，
   与腋下紧贴的姿势一致，观感可接受。
5. **账号水印清除只认「贴底、稀疏、横贯大半衣宽」的横排白字**：字号大、压在
   衣身中部的品牌标语不会被当成水印（宁可不改，也不误伤 logo）。水印下方那块
   布是平涂渐变时补得最干净；压在密集水珠/印花上时，修复区会显得比周围平滑
   一点，缩小到换装尺寸后看不出来。
6. **人脸打码依赖语义模型的 `Face` 类**：正面/半侧脸 36/36 命中；侧脸、低头、
   被手或麦克风挡掉大半张脸时，框可能只盖住露出的部分。全黑、逆光的脸也可能
   整块漏检 —— 打码是「宁可多盖一格」的策略，但库里的资产要肉眼过一遍
   `report/wardrobe_face_mask.png` 再发包。

---

MIT（代码）；图片与数据按各自来源授权，见 `ATTRIBUTION.md`。
