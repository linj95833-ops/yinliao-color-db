"""Build the independent Windows release using the current Python environment."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DIST=ROOT/'release'
cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--console',
     '--name','DrinkStudio','--distpath',str(DIST),'--workpath',str(ROOT/'build/studio'),
     '--specpath',str(ROOT/'build'),'--collect-binaries','onnxruntime',
     '--hidden-import','onnxruntime.capi.onnxruntime_pybind11_state',
     '--exclude-module','matplotlib','--exclude-module','IPython',
     '--exclude-module','pytest','--exclude-module','tkinter',
     '--exclude-module','aiohttp',str(ROOT/'studio.py')]
subprocess.run(cmd,cwd=ROOT,check=True)
out=DIST/'DrinkStudio'
for rel in ['data/wardrobe','web']:
    shutil.copytree(ROOT/rel,out/rel,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__','index.html','wardrobe.html'))
(out/'models').mkdir(exist_ok=True)
shutil.copy2(ROOT/'models/segformer_clothes.onnx',out/'models/segformer_clothes.onnx')
for rel in ['使用说明.txt','LICENSE','ATTRIBUTION.md']:
    shutil.copy2(ROOT/rel,out/rel)
print('Release:',out)
