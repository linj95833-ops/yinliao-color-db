"""下载离线扩散第一阶段权重（SD1.5 fp16），全程可走 hf-mirror，运行时无需联网。

只取 img2img 必需的子模块，跳过 safety_checker（省 1.2GB）。
下载的 .fp16.safetensors 会被**复制**成 diffusers 期望的标准名（纯新增，
不删除/不重命名，避免触发宿主文件保护）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import snapshot_download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "models" / "diffusion" / "sd15"
REPO = "sd-legacy/stable-diffusion-v1-5"

ALLOW = [
    "model_index.json",
    "scheduler/scheduler_config.json",
    "tokenizer/*",
    "text_encoder/config.json",
    "text_encoder/model.fp16.safetensors",
    "unet/config.json",
    "unet/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.fp16.safetensors",
]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"repo   : {REPO}")
    print(f"endpoint: {os.environ['HF_ENDPOINT']}")
    print(f"dest   : {DEST}")
    path = Path(snapshot_download(repo_id=REPO, local_dir=str(DEST),
                                  allow_patterns=ALLOW, max_workers=4))

    for src in sorted(path.rglob("*.fp16.safetensors")):
        dst = src.with_name(src.name.replace(".fp16.safetensors", ".safetensors"))
        if not dst.exists():
            shutil.copy2(src, dst)
            print(f"alias  : {dst.relative_to(path)}")

    files = [p for p in path.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"\n完成：{len(files)} 个文件，{total / 1024 ** 3:.2f} GiB")
    must = ["model_index.json", "unet/diffusion_pytorch_model.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
            "text_encoder/model.safetensors", "tokenizer/vocab.json"]
    for rel in must:
        p = path / rel
        print(f"  {'OK ' if p.is_file() else '缺失'} {rel}")
    if not all((path / rel).is_file() for rel in must):
        raise SystemExit("权重不完整，请重跑本脚本（支持断点续传）")


if __name__ == "__main__":
    main()
