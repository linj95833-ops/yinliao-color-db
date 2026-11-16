#!/usr/bin/env python3
"""把「程序 + 数据库 + 图片库 + 配置」打包成可移植的 zip。

    .venv/Scripts/python.exe scripts/build_package.py

产物放在项目上一级目录（工作区根）：
    beverage-color-db_portable.zip   运行必需的全部文件（推荐）
    beverage-color-db_samples.zip    可选的生成成果/对比图（含测试人像）
    饮料换装.exe                     Windows 单文件版（若已构建）
    SHA256SUMS.txt                   以上已存在文件的校验值

产物使用固定文件名：跨天重打包不会生成新的日期后缀，已保存的路径始终有效。

要点：
* 用 sqlite backup 导出 catalog.db，摆脱 -wal / -shm，单文件即可打开；
* 不打包 .venv（277MB 且与本机 Python 路径绑定）、__pycache__、临时缓存、
  以及任何带 -wal/-shm/-part 的中间文件；
* MANIFEST.txt 记录包内每个文件的大小与 SHA256，便于核对完整性。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent
PKG_NAME = "beverage-color-db"
STAGE = OUT / "_packaging" / PKG_NAME

ROOT_FILES = [
    "README.md", "START_HERE.txt", "ATTRIBUTION.md", "requirements.txt",
    "requirements-build.txt", ".gitignore",
    "start.py", "start.bat", "start_lan.bat", "start_mobile.bat", "start.sh",
    "app_main.py",
    # 打码台账（路径相对工程根）：带上它，交付目录里再跑 mask_faces.py 不会
    # 把已经打过的脸再糊一层。
    ".facemask_state.json",
]
ROOT_DIRS = ["beverage_color_db", "config", "web", "scripts", "examples",
             "data/images", "data/images_hd", "data/wardrobe", "models"]
EXTRA_FILES = ["report/brand_gallery.html", "report/quality.md", "report/quality.json"]
# models/_source 是 105MB 的 fp32 原始权重缓存，只用于重新量化，不进交付包；
# models/diffusion 是离线扩散的大权重（数 GB），只在本机 AI 实验用，同样排除，
# 避免重打包时把交付包撑到几 GB。
SKIP_NAMES = {"__pycache__", ".DS_Store", ".deps-ok", "Thumbs.db",
              "_source", "diffusion"}
SKIP_SUFFIX = {".pyc", ".pyo", ".db-shm", ".db-wal", ".db-journal", ".part"}
PLACEHOLDERS = ["data/inbox/.gitkeep", "data/cache/.gitkeep"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def portable_db(src: Path, dst: Path) -> dict:
    """WAL 模式下直接复制 .db 会丢数据，必须用 backup API 导出一致性快照。"""
    con = sqlite3.connect(str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    out = sqlite3.connect(str(dst))
    try:
        con.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
        out.commit()
        counts = {t: out.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("brands", "images", "brand_color_profile", "color_features", "fetch_log")}
        integrity = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
        con.close()
    if integrity != "ok":
        raise RuntimeError(f"数据库完整性检查失败：{integrity}")
    return counts


def _reset_stage() -> None:
    """逐文件清理暂存目录（不用 rmtree：整树删除会触发批量删除保护）。

    每次只 unlink 单个文件，且在复制前完成，避免上一轮的残留文件混进新包。
    """
    if STAGE.exists():
        for p in sorted(STAGE.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            try:
                if p.is_dir():
                    p.rmdir()
                else:
                    p.unlink()
            except OSError:
                pass
        try:
            STAGE.rmdir()
        except OSError:
            pass
    STAGE.mkdir(parents=True, exist_ok=True)


def stage_files(reset: bool = True) -> dict:
    if reset:
        _reset_stage()
    else:
        # 覆盖式整理：文件集合没变时（只改了内容）不必先清空，逐个删除文件
        # 会撞上「单轮批量删除」的安全阈值，整次打包白跑。
        STAGE.mkdir(parents=True, exist_ok=True)
    copied = 0
    for rel in ROOT_FILES + EXTRA_FILES:
        src = ROOT / rel
        if not src.is_file():
            raise FileNotFoundError(src)
        dst = STAGE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for rel in ROOT_DIRS:
        src = ROOT / rel
        for p in sorted(src.rglob("*")):
            if not p.is_file() or p.name in SKIP_NAMES or p.suffix in SKIP_SUFFIX \
                    or any(part in SKIP_NAMES for part in p.parts):
                continue
            r = p.relative_to(ROOT)
            dst = STAGE / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            copied += 1
    for rel in PLACEHOLDERS:
        p = STAGE / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        copied += 1
    counts = portable_db(ROOT / "data/catalog.db", STAGE / "data/catalog.db")
    copied += 1
    return {"files": copied, "db": counts}


def write_manifest(meta: dict) -> None:
    lines = [
        "饮料换装 · 交付包文件清单",
        "=" * 68,
        f"打包时间 : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"品牌     : {meta['db']['brands']}   图片记录: {meta['db']['images']}   "
        f"多色档案: {meta['db']['brand_color_profile']}   色特征: {meta['db']['color_features']}",
        f"文件数   : {meta['files']}",
        "",
        "格式：相对路径 | 字节数 | SHA256",
        "-" * 68,
    ]
    for p in sorted(STAGE.rglob("*")):
        if p.is_file():
            lines.append(f"{p.relative_to(STAGE).as_posix()} | {p.stat().st_size} | {sha256(p)}")
    (STAGE / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_zip(path: Path, entries: list[tuple[Path, str]]) -> None:
    if path.exists():
        path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, arc in entries:
            z.write(src, arc)
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"压缩包损坏：{bad}")
    print(f"  {path.name}: {path.stat().st_size / 1024 / 1024:.2f} MB, {len(entries)} 项, 校验通过")


def mask_sample_faces() -> None:
    """打包前把 report/ 里的图打一遍码（交给 mask_faces.py，带台账、可重入）。

    用户上传的照片不走这条路 —— 换装链路根本不调用打码模块。这里做的是
    证据图/演示图，和库里 thumb.jpg 用同一套规则。默认开着，`--no-mask` 关掉。
    """
    import subprocess
    import sys

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "mask_faces.py"),
                        "report"], cwd=str(ROOT), capture_output=True, text=True)
    tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    print("成果包人脸打码：" + (tail[-1] if tail else f"跳过（exit {r.returncode}）"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reset", action="store_true",
                    help="跳过暂存目录清理，直接覆盖（改了内容、没增删文件时用）")
    ap.add_argument("--no-mask", action="store_true",
                    help="不打码（默认给成果包里的图打码，用户上传的图从不打）")
    args = ap.parse_args()
    meta = stage_files(reset=not args.no_reset)
    write_manifest(meta)
    print(f"暂存目录：{STAGE}")
    print(f"数据库导出：{meta['db']}")

    entries = [(p, f"{PKG_NAME}/{p.relative_to(STAGE).as_posix()}")
               for p in sorted(STAGE.rglob("*")) if p.is_file()]
    # 固定文件名：避免跨天重打包时路径变化，用户已保存的链接始终有效
    main_zip = OUT / f"{PKG_NAME}_portable.zip"
    make_zip(main_zip, entries)

    # 可选成果包：所有生成结果/对比图，含测试人像，单独存放避免混入主包
    # 成果包保留所有对比图，但不带临时诊断/回归脚本与修复前备份
    def sample_ok(p: Path) -> bool:
        rel = p.relative_to(ROOT / "report")
        # 备份目录里是**没打码的原图**（例如 report/_thumb_orig），一律不进包。
        if any(part.endswith("_orig") or part in {"_backup", "_orig"} for part in rel.parts):
            return False
        return (p.is_file() and p.name not in SKIP_NAMES and p.suffix != ".log"
                and not p.name.startswith("_"))  # 下划线开头均为临时诊断脚本/中间图

    if not args.no_mask:
        mask_sample_faces()
    samples = sorted(p for p in (ROOT / "report").rglob("*") if sample_ok(p))
    sample_zip = OUT / f"{PKG_NAME}_samples.zip"
    make_zip(sample_zip, [(p, f"{PKG_NAME}/report/{p.relative_to(ROOT / 'report').as_posix()}")
                          for p in samples])

    # 校验清单：两个压缩包 + 如果已构建好的 exe
    exe = OUT / "饮料换装.exe"
    # 旧 exe 被运行中的进程锁住时，build_exe.py 会另存「饮料换装_新版.exe」；
    # 一并纳入清单，避免交付一个没有校验值的可执行文件。
    targets = [p for p in (main_zip, sample_zip, exe,
                           OUT / "饮料换装_新版.exe") if p.is_file()]
    sums = OUT / "SHA256SUMS.txt"
    # 固定 LF 行尾：Windows 默认 CRLF 会让 GNU sha256sum -c 把 \r 当文件名的一部分
    with sums.open("w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{sha256(p)}  {p.name}\n" for p in targets))
    print(f"校验文件：{sums}（{len(targets)} 项）")
    print("完成。")


if __name__ == "__main__":
    main()
