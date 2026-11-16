"""Reproducible single-file release. Explicit resources exclude user photos."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from studio_runtime import VERSION

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'release-assets'
BUILD=ROOT/'build/singlefile'


def main():
    OUT.mkdir(exist_ok=True);BUILD.mkdir(parents=True,exist_ok=True)
    from scripts.build_exe import make_icon
    make_icon(BUILD/'drink.ico')
    version=BUILD/'version.txt'
    version.write_text('''VSVersionInfo(ffi=FixedFileInfo(filevers=(2,1,0,0),prodvers=(2,1,0,0),mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('080404B0',[StringStruct('CompanyName','linj95833-ops'),StringStruct('FileDescription','饮料换装工作室'),StringStruct('FileVersion','2.1.0'),StringStruct('InternalName','DrinkStudio'),StringStruct('OriginalFilename','DrinkStudio.exe'),StringStruct('ProductName','饮料换装工作室'),StringStruct('ProductVersion','2.1.0'),StringStruct('LegalCopyright','Copyright (c) 2026 碎觉')])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])''',encoding='utf-8')
    cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--console',
        '--name',f'DrinkStudio-v{VERSION}','--distpath',str(OUT),
        '--workpath',str(BUILD/'pyinstaller'),'--specpath',str(BUILD),
        '--icon',str(BUILD/'drink.ico'),'--version-file',str(version),
        '--add-data',f'{ROOT/"web/studio.html"};web',
        '--add-data',f'{ROOT/"models/segformer_clothes.onnx"};models',
        '--collect-binaries','onnxruntime',
        '--hidden-import','onnxruntime.capi.onnxruntime_pybind11_state']
    # The old index contains a workstation path and stale import summaries.
    # Runtime enumerates item directories directly and does not need that file.
    for folder in sorted((ROOT/'data/wardrobe').iterdir()):
        if folder.is_dir(): cmd += ['--add-data',f'{folder};data/wardrobe/{folder.name}']
    for name in ['使用说明_单文件版.txt','LICENSE','ATTRIBUTION.md']:
        cmd += ['--add-data',f'{ROOT/name};docs']
    for name in ['matplotlib','IPython','pytest','tkinter','aiohttp']:
        cmd += ['--exclude-module',name]
    subprocess.run(cmd+[str(ROOT/'studio.py')],cwd=ROOT,check=True)
    package()


def package():
    # Offline source includes exactly the app resources, never outputs/report.
    files=['studio.py','studio_runtime.py','studio_engine.py','build_release.py',
           'build_studio.py','verify_studio.py','verify_ui.cjs','repair_heqizheng.py',
           'requirements.txt','requirements-build.txt','README.md','LICENSE',
           'ATTRIBUTION.md','使用说明_单文件版.txt']
    dirs=['beverage_color_db','config','examples','scripts','data/wardrobe']
    archive=OUT/f'DrinkStudio-v{VERSION}-source.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in files: z.write(ROOT/name,'DrinkStudio/'+name)
        for name in dirs:
            for p in sorted((ROOT/name).rglob('*')):
                if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'):
                    if p.name=='index.json' and p.parent.name=='wardrobe': continue
                    z.write(p,'DrinkStudio/'+p.relative_to(ROOT).as_posix())
        z.write(ROOT/'web/studio.html','DrinkStudio/web/studio.html')
        z.write(ROOT/'models/segformer_clothes.onnx','DrinkStudio/models/segformer_clothes.onnx')
    shutil.copy2(ROOT/'使用说明_单文件版.txt',OUT/'READ_ME.txt')
    names=[f'DrinkStudio-v{VERSION}.exe',archive.name,'READ_ME.txt']
    sums=[]
    for name in names:
        p=OUT/name;sums.append(f'{hashlib.file_digest(p.open("rb"),"sha256").hexdigest()}  {name}')
    (OUT/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='utf-8')
    print(json.dumps({'version':VERSION,'assets':[{'name':n,'bytes':(OUT/n).stat().st_size} for n in names]},indent=2),flush=True)


if __name__=='__main__':
    if '--package-only' in sys.argv: package()
    else: main()
