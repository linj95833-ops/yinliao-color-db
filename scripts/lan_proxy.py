"""局域网端口转发：让手机在不改 Windows 防火墙的情况下打开本机网页。

背景
----
Windows 防火墙的入站「允许」规则按**可执行文件路径**匹配。本项目的
`.venv/Scripts/python.exe` 没有放行规则，而普通权限又无法新增规则，
所以直接 `serve --lan` 时手机连不上。

做法
----
用系统**已经放行**的那个 Python 解释器（纯标准库，任何 3.8+ 均可）运行
本脚本：它在 0.0.0.0:8765 监听，把流量原样转发到 127.0.0.1:8766，
真正的应用只监听回环地址。手机访问 8765，电脑访问 127.0.0.1:8765
也照常可用。

    <已放行的 python.exe> scripts/lan_proxy.py
    .venv/Scripts/python.exe -c "from beverage_color_db import web; web.serve('127.0.0.1', 8766)"

如果已经用管理员权限放行了 8765，就不需要本脚本，直接：

    .venv/Scripts/python.exe -m beverage_color_db.cli serve --lan --port 8765
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=15)
    except OSError:
        client.close()
        return
    back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
    back.start()
    _pipe(client, upstream)
    back.join(timeout=3)
    client.close()
    upstream.close()


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，只让系统选默认出口网卡
            return s.getsockname()[0]
    except OSError:
        return "<本机局域网 IP>"


def main() -> None:
    ap = argparse.ArgumentParser(description="局域网 TCP 端口转发（纯标准库）")
    ap.add_argument("--listen-port", type=int, default=8765)
    ap.add_argument("--target-port", type=int, default=8766)
    ap.add_argument("--target-host", default="127.0.0.1")
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.listen_port))
    srv.listen(128)
    print(f"局域网转发已启动： 0.0.0.0:{args.listen_port} → "
          f"{args.target_host}:{args.target_port}", flush=True)
    print(f"  手机（同一 Wi-Fi）打开： http://{_lan_ip()}:{args.listen_port}/", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle,
                             args=(conn, args.target_host, args.target_port),
                             daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    sys.exit(main())
