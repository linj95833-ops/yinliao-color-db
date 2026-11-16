#!/usr/bin/env bash
# 饮料换装 · macOS / Linux 启动脚本（Windows 用 start.bat）
set -e
cd "$(dirname "$0")"
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
case "${1:-}" in
  --lan)   shift; exec "$PY" start.py --lan "$@" ;;
  --proxy) shift; exec "$PY" start.py --proxy "$@" ;;
  --check) shift; exec "$PY" start.py --check "$@" ;;
  *)       exec "$PY" start.py "$@" ;;
esac
