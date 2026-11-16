"""离线扩散环境一键搭建：Windows / Python 3.11 / CUDA 12.1。

与项目主 .venv（Python 3.13，纯 CPU ONNX）完全隔离，互不影响。
分步执行并记录日志与状态，失败即停，便于定位。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv-ai-final"
PY311 = Path(r"E:\Python\python.exe")
TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"
CU121 = "https://download.pytorch.org/whl/cu121"
LOG = ROOT / "report" / "_ai_install_cuda.log"
STATUS = ROOT / "report" / "ai_env_status.json"

PY = str(VENV / "Scripts" / "python.exe")
AI_LIBS = [
    "diffusers", "transformers", "accelerate", "safetensors", "huggingface_hub",
    "opencv-python-headless", "Pillow", "numpy", "scipy", "scikit-image",
]

STEPS = [
    ("create_venv", [str(PY311), "-m", "venv", str(VENV)]),
    # 宿主 sitecustomize.py 会拦截 pip 的 os.unlink（批量删除保护），
    # 任何覆盖已存在文件的安装都会被 SystemExit 掐断。因此这里不升级 pip、
    # 不碰 venv 自带的 setuptools，全部走「纯新增文件」路径；同时不装
    # controlnet-aux（它会拉 timm 并强制升级 setuptools，正是前两次的诱因）。
    # cu121 源上只有 torch 轮子，必须用 extra-index 让清华源补齐普通依赖，
    # 否则 pip 解析 torch 依赖（flit_core 等）时会报 No matching distribution。
    ("torch_cuda", [PY, "-m", "pip", "install",
                    "torch==2.5.1+cu121", "torchvision==0.20.1+cu121",
                    "--index-url", CU121, "--extra-index-url", TSINGHUA]),
    ("ai_libs", [PY, "-m", "pip", "install", *AI_LIBS, "-i", TSINGHUA]),
]

VERIFY = (
    "import json,torch,importlib.util as u;"
    "print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda,"
    "'available':torch.cuda.is_available(),"
    "'device':(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None),"
    "'libs':{m:bool(u.find_spec(m)) for m in "
    "['torchvision','diffusers','transformers','accelerate','safetensors',"
    "'huggingface_hub','cv2','skimage']}}))"
)


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    result = {"venv": str(VENV), "steps": {}, "ok": False}

    def emit(s):
        print(s, flush=True)
        lines.append(s)
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    emit(f"Python 3.11 source: {PY311} | exists: {PY311.is_file()}")
    reuse = Path(PY).is_file()
    # venv 已存在时不重建：重建会覆盖已有文件，再次触发宿主删除钩子
    active = [(n, c) for n, c in STEPS if n != "create_venv" or not reuse]
    emit(f"venv 已存在: {reuse} -> {'复用（不覆盖）' if reuse else '新建'}")
    for name, cmd in active:
        emit(f"\n===== {name} =====\n$ {' '.join(cmd)}")
        t0 = time.time()
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        emit(out[-4000:] if len(out) > 4000 else out)
        dt = round(time.time() - t0, 1)
        result["steps"][name] = {"exit": p.returncode, "seconds": dt}
        emit(f"[{name}] exit={p.returncode} {dt}s")
        if p.returncode != 0:
            emit(f"ABORT at {name}")
            STATUS.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            raise SystemExit(f"安装失败于 {name}，详见 {LOG}")

    emit("\n===== verify =====")
    p = subprocess.run([PY, "-c", VERIFY], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    emit((p.stdout or "") + (p.stderr or ""))
    if p.returncode == 0:
        try:
            result["verify"] = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            result["verify"] = {"raw": p.stdout.strip()}
    result["ok"] = bool(p.returncode == 0 and result.get("verify", {}).get("available"))
    STATUS.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    emit(f"\n环境可用: {result['ok']} | 状态: {STATUS}")
    if not result["ok"]:
        raise SystemExit("torch CUDA 不可用，请检查日志")


if __name__ == "__main__":
    main()
