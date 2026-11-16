#!/usr/bin/env python3
"""一键引导脚本：准备虚拟环境 -> 安装依赖 -> 启动网页服务。

用法（在解压后的项目根目录执行，或直接双击 start.bat）：

    python start.py                 # 仅本机访问，127.0.0.1:8765
    python start.py --lan           # 监听 0.0.0.0，供同一 Wi-Fi 的手机访问
    python start.py --proxy         # 手机访问（转发模式，绕开 Windows 防火墙限制）
    python start.py --port 9000     # 换端口
    python start.py --check         # 只做环境自检，不启动服务
    python start.py --cli match --color "#E60012"   # 直接透传给原 CLI

设计原则：不改动 beverage_color_db 包内任何代码；本文件只负责环境准备与进程编排。
第一次运行需要联网安装 requirements.txt（约 6 个依赖）；之后 .venv/.deps-ok
存在时直接启动，完全离线可用。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MARKER = VENV / ".deps-ok"
REQ = ROOT / "requirements.txt"


def _run(cmd: list[str], **kw) -> int:
    print("+ " + " ".join(str(x) for x in cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT), **kw)


def bootstrap() -> str:
    if not PY.exists():
        print("[1/2] 创建虚拟环境 .venv ...", flush=True)
        rc = _run([sys.executable, "-m", "venv", str(VENV)])
        if rc or not PY.exists():
            raise SystemExit("创建虚拟环境失败。请确认已安装 Python 3.10+ 且包含 venv 模块。")
    if not MARKER.exists():
        print("[2/2] 首次运行：安装依赖（需要联网，约 1-3 分钟）...", flush=True)
        rc = _run([str(PY), "-m", "pip", "install", "-r", str(REQ)])
        if rc:
            raise SystemExit("依赖安装失败。可手动执行： " + str(PY) + " -m pip install -r requirements.txt")
        MARKER.write_text("ok", encoding="utf-8")
    else:
        print("环境已就绪（.venv/.deps-ok），跳过安装。", flush=True)
    return str(PY)


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，只让系统选出默认出口网卡
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def make_qr_page(py: str, port: int) -> None:
    """生成带二维码的手机访问页；qrcode 未安装时脚本会自行给出提示。"""
    try:
        _run([py, str(ROOT / "scripts/make_phone_qr.py"), "--port", str(port)])
    except OSError as exc:
        print(f"（二维码页生成跳过：{exc}）", flush=True)


def check(py: str) -> None:
    import sqlite3

    db = ROOT / "data/catalog.db"
    con = sqlite3.connect(str(db))
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("brands", "images", "brand_color_profile", "color_features")}
    con.close()
    img_dirs = len([p for p in (ROOT / "data/images").iterdir() if p.is_dir()])
    print(f"Python     : {sys.version.split()[0]}")
    print(f"解释器     : {py}")
    print(f"数据库     : {db}")
    print(f"品牌/图片/档案/色特征 : {counts}")
    print(f"图片目录数 : {img_dirs}")
    ok = (counts["brands"] == 55 and counts["images"] == 55
          and counts["brand_color_profile"] == 55 and img_dirs == 55)
    print("自检结果   : " + ("通过（55 品牌完整）" if ok else "注意：数量与预期 55 不一致"))
    _run([py, "-m", "beverage_color_db.cli", "match", "--color", "#E60012", "--top", "3", "--no-report"])


def main() -> int:
    ap = argparse.ArgumentParser(description="饮料换装 · 一键启动")
    ap.add_argument("--lan", action="store_true", help="监听 0.0.0.0，供手机访问")
    ap.add_argument("--proxy", action="store_true", help="手机访问：0.0.0.0:PORT 转发到 127.0.0.1:PORT+1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true", help="只自检环境")
    ap.add_argument("--cli", nargs=argparse.REMAINDER, help="透传给 beverage_color_db.cli")
    args = ap.parse_args()

    py = bootstrap()
    if args.check:
        check(py)
        return 0
    if args.cli is not None:
        return _run([py, "-m", "beverage_color_db.cli", *args.cli])

    port = args.port
    url_local = f"http://127.0.0.1:{port}/"
    if args.proxy:
        inner = port + 1
        print(f"转发模式：应用 {url_local.replace(str(port), str(inner))} <- 0.0.0.0:{port}", flush=True)
        server = subprocess.Popen(
            [py, "-m", "beverage_color_db.cli", "serve", "--host", "127.0.0.1", "--port", str(inner)],
            cwd=str(ROOT),
        )
        make_qr_page(py, port)
        print(f"手机（同一 Wi-Fi）打开： http://{lan_ip()}:{port}/", flush=True)
        print("按 Ctrl+C 结束。", flush=True)
        try:
            # 转发器只用标准库，用系统 Python 运行（Windows 防火墙常按解释器路径放行）
            return _run([sys.executable, str(ROOT / "scripts/lan_proxy.py"),
                         "--listen-port", str(port), "--target-port", str(inner)])
        except KeyboardInterrupt:
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        make_qr_page(py, port)
    print(f"启动网页界面： {url_local}" + (f"   手机：http://{lan_ip()}:{port}/" if args.lan else ""), flush=True)
    if not args.no-browser:
        threading.Timer(1.5, lambda: webbrowser.open(url_local)).start()
    return _run([py, "-m", "beverage_color_db.cli", "serve", "--host", host, "--port", str(port)])


if __name__ == "__main__":
    raise SystemExit(main())
