"""Target silhouette try-on. No source silhouette or rectangular cover is used."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from scipy.interpolate import PchipInterpolator

from beverage_color_db import cloth, garment, human, wardrobe


def load_photo(raw: Image.Image, max_side=2400):
    im = ImageOps.exif_transpose(raw).convert('RGBA')
    bg = Image.new('RGBA', im.size, 'white')
    bg.alpha_composite(im)
    im = bg.convert('RGB')
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return im


def refine_mask(rgb, upper, protect, probability):
    """Complete enclosed print holes; protect skin again AFTER all morphology.

    Previous row-envelope smoothing and post-segmentation dilation could undo
    arm exclusion. This pipeline never rebuilds a mask from row endpoints.
    """
    upper = np.asarray(upper, bool)
    protect = np.asarray(protect, bool)
    filled = ndi.binary_fill_holes(upper)
    # Fill closed print holes, but never skin/arms/hair/background openings.
    mask = (upper | (filled & (probability > .12))) & ~protect
    closed = ndi.binary_closing(mask, iterations=1, border_value=1)
    mask |= closed & (probability > .18)
    mask &= ~protect
    cc, n = ndi.label(mask)
    if n:
        sizes = np.bincount(cc.ravel()); sizes[0] = 0
        mask &= sizes[cc] >= max(12, sizes.max() * .008)
    return mask


def analyze(im):
    work = im.copy(); work.thumbnail((800, 800), Image.Resampling.LANCZOS)
    info = human.parse(work)
    warning = None
    if info is None:
        seg = cloth.segment(work, engine='tone', layer_split=False,
                            edge_smooth=False, sleeve_trim=0, edge_grow=0)
        hard = seg['hard']
        warning = '人体模型不可用，当前为颜色识别；请检查并修正换装区域。'
        engine = 'tone'
    else:
        labels = info['labels']
        upper = np.isin(labels, (4, 7))  # upper/dress; never skirts/pants
        # Printed white panels can be mislabeled Bag (16). Keep skin/head/pants
        # strictly protected; enclosed weak-upper print is repaired below.
        protect = np.isin(labels, (1, 2, 3, 6, 9, 10, 11, 12, 13, 14, 15))
        hard = refine_mask(np.asarray(work), upper, protect, info['upper_prob'])
        if hard.any():
            prior = hard.copy()
            hard = refine_boundary(np.asarray(work), hard)
            hard = repair_hem_print(hard, prior, protect, info['upper_prob'])
        engine = 'segformer'
        if hard.sum() < 80:
            warning = '未可靠识别上衣，请在「修正区域」里涂出衣服后再生成。'
    mask = Image.fromarray(hard.astype('uint8') * 255)
    mask = mask.resize(im.size, Image.Resampling.NEAREST)
    return mask, {'engine': engine, 'coverage': round(float(hard.mean()), 4),
                  'warning': warning, 'work_size': list(work.size)}


def repair_hem_print(mask, prior, protect, probability):
    """Keep lower printed panels, including prints open to the image bottom.

    Color snapping can mistake white print for a white backdrop. Only restore
    the lower interior bounded by the garment; skin and pants remain excluded.
    Closing the padded bottom finds print cutouts without rebuilding arm gaps.
    """
    rows=np.flatnonzero(prior.any(axis=1))
    if not len(rows): return mask
    start=int(rows[0]+.55*(rows[-1]-rows[0]))
    padded=np.pad(prior,1)
    padded[-1,1:-1]=True
    filled=ndi.binary_fill_holes(padded)[1:-1,1:-1]
    interior=np.zeros_like(prior)
    for y in range(start,prior.shape[0]):
        xs=np.flatnonzero(prior[y])
        if len(xs)>=3: interior[y,xs[0]+1:xs[-1]]=True
    restore=interior & (prior | filled) & ~protect
    result=mask | restore
    enclosed=ndi.binary_fill_holes(result)
    result |= enclosed & ~protect & (probability>.08)
    # Remove isolated pinholes/jagged single-pixel fringes, never grow onto skin.
    smooth=ndi.gaussian_filter(result.astype(np.float32),.55)>.45
    return (smooth | restore) & ~protect


def refine_boundary(rgb, hard):
    """Snap coarse model edges to nearby image colors (including sleeve hems).

    Only a narrow boundary can change. Samples from both sides include spatial
    position, so a skin-colored garment is compared to nearby cloth and skin.
    """
    radius=max(2,round(min(hard.shape)*.012))
    inside=ndi.distance_transform_edt(hard)
    outside=ndi.distance_transform_edt(~hard)
    band=(inside<=radius)&(outside<=radius)
    fg=inside>radius; bg=outside>radius
    if fg.sum()<20 or bg.sum()<20: return hard
    h,w=hard.shape
    yy,xx=np.indices((h,w))
    features=np.concatenate([rgb.astype(np.float32),
        (xx*.6)[...,None],(yy*.6)[...,None]],axis=2)
    # Bound memory; deterministic sampling still covers all image regions.
    def tree(sel):
        pts=features[sel];step=max(1,len(pts)//60000)
        return cKDTree(pts[::step])
    d_fg=tree(fg).query(features[band],workers=1)[0]
    d_bg=tree(bg).query(features[band],workers=1)[0]
    result=hard.copy();result[band]=d_fg<d_bg
    return result


def inward_alpha(mask):
    hard = np.asarray(mask) > 127
    # Only feather inside the approved mask. Pixels outside remain byte exact.
    distance = ndi.distance_transform_edt(hard)
    width = max(1.0, min(hard.shape) / 650)
    return np.minimum(distance / width, 1).astype(np.float32)


def render(im, mask, gid, zoom=1., offset_x=0., offset_y=0., lighting=.35):
    item = wardrobe.get(gid)
    asset = garment._load(gid) if item else None
    if asset is None:
        raise ValueError('所选饮料衣服资产缺失或损坏，请换一件或重新导入。')
    hard = np.asarray(mask) > 127
    if hard.sum() < 50:
        raise ValueError('换装区域为空，请先涂出衣服区域。')
    h, w = hard.shape
    yy, xx = np.where(hard)
    y0, y1 = int(yy.min()), int(yy.max())
    _, _, centers, halves, _, _, good = garment.row_profile(hard, 0, 0)
    sy0, sy1 = int(asset['y0']), int(asset['y1'])
    sc = np.asarray(asset['center'], dtype=np.float32)
    sh = np.asarray(asset['half'], dtype=np.float32)
    strip = np.asarray(asset['strip'], dtype=np.float32)
    mid = slice(y0 + (y1-y0)//5, max(y0+1, y1-(y1-y0)//5))
    target_half = max(1, float(np.median(halves[mid])))
    source_half = max(1, float(np.median(sh[sy0:sy1+1])))
    scale = min(target_half/source_half, max(1,y1-y0)/max(1,sy1-sy0)) * zoom
    dc = ndi.gaussian_filter1d(centers.astype(float), max(2, h*.025))
    placement=asset['meta'].get('placement',{})
    source_c = float(placement.get('anchor_x',np.median(sc[sy0:sy1+1])))
    source_y = (sy0+sy1)/2
    target_y = (y0+y1)/2 + offset_y * (y1-y0)
    y = np.arange(h)[:,None]
    x = np.arange(w)[None,:]
    # Fit the full source height with a continuous, monotone map. Keep the
    # central print near its uniform scale and absorb aspect differences in
    # cloth margins, instead of clamping/repeating a row across the abdomen.
    span_s=max(1.,float(sy1-sy0)); span_d=max(1.,float(y1-y0))
    margin=.10
    inner_d=min(span_s*(1-2*margin)*scale/zoom,span_d*.84)
    mid_d=(y0+y1)/2
    knots_d=[y0,mid_d-inner_d/2,mid_d+inner_d/2,y1]
    knots_s=[sy0,sy0+span_s*margin,sy1-span_s*margin,sy1]
    vertical=PchipInterpolator(knots_d,knots_s,extrapolate=True)
    sy=vertical(mid_d+(y-target_y)/zoom)
    sx = source_c + (x-dc[:,None]-offset_x*target_half*2)/max(.001,scale)
    # Preserve logo proportions centrally, extend clean cloth at the borders.
    yi = np.clip(np.rint(sy[:,0]), sy0, sy1).astype(int)
    left = sc[yi]-sh[yi]*.94; right = sc[yi]+sh[yi]*.94
    sx = np.clip(sx, left[:,None], right[:,None])
    coords = np.array([np.broadcast_to(np.clip(sy,sy0,sy1),(h,w)), sx])
    texture = np.stack([ndi.map_coordinates(strip[...,c],coords,order=1,
                                            mode='nearest') for c in range(3)],axis=-1)
    arr = np.asarray(im,dtype=np.float32)
    # Broad illumination only: original letters/numbers must not ghost through.
    lum = arr.mean(axis=2)
    sigma = max(5, (y1-y0)*.09)
    field = ndi.gaussian_filter(lum*hard,sigma) / np.maximum(
        ndi.gaussian_filter(hard.astype(float),sigma),1e-5)
    ref = max(15,float(np.median(field[hard])))
    shade = 1 + lighting*(np.clip(field/ref,.65,1.4)-1)
    texture = np.clip(texture*shade[...,None],0,255)
    alpha = inward_alpha(mask)[...,None]
    result = np.rint(arr*(1-alpha)+texture*alpha).clip(0,255).astype('uint8')
    return Image.fromarray(result), {'garment':gid,'name':item['name_cn'],
        'size':list(im.size),'coverage':round(float(hard.mean()),4),
        'method':'target-silhouette-v2', 'zoom':zoom,
        'offset_x':offset_x,'offset_y':offset_y,'lighting':lighting}


def audit_library():
    rows=[]
    for item in wardrobe.list_items():
        asset=garment._load(item['id'])
        error=None
        if asset is None:
            error='无法读取资产'
        elif asset['strip'].shape[:2] != (int(asset['height']),int(asset['width'])):
            error='条带与轮廓尺寸不一致'
        rows.append({'id':item['id'],'name':item['name_cn'],'error':error,
                     'source_size':item.get('source_size'),
                     'thumb':f"/thumb/{item['id']}", 'hex':item.get('hex','#888888')})
    return rows
