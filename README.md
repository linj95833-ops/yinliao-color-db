# 饮料换装工作室

输入单人人像，选择指定饮料，生成保留原姿势和衣服轮廓的换装 PNG。

## 下载运行（Windows 64 位）

从 [v2.1.0 发布页](https://github.com/linj95833-ops/yinliao-color-db/releases/tag/v2.1.0) 下载 **DrinkStudio-v2.1.0.exe**，放到可写文件夹后双击即可运行。内置人体解析模型和 36 件饮料衣橱，无需安装 Python、无需联网，也不要求独立显卡。

首次运行会解压资源，随后自动打开本机网页。程序旁的 `DrinkStudio_Data` 文件夹保存衣橱、模型和 `outputs` 成品。用完关闭启动窗口即可停止服务。

## 使用

1. 上传单人人像，检查绿色换装区域。
2. 搜索并选择指定饮料。
3. 必要时使用「擦除 / 补全 / 撤销」修正衣服区域。
4. 生成，查看原图与成品对比，下载 PNG。

可调整图案大小、上下左右位置和原图光影。每次成品独立保存，同时记录最终蒙版与参数。最长边超过 2400 像素的输入会等比缩小；上传上限 20MB、2400 万像素。

## v2.1.0

- 独立的「上传人像 → 指定饮料 → 生成下载」流程。
- 保护外露手臂，保留整件衣服与袖子的覆盖区域。
- 增加画笔修正、撤销、图案位置调整。
- 修复腹部白色印花误判后留下的缺口与原文字。
- 使用连续纵向贴图，移除腹部重复末行与大块平涂。
- 重做「和其正」衣服素材，完整保留三个字并以文字中心定位。
- 修复相似人像错误复用分割缓存。

自动分割在交叉手臂、复杂遮挡、插画或相近肤色衣服上仍可能不准确，请先检查绿色区域并按需修正。成品细节受原始衣橱素材清晰度影响。

## 源码运行和打包

完整离线源码包 `DrinkStudio-v2.1.0-source.zip` 随 Release 提供，包含模型和衣橱。Git 仓库只提交代码；从 Git 克隆后需从源码包补入 `models/segformer_clothes.onnx` 和 `data/wardrobe`。

```powershell
python -m pip install -r requirements.txt
python studio.py
```

建议使用 Python 3.13。生成 Windows 单文件 EXE：

```powershell
python -m pip install -r requirements-build.txt
python build_release.py
```

产物位于 `release-assets`，包含 EXE、完整源码 ZIP、说明与 SHA256 校验文件。打包明确列出资源，不包含用户上传照片、成品、缓存或测试对照图。

## 代码和验证

- `studio.py`：本机服务、照片会话、结果下载。
- `studio_runtime.py`：单文件资源释放、持久数据目录。
- `studio_engine.py`：分割、手臂保护、腹部补全与衣服图案渲染。
- `web/studio.html`：上传、衣橱、画笔和结果界面。
- `verify_studio.py`：蒙版保护、腹部印花、36 件资产与 HTTP 回归检查。
- `verify_ui.cjs`：Playwright 浏览器验证，可用 `STUDIO_TEST_IMAGE` 指定本地测试人像。

```powershell
python verify_studio.py
```

测试中的「区域外不变」指最终蒙版外像素与处理后的输入逐像素相同，不代表自动蒙版在所有照片上都完全正确。

原有包装配色数据库、CLI 和旧网页代码继续保留供对照。新工作室入口是 `studio.py`，不经过旧版 `web.py` / `compose.py`。

代码采用 MIT 许可证。素材与模型来源见 [ATTRIBUTION.md](ATTRIBUTION.md)。
