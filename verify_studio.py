"""Regression checks and real-library contact sheet; standard unittest only."""
import base64
import io
import json
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import studio
import studio_engine as engine

ROOT=Path(__file__).resolve().parent


class RegressionTests(unittest.TestCase):
    def test_white_hem_print_and_arm(self):
        prior=np.zeros((100,100),bool);prior[30:,30:70]=1
        prior[91:,42:62]=0  # white panel mistaken for backdrop, open at bottom
        snapped=prior.copy();snapped[85:,38:65]=0
        protect=np.zeros_like(prior);protect[85:90,45:55]=1
        probability=np.full(prior.shape,.2)
        repaired=engine.repair_hem_print(snapped,prior,protect,probability)
        self.assertTrue(repaired[91:,43:61].all())
        self.assertFalse(repaired[protect].any())
        self.assertFalse(repaired[:, :28].any())

    def test_protection_after_hole_fill(self):
        upper=np.zeros((80,80),bool); upper[10:70,10:70]=1
        upper[28:35,28:35]=0  # print hole
        protect=np.zeros_like(upper);protect[35:50,35:50]=1 # crossed forearm
        upper[protect]=0
        mask=engine.refine_mask(None,upper,protect,np.ones_like(upper,float))
        self.assertTrue(mask[28:35,28:35].all())
        self.assertFalse(mask[protect].any())

    def test_similar_photos_not_same(self):
        a=np.arange(24*24*3).reshape(24,24,3).astype(float)
        self.assertFalse(engine.human._same_photo(a,a+1))
        self.assertTrue(engine.human._same_photo(a,a.copy()))

    def test_all_36_assets_render(self):
        rows=engine.audit_library();self.assertEqual(len(rows),36)
        im=Image.new('RGB',(180,240),(160,130,100))
        m=Image.new('L',im.size);ImageDraw.Draw(m).rectangle((55,75,125,239),fill=255)
        hard=np.asarray(m)>127
        for row in rows:
            with self.subTest(garment=row['id']):
                self.assertIsNone(row['error'])
                out,_=engine.render(im,m,row['id'])
                self.assertTrue(np.array_equal(np.asarray(out)[~hard],np.asarray(im)[~hard]))
                self.assertGreater(np.abs(np.asarray(out,dtype=float)[hard]-np.asarray(im)[hard]).mean(),5)

    def test_http_roundtrip(self):
        server=studio.ThreadingHTTPServer(('127.0.0.1',0),studio.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        def post(path,data):
            req=urllib.request.Request(url+path,json.dumps(data).encode(),{'Content-Type':'application/json'})
            return json.load(urllib.request.urlopen(req,timeout=60))
        try:
            rows=json.load(urllib.request.urlopen(url+'/api/library'))['items']
            im=Image.open(ROOT/'examples/portrait_sample.png')
            buf=io.BytesIO();im.save(buf,'PNG')
            data='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
            d=post('/api/upload',{'image':data})
            mask=Image.new('L',tuple(d['size']));ImageDraw.Draw(mask).rectangle((30,80,120,200),fill=255)
            buf=io.BytesIO();mask.save(buf,'PNG')
            r=post('/api/render',{'session':d['session'],'garment':rows[0]['id'],
                'mask':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
            out=Image.open(io.BytesIO(urllib.request.urlopen(url+r['url']).read()))
            self.assertEqual(out.size,tuple(d['size']))
            r2=post('/api/render',{'session':d['session'],'garment':rows[-1]['id'],
                'mask':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
            self.assertNotEqual(r['url'],r2['url'])
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post('/api/render',{'session':d['session'],'garment':'../unknown'})
            self.assertEqual(cm.exception.code,400)
        finally:
            server.shutdown();server.server_close()


def corpus(source):
    out=ROOT/'report/v2';out.mkdir(parents=True,exist_ok=True)
    folders=[p for p in Path(source).iterdir() if p.is_dir()]
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',14)
    sheet=Image.new('RGB',(1000,((len(folders)+3)//4)*230),'#f5f2eb')
    results=[]
    for i,folder in enumerate(sorted(folders)):
        files=[p for p in folder.iterdir() if p.suffix.lower() in engine.wardrobe.IMAGE_EXTS]
        if not files: continue
        with Image.open(files[0]) as raw: im=engine.load_photo(raw,max_side=600)
        mask,meta=engine.analyze(im)
        result,_=engine.render(im,mask,'kangshifu_ice_tea') if np.asarray(mask).any() else (im,{})
        hard=np.asarray(mask)>127
        unchanged=np.array_equal(np.asarray(im)[~hard],np.asarray(result)[~hard])
        assert unchanged,folder.name
        # Left = green overlay; right = generated output.
        overlay=np.asarray(im).copy();overlay[hard]=(overlay[hard]*.5+np.array([30,200,80])*.5).astype('uint8')
        tile=Image.new('RGB',(250,230),'white');draw=ImageDraw.Draw(tile);draw.text((6,3),folder.name,fill='#183d35',font=font)
        for n,img in enumerate([Image.fromarray(overlay),result]):
            img.thumbnail((122,204));tile.paste(img,(n*125+(125-img.width)//2,25))
        sheet.paste(tile,((i%4)*250,(i//4)*230))
        result.save(out/f'{engine.wardrobe.slugify(folder.name)}.png')
        results.append({'name':folder.name,**meta,'outside_unchanged':unchanged})
        print(f'{i+1}/{len(folders)} {folder.name} {meta["coverage"]}',flush=True)
    sheet.save(out/'contact-sheet.jpg')
    (out/'corpus.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='--corpus': corpus(sys.argv[2])
    else: unittest.main(verbosity=2)
