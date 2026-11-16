#!/usr/bin/env python3
"""把饮料换装打包成 Windows 单文件 exe。

    .venv/Scripts/python.exe scripts/build_exe.py

流程：整理只读资源 -> 导出独立数据库 -> 生成图标 -> PyInstaller onefile 打包
     -> 把成品复制到工作区根目录「饮料换装.exe」。
需要 .venv 里已装 pyinstaller（pip install pyinstaller==6.22.3）。
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WS = ROOT.parent
BUILD = ROOT / "build"
ASSETS = BUILD / "exe_assets"
DIST = ROOT / "dist"
PY = ROOT / ".venv/Scripts/python.exe"


def portable_db(dst: Path) -> None:
    con = sqlite3.connect(str(ROOT / "data/catalog.db"))
    out = sqlite3.connect(str(dst))
    try:
        con.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
        out.commit()
    finally:
        out.close()
        con.close()


def make_icon(path: Path) -> None:
    """纯 Pillow 画一个饮料罐图标，避免引入图标素材依赖。"""
    size = 256
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((18, 18, 238, 238), fill=(238, 243, 250, 255))
    # 罐身
    d.rounded_rectangle((78, 44, 178, 218), radius=22, fill=(214, 32, 40, 255))
    # 顶部/底部金属边
    d.rounded_rectangle((78, 38, 178, 62), radius=12, fill=(197, 205, 216, 255))
    d.rounded_rectangle((78, 202, 178, 220), radius=10, fill=(176, 186, 200, 255))
    # 白色标签带 + 蓝色波浪
    d.rectangle((78, 104, 178, 158), fill=(248, 250, 253, 255))
    d.polygon([(78, 146), (100, 132), (122, 144), (146, 126), (178, 142),
               (178, 158), (78, 158)], fill=(24, 84, 178, 255))
    d.ellipse((112, 68, 144, 100), fill=(255, 255, 255, 235))
    d.rounded_rectangle((118, 74, 124, 94), radius=3, fill=(214, 32, 40, 255))
    d.rounded_rectangle((132, 74, 138, 94), radius=3, fill=(214, 32, 40, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def stage() -> None:
    # 覆盖式整理而非整树删除：资源集合是固定的，重复打包不需要 rmtree，
    # 也避免误删用户放进暂存目录的文件（以及批量删除的安全限制）。
    ASSETS.mkdir(parents=True, exist_ok=True)
    ign = shutil.ignore_patterns("__pycache__", "*.pyc")

    shutil.copytree(ROOT / "config", ASSETS / "config", ignore=ign, dirs_exist_ok=True)
    shutil.copytree(ROOT / "data/images", ASSETS / "data/images", dirs_exist_ok=True)
    hd = ROOT / "data/images_hd"
    if hd.is_dir():  # R2 离线超分素材（可选，删掉即可回退原图）
        shutil.copytree(hd, ASSETS / "data/images_hd", dirs_exist_ok=True)
    wd = ROOT / "data/wardrobe"
    if wd.is_dir():  # 衣橱资产（36 件基准人像切出的衣服，约 2.8MB）
        shutil.copytree(wd, ASSETS / "data/wardrobe", ignore=ign, dirs_exist_ok=True)
    # 旧「饮料包装库」的 8 件基准衣服已被衣橱取代：源目录不存在时，暂存目录里
    # 上一轮留下的 data/garments 必须清掉，否则 exe 里会同时存在两套资产
    # （garment.status() 报 44 件，网页列表也会混进已经退役的 id）。
    stale_garments = ASSETS / "data/garments"
    if stale_garments.is_dir() and not (ROOT / "data/garments").is_dir():
        for p in sorted(stale_garments.rglob("*"), key=lambda x: len(x.parts),
                        reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        stale_garments.rmdir()
    (ASSETS / "data/inbox").mkdir(parents=True, exist_ok=True)
    (ASSETS / "data/cache").mkdir(parents=True, exist_ok=True)
    (ASSETS / "data/inbox/.gitkeep").write_text("", encoding="utf-8")
    (ASSETS / "data/cache/.gitkeep").write_text("", encoding="utf-8")
    portable_db(ASSETS / "data/catalog.db")
    shutil.copytree(ROOT / "web", ASSETS / "web", ignore=ign, dirs_exist_ok=True)
    shutil.copytree(ROOT / "examples", ASSETS / "examples", dirs_exist_ok=True)
    models = (ROOT / "models" / "segformer_clothes.onnx",
              ROOT / "models" / "depth_anything_v2_small.onnx",
              # YuNet 只在离线建库/给证据图打码时用，带上它 exe 侧才能对
              # 新导入的资产自动打码（230KB，不值得为它分叉两条代码路径）。
              ROOT / "models" / "face_detect_yunet.onnx")
    for model in models:
        if not model.is_file():
            raise SystemExit(f"缺少模型 {model.name}，请先运行："
                             ".venv/Scripts/python.exe scripts/fetch_render_models.py")
    (ASSETS / "models").mkdir(exist_ok=True)
    for model in models:  # 超分模型只用于离线预处理，不随 exe 分发
        shutil.copy2(model, ASSETS / "models" / model.name)
    (ASSETS / "docs").mkdir(exist_ok=True)
    for f in ("README.md", "ATTRIBUTION.md"):
        shutil.copy2(ROOT / f, ASSETS / "docs" / f)
    print(f"资源已整理：{ASSETS}")


def build() -> Path:
    icon = BUILD / "app.ico"
    make_icon(icon)
    cmd = [
        str(PY), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--console", "--name", "DrinkWrap",
        "--icon", str(icon),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        "--add-data", f"{ASSETS / 'config'};config",
        "--add-data", f"{ASSETS / 'data'};data",
        "--add-data", f"{ASSETS / 'web'};web",
        "--add-data", f"{ASSETS / 'examples'};examples",
        "--add-data", f"{ASSETS / 'models'};models",
        "--add-data", f"{ASSETS / 'docs'};docs",
        # onnxruntime 的 .pyd / DLL 是动态加载的，显式收集，否则 exe 里
        # import onnxruntime 会失败并静默降级成色调引擎。
        "--collect-binaries", "onnxruntime",
        "--hidden-import", "onnxruntime.capi.onnxruntime_pybind11_state",
        "--hidden-import", "sklearn.utils._typedefs",
        "--hidden-import", "sklearn.utils._heap",
        "--hidden-import", "sklearn.utils._sorting",
        "--hidden-import", "sklearn.neighbors._partition_nodes",
        "--hidden-import", "qrcode.image.pil",
        "--exclude-module", "aiohttp",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
        "--exclude-module", "PySide6",
        "--exclude-module", "PyQt5",
        str(ROOT / "app_main.py"),
    ]
    extra_hidden: list[str] = []
    try:
        import yaml._yaml  # noqa: F401  # PyYAML 的 C 扩展，存在时才显式收集
        extra_hidden = ["--hidden-import", "yaml._yaml"]
    except ImportError:
        pass
    cmd[cmd.index("--exclude-module"):cmd.index("--exclude-module")] = extra_hidden
    print("开始打包（约 2-6 分钟）...", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT), text=True)
    if r.returncode:
        raise SystemExit(f"PyInstaller 失败，退出码 {r.returncode}")
    exe = DIST / "DrinkWrap.exe"
    if not exe.is_file():
        raise SystemExit("未找到打包产物 " + str(exe))
    return exe


def main() -> None:
    stage()
    exe = build()
    target = WS / "饮料换装.exe"
    # Windows 上旧 exe 可能被杀软/资源管理器短暂占用，copy2 会抛 WinError 32；
    # 重试几次即可，不必让整次构建白跑（dist 里已有完整产物）。
    for attempt in range(6):
        try:
            shutil.copy2(exe, target)
            break
        except PermissionError:
            time.sleep(1.5)
    else:
        # 旧 exe 仍在运行时 Windows 会锁住该文件，覆盖必然失败。此时不判构建失败：
        # dist 里的新 exe 已经完整可用，另存一个可直接双击的备用名即可交付。
        alt = WS / "饮料换装_新版.exe"
        try:
            shutil.copy2(exe, alt)
            print(f"目标 {target.name} 被运行中的旧版本占用；新版已另存为 {alt.name}")
            print("关闭旧的黑色控制台窗口后，用它改名替换即可（两者内容完全相同）。")
            target = alt
        except PermissionError:
            target = exe
            print(f"目标与备用文件均被占用；新版 exe 已就绪：{exe}")
            print("请关闭所有旧的控制台窗口后手动复制，构建本身已成功。")
    print(f"成品：{target}  ({target.stat().st_size / 1024 / 1024:.1f} MB)")
    # 保留 PyInstaller 临时工作目录：下次构建会自行复用/更新缓存，
    # 不做整树删除（既方便复现，也避开批量删除的安全限制）。
    # dist/ 里的 ASCII 原始产物同样保留。


if __name__ == "__main__":
    main()
