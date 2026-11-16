"""Drink Studio local server, separate from the legacy matching application."""
from __future__ import annotations
import argparse
import base64
import io
import json
import math
import re
import sys
import threading
import time
import uuid
import webbrowser
from studio_runtime import resource_root, VERSION
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError
from beverage_color_db import config, wardrobe
import studio_engine as engine

ROOT=resource_root()
# The distributable keeps user-editable assets beside the executable.
if getattr(sys,'frozen',False):
    old_root=config.ROOT
    for name in dir(config):
        val=getattr(config,name)
        if isinstance(val,Path) and (val==old_root or old_root in val.parents):
            setattr(config,name,ROOT/val.relative_to(old_root))
STORE=ROOT/'outputs'
LOCK=threading.Lock()
ID=re.compile(r'^[0-9a-f]{32}$')


def decode(value, mask=False):
    if not isinstance(value,str) or not value.startswith('data:image/'):
        raise ValueError('请选择图片文件。')
    try:
        raw=base64.b64decode(value.split(',',1)[1],validate=True)
        if len(raw)>20*1024*1024: raise ValueError('图片不能超过 20MB。')
        with Image.open(io.BytesIO(raw)) as im:
            if im.width*im.height>24_000_000: raise ValueError('图片像素过大，请缩小到 2400 万像素以内。')
            im.load()
            return im.convert('L') if mask else engine.load_photo(im)
    except (UnidentifiedImageError,IndexError) as exc:
        raise ValueError('无法读取图片，请使用 JPG、PNG 或 WebP。') from exc


def session_dir(sid):
    if not isinstance(sid,str) or not ID.fullmatch(sid): raise ValueError('照片会话无效，请重新上传。')
    path=STORE/sid
    if not (path/'photo.png').is_file(): raise ValueError('照片会话不存在，请重新上传。')
    return path


def numeric(req,name,default,lo,hi):
    val=float(req.get(name,default))
    if not math.isfinite(val) or not lo<=val<=hi: raise ValueError(f'{name} 参数超出范围。')
    return val


class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def send(self,code,body,kind='application/json; charset=utf-8'):
        self.send_response(code); self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
    def json(self,code,value):
        self.send(code,json.dumps(value,ensure_ascii=False).encode())
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/':
            return self.send(200,(ROOT/'web/studio.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/library':
            return self.json(200,{'items':engine.audit_library()})
        if path.startswith('/thumb/'):
            gid=path.removeprefix('/thumb/')
            if not wardrobe.get(gid): return self.json(404,{'error':'饮料不存在'})
            raw=wardrobe.thumb_bytes(gid)
            if raw: return self.send(200,raw,'image/jpeg' if raw[:2]==b'\xff\xd8' else 'image/webp')
        if path.startswith('/media/'):
            parts=path.split('/')
            if len(parts)==4 and ID.fullmatch(parts[2]) and re.fullmatch(r'(photo|mask|[0-9a-f]{32})\.(png|json)',parts[3]):
                file=STORE/parts[2]/parts[3]
                if file.is_file(): return self.send(200,file.read_bytes(),'application/json' if file.suffix=='.json' else 'image/png')
        return self.json(404,{'error':'找不到文件'})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=30*1024*1024: return self.json(413,{'error':'请求为空或超过 30MB。'})
            req=json.loads(self.rfile.read(size))
            if not isinstance(req,dict): raise ValueError('请求格式错误。')
            path=urlparse(self.path).path
            if path=='/api/upload':
                im=decode(req.get('image'))
                with LOCK: mask,meta=engine.analyze(im)
                sid=uuid.uuid4().hex; folder=STORE/sid; folder.mkdir(parents=True)
                im.save(folder/'photo.png'); mask.save(folder/'mask.png')
                (folder/'analysis.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
                return self.json(200,{'session':sid,'photo':f'/media/{sid}/photo.png',
                    'mask':f'/media/{sid}/mask.png','size':list(im.size),**meta})
            if path=='/api/render':
                folder=session_dir(req.get('session'))
                gid=req.get('garment')
                if not isinstance(gid,str) or not wardrobe.get(gid): raise ValueError('请先选择饮料。')
                im=Image.open(folder/'photo.png').convert('RGB')
                mask=decode(req['mask'],mask=True) if req.get('mask') else Image.open(folder/'mask.png').convert('L')
                if mask.size!=im.size: raise ValueError('修正区域与照片尺寸不符，请重新上传。')
                args={k:numeric(req,k,d,lo,hi) for k,d,lo,hi in (
                    ('zoom',1,.5,2),('offset_x',0,-.5,.5),('offset_y',0,-.5,.5),('lighting',.35,0,1))}
                started=time.monotonic()
                with LOCK: result,meta=engine.render(im,mask,gid,**args)
                rid=uuid.uuid4().hex; result.save(folder/f'{rid}.png')
                mask.save(folder/f'{rid}_mask.png')
                meta.update(elapsed=round(time.monotonic()-started,2))
                (folder/f'{rid}.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
                return self.json(200,{'url':f'/media/{folder.name}/{rid}.png',**meta})
            return self.json(404,{'error':'接口不存在'})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            self.json(400,{'error':str(exc)})
        except Exception as exc:
            import traceback; traceback.print_exc()
            self.json(500,{'error':f'处理失败：{type(exc).__name__}: {exc}'})


def main():
    ap=argparse.ArgumentParser(description='饮料换装工作室')
    ap.add_argument('--port',type=int,default=8877)
    ap.add_argument('--no-browser',action='store_true')
    ap.add_argument('--check',action='store_true')
    args=ap.parse_args()
    if args.check:
        rows=engine.audit_library()
        print(json.dumps({'count':len(rows),'errors':[r for r in rows if r['error']],
                          'model':engine.human.available()},ensure_ascii=False,indent=2))
        return
    STORE.mkdir(exist_ok=True)
    for port in range(args.port,args.port+30):
        try: srv=ThreadingHTTPServer(('127.0.0.1',port),Handler); break
        except OSError:
            if port==args.port+29: raise
    url=f'http://127.0.0.1:{port}'
    print(f'饮料换装工作室 v{VERSION}：{url}\n结果目录：{STORE}',flush=True)
    if not args.no_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: srv.server_close()


if __name__=='__main__': main()
