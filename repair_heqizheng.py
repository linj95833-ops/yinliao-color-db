"""Re-extract the whole vest; the previous asset cropped off 和 and shifted it."""
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from beverage_color_db import garment,wardrobe

ROOT=Path(__file__).resolve().parent

def main():
    src=next((Path.home()/'Desktop/饮料/和其正').iterdir())
    im=Image.open(src).convert('RGB')
    # Measured on the original 176x176 image: garment only, preserving neckline.
    points=[(48,65),(63,56),(66,64),(70,72),(77,79),(84,83),(91,84),
            (99,80),(106,71),(112,60),(128,63),(127,82),(126,103),
            (126,130),(132,175),(43,175),(47,144),(48,120),(47,99),(48,80)]
    full=Image.new('L',im.size);ImageDraw.Draw(full).polygon(points,fill=255)
    crop=(42,54,134,176)
    rgb=np.asarray(im.crop(crop).resize((368,488),Image.Resampling.LANCZOS),dtype=float)
    mask=np.asarray(full.crop(crop).resize((368,488),Image.Resampling.NEAREST))>127
    left,right,center,half,y0,y1,good=garment.row_profile(mask,0,0)
    strip=garment.row_fill(rgb,mask,left,right,good)
    out=ROOT/'data/wardrobe/heqizheng'
    Image.fromarray(strip.clip(0,255).astype('uint8')).save(out/'strip.webp',quality=97)
    Image.fromarray(mask.astype('uint8')*255).save(out/'mask.png')
    np.savez_compressed(out/'profile.npz',center=center.astype('float32'),
        half=half.astype('float32'),good=good,y0=y0,y1=y1,height=488,width=368,
        width_ratio=0.,shrink=0.)
    m=json.loads((out/'meta.json').read_text(encoding='utf-8'))
    pal=wardrobe.garment_palette(rgb,mask,k=5)
    m.update(size=[368,488],src_rows=[y0,y1],mask_bbox=list(crop),
        mask_coverage=round(float(mask.mean()),4),palette=pal,hex=pal[0]['hex'],
        placement={'anchor_x':(91-42)*4,'note':'以完整和其正竖排文字中心定位'},
        segment_engine='reviewed-source-polygon-v21',
        upscale={'upscaled':False,'method':'Lanczos 4x; original letters retained'})
    (out/'meta.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    print('heqizheng repaired:',m['size'],m['src_rows'],m['placement'])

if __name__=='__main__':main()
