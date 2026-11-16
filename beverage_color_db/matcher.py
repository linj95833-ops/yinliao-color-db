"""衣服颜色 -> 饮料品牌匹配，以及可视化结果报告。

匹配依据：多色联合 CIEDE2000。衣服色板每个色找品牌色板最近色（按衣服占比加权），
品牌色板每个色反过来找衣服色板最近色（按包装占比加权），两者组合后与权威主色
色差一起构成最终距离。人工占位档案额外加惩罚；结果里给出逐色配对明细。
"""
from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from . import color, config, db, portrait

_ORIGIN_LABEL = {"images": "图片实测", "reference": "人工占位"}


def _query_palette(query: dict[str, Any]) -> tuple[list[dict[str, Any]], list[float], str]:
    """返回 (查询色板, 主色 lab, 主色 hex)。有 palette 时优先用多色。"""
    palette = query.get("palette") or []
    if palette:
        items = []
        for c in palette[:config.MATCH_QUERY_MAX]:
            h = str(c.get("hex") or "#000000").upper()
            lab = c.get("lab")
            if not isinstance(lab, (list, tuple)) or len(lab) != 3:
                lab = list(color.hex_to_lab(h))
            items.append({"hex": h, "lab": [float(v) for v in lab],
                          "ratio": max(0.01, float(c.get("ratio", 1.0)))})
        return items, list(items[0]["lab"]), str(query.get("dominant_hex") or items[0]["hex"])
    h = str(query["hex"]).upper()
    lab = list(color.hex_to_lab(h))
    return [{"hex": h, "lab": lab, "ratio": 1.0}], lab, h


def _brand_palette(row) -> list[dict[str, Any]]:
    """品牌多色色板；权威主色 (dominant_hex) 必须排在第一位。"""
    items: list[dict[str, Any]] = []
    try:
        for c in json.loads(row["color_vector"] or "[]"):
            lab = c.get("lab")
            if isinstance(lab, (list, tuple)) and len(lab) == 3:
                items.append({"hex": str(c.get("hex") or "#000000").upper(),
                              "lab": [float(v) for v in lab],
                              "ratio": max(0.01, float(c.get("ratio", 1.0)))})
    except (ValueError, TypeError):
        pass
    dom = {"hex": str(row["dominant_hex"]).upper(),
           "lab": [float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"])],
           "ratio": 1.0}
    if items and color.ciede2000(items[0]["lab"], dom["lab"]) <= 3:
        # 用权威主色覆盖首色，但保留档案里的真实占比（不人为放大主色权重）。
        items[0]["hex"], items[0]["lab"] = dom["hex"], dom["lab"]
    else:
        items.insert(0, {**dom, "ratio": max(items[0]["ratio"], 0.5) if items else 1.0})
    return items[:config.MATCH_VECTOR_MAX]


def _joint_distance(q: list[dict[str, Any]], b: list[dict[str, Any]]
                    ) -> tuple[float, float, float, list[dict[str, Any]]]:
    """双向加权覆盖：衣服 -> 品牌、品牌 -> 衣服，返回 (联合, 正向, 反向, 明细)。"""
    qw = [c["ratio"] for c in q]
    bw = [c["ratio"] for c in b]
    qs, bs = sum(qw) or 1.0, sum(bw) or 1.0
    details: list[dict[str, Any]] = []
    q2b = 0.0
    for item, w in zip(q, qw):
        ds = [color.ciede2000(item["lab"], x["lab"]) for x in b]
        j = min(range(len(ds)), key=lambda i: ds[i])
        q2b += w * ds[j]
        details.append({"query_hex": item["hex"], "brand_hex": b[j]["hex"],
                        "delta_e": round(ds[j], 2), "weight": round(w / qs, 4)})
    q2b /= qs
    b2q = sum(w * min(color.ciede2000(x["lab"], y["lab"]) for y in q)
              for x, w in zip(b, bw)) / bs
    joint = config.MATCH_Q2B_WEIGHT * q2b + config.MATCH_B2Q_WEIGHT * b2q
    return joint, q2b, b2q, details


def match(query: dict[str, Any], top: int = 8) -> list[dict[str, Any]]:
    """query 支持 {"hex": "#RRGGBB"}、{"palette": [...]}、portrait.analyze() 结果。"""
    q_items, main, query_hex = _query_palette(query)
    out: list[dict[str, Any]] = []
    for r in db.get_profiles():
        b_items = _brand_palette(r)
        d_main = color.ciede2000(main, (r["lab_l"], r["lab_a"], r["lab_b"]))
        joint, q2b, b2q, details = _joint_distance(q_items, b_items)
        # 占位色只是估计值，额外 6 个 ΔE 惩罚，让图片实测档案在近似色差下优先。
        penalty = 0.0 if r["origin"] == "images" else 6.0
        adjusted = (config.MATCH_MAIN_WEIGHT * d_main
                    + config.MATCH_JOINT_WEIGHT * joint + penalty)
        out.append({
            "key": r["key"], "name_cn": r["name_cn"], "name_en": r["name_en"],
            "region": r["region"], "category": r["category"],
            "brand_hex": r["dominant_hex"], "query_hex": query_hex,
            "delta_e": round(d_main, 2),
            "palette_delta_e": round(q2b, 2), "joint_delta_e": round(joint, 2),
            "query_to_brand": round(q2b, 2), "brand_to_query": round(b2q, 2),
            "adjusted_delta_e": round(adjusted, 2),
            "score": round(max(0.0, 100.0 - config.MATCH_SCORE_SCALE * adjusted), 2),
            "profile_origin": r["origin"],
            "profile_origin_label": _ORIGIN_LABEL.get(r["origin"], r["origin"]),
            "profile_confidence": r["confidence"], "samples": r["sample_count"],
            "color_vector": b_items, "color_matches": details,
            "palette_size": len(q_items), "brand_palette_size": len(b_items),
        })
    out.sort(key=lambda x: (x["adjusted_delta_e"], x["delta_e"]))
    for i, item in enumerate(out[:max(1, top)], 1):
        item["rank"] = i
    return out[:max(1, top)]


def match_hex(hex_value: str, top: int = 8) -> list[dict[str, Any]]:
    """单色兼容入口。"""
    return match({"hex": hex_value}, top)


def match_colors(hex_values: list[str], top: int = 8,
                 weights: list[float] | None = None) -> dict[str, Any]:
    """直接给多个颜色做联合匹配，返回 query + matches。权重默认等权。"""
    hs = [str(h).strip().upper() for h in hex_values if str(h).strip()]
    if not hs:
        raise ValueError("至少需要一个颜色，格式 #RRGGBB")
    for h in hs:
        color.hex_to_lab(h)  # 格式校验
    if weights:
        ws = [float(w) for w in weights]
        if len(ws) != len(hs) or any(w <= 0 for w in ws):
            raise ValueError("weights 个数必须与颜色一致且为正数")
        total = sum(ws)
        ws = [w / total for w in ws]
    else:
        ws = [1.0 / len(hs)] * len(hs)
    palette = [
        {"hex": h, "lab": [round(float(v), 2) for v in color.hex_to_lab(h)],
         "ratio": round(w, 4)}
        for h, w in zip(hs, ws)
    ]
    query = {"mode": "colors", "dominant_hex": hs[0], "palette": palette,
             "confidence": 1.0}
    return {"query": query, "matches": match(query, top)}


def match_photo(source: str | Path | Image.Image, box=None, top: int = 8,
                exclude_skin: bool = True, exclude_bg: bool = True,
                engine: str | None = None) -> dict[str, Any]:
    q = portrait.analyze(source, box=box, exclude_skin=exclude_skin,
                         exclude_bg=exclude_bg, engine=engine)
    q["mode"] = "photo"
    q["source"] = str(source) if not isinstance(source, Image.Image) else "uploaded image"
    return {"query": q, "matches": match(q, top)}


def _b64(img: Image.Image, max_side: int = 900, quality: int = 78) -> str:
    im = img.convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                       Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _swatch(hex_value: str, size: int = 22, label: str = "") -> str:
    safe = html.escape(str(hex_value))
    tip = html.escape(label or safe)
    return (f'<span class="sw" style="background:{safe};width:{size}px;height:{size}px" '
            f'title="{tip}"></span>')


def build_report(payload: dict[str, Any], source=None, write: bool = True,
                 composite: dict[str, Any] | str | None = None) -> dict[str, str]:
    """生成 report/match_result.json 与 match_result.html（图片内联，可离线打开）。"""
    config.ensure_dirs()
    query = payload["query"]
    matches = payload["matches"]
    paths = {"json": str(config.MATCH_RESULT_JSON), "html": str(config.MATCH_RESULT_HTML)}
    if not write:
        return paths

    record = {"generated_at": db.utcnow(), **payload}
    config.MATCH_RESULT_JSON.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    figures: list[str] = []
    if source is not None:
        try:
            raw = portrait.load_image(source)
            debug = portrait.debug_image(source, query)
            figures.append(
                '<figure><img src="' + _b64(raw) + '" alt="上传人像">'
                '<figcaption>上传的人像照片</figcaption></figure>'
                '<figure><img src="' + _b64(debug) + '" alt="衣服区域标注">'
                '<figcaption>白线 = 按色调分割出的衣服轮廓（号码/印花一并归入衣服）；'
                '提示框只用于取色，下方为提取色板</figcaption></figure>'
            )
        except (OSError, ValueError):
            pass
    if composite:
        url = composite.get("preview") if isinstance(composite, dict) else str(composite)
        name = (composite.get("brand_name") or "") if isinstance(composite, dict) else ""
        is_dict = isinstance(composite, dict)
        if is_dict and composite.get("compose_style") == "wrap":
            cap = (f"把{html.escape(name)}包装穿上身：包装原字/图案 + 原图光影，"
                   "原号码已覆盖（本地生成，仅娱乐）")
            if composite.get("wrap_fallback_reason"):
                cap += "　[包装图不可用，已回退平涂印花：" + html.escape(
                    str(composite["wrap_fallback_reason"])) + "]"
        else:
            cap = f"衣服换成{html.escape(name)}配色 + 品牌字样/元素（本地生成，仅娱乐）"
        figures.append('<figure><img src="' + html.escape(url, quote=True) + '" '
                       'alt="衣服换色合成图"><figcaption>' + cap + '</figcaption></figure>')
    photos = '<div class="photos">' + "".join(figures) + "</div>" if figures else ""

    palette = "".join(
        f'<div class="pc"><span class="big" style="background:{html.escape(c["hex"])}"></span>'
        f'<code>{html.escape(c["hex"])}</code><small>{c["ratio"]:.0%}</small></div>'
        for c in query.get("palette", [])
    ) or _swatch(query.get("dominant_hex", "#000000"))

    cards = []
    for m in matches:
        vector = "".join(
            _swatch(c.get("hex", "#cccccc"), 16,
                    f'{c.get("hex", "")} · {float(c.get("ratio", 0)):.0%}')
            for c in (m.get("color_vector") or [])[:5]
        )
        pairs = "".join(
            f'<span class="pair">{_swatch(p["query_hex"], 12)}<i>→</i>'
            f'{_swatch(p["brand_hex"], 12)}<em>ΔE {p["delta_e"]}'
            f' ({p["weight"]:.0%})</em></span>'
            for p in (m.get("color_matches") or [])[:5]
        )
        cls = "ok" if m["profile_origin"] == "images" else "seed"
        cards.append(
            '<div class="brand">'
            f'<div class="rank">#{m["rank"]}</div>'
            f'<div class="main-sw" style="background:{html.escape(m["brand_hex"])}"></div>'
            '<div class="info">'
            f'<b>{html.escape(m["name_cn"])}</b> '
            f'<span class="en">{html.escape(m["name_en"] or "")}</span>'
            f'<small>{html.escape(m["key"])} · {html.escape(m["region"])} · '
            f'{html.escape(m["category"])} · {html.escape(m["brand_hex"])}</small>'
            f'<div class="vec">{vector}</div>'
            f'<div class="pairs">{pairs}</div></div>'
            '<div class="metrics">'
            f'<span class="de">主色 ΔE {m["delta_e"]}</span>'
            f'<span class="score">匹配 {m["score"]}</span>'
            f'<span class="tag {cls}">{html.escape(m["profile_origin_label"])}</span>'
            f'<small>联合 ΔE {m["joint_delta_e"]} · 衣→品 {m["query_to_brand"]} · '
            f'品→衣 {m["brand_to_query"]}</small>'
            f'<small>置信度 {m["profile_confidence"]} · 样本 {m["samples"]} · '
            f'色板 {m["brand_palette_size"]} 色</small>'
            '</div></div>'
        )

    mode = query.get("mode")
    title = {"photo": "人像衣服颜色", "colors": "指定的多个颜色"}.get(mode, "指定颜色")
    if mode == "photo" and query.get("confidence") is not None:
        cl = query.get("cloth") or {}
        conf_line = (f'识别置信度 {query["confidence"]} · 衣服覆盖 '
                     f'{cl.get("coverage", query.get("coverage"))} · '
                     f'分割 {cl.get("method", query.get("method", "-"))} · '
                     f'取色提示区 {query.get("box")}')
        if cl.get("fallback"):
            conf_line += "（该照片已回退到矩形取色蒙版）"
    elif mode == "colors":
        conf_line = (f'{len(query.get("palette", []))} 个颜色联合匹配 · '
                     '默认等权，可用 --weights 调整')
    else:
        conf_line = "手动指定颜色，不做图像识别"
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>饮料品牌匹配结果</title><style>
:root{{color-scheme:light}}
*{{box-sizing:border-box}}
body{{margin:0;padding:26px 30px;background:#f6f8fb;color:#1c2026;
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:22px;margin:0 0 4px}}
.lead{{color:#5b6472;margin:0 0 20px}}
.panel{{background:#fff;border:1px solid #e3e6eb;border-radius:10px;padding:18px 20px;margin-bottom:20px}}
.photos{{display:flex;gap:18px;flex-wrap:wrap}}
.photos figure{{margin:0}}
.photos img{{max-height:330px;max-width:100%;border-radius:8px;border:1px solid #e3e6eb;display:block}}
figcaption,.hint{{color:#7b8492;font-size:12px;margin-top:6px}}
.q{{display:flex;align-items:center;gap:18px;flex-wrap:wrap}}
.q .dom{{width:74px;height:74px;border-radius:10px;border:1px solid #cbd1da}}
.palette{{display:flex;gap:14px;flex-wrap:wrap}}
.pc{{display:flex;flex-direction:column;align-items:center;font-size:11px;color:#5b6472}}
.pc .big{{width:46px;height:46px;border-radius:8px;border:1px solid #cbd1da}}
.brand{{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid #e3e6eb;
 border-radius:10px;padding:12px 16px;margin-bottom:10px}}
.rank{{font-size:16px;font-weight:700;color:#98a2b0;width:38px}}
.main-sw{{width:52px;height:52px;border-radius:9px;border:1px solid #cbd1da;flex:none}}
.info{{flex:1;min-width:180px}}
.info b{{font-size:16px}} .info .en{{color:#7b8492;font-size:12px}}
.info small{{display:block;color:#8a93a0;font-size:11px}}
.sw{{display:inline-block;width:22px;height:22px;border-radius:4px;border:1px solid #cbd1da;
 margin:4px 4px 0 0;vertical-align:middle}}
.vec{{margin-top:2px}}
.pairs{{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px}}
.pair{{display:inline-flex;align-items:center;gap:3px;font-size:10px;color:#8a93a0}}
.pair .sw{{margin:0}} .pair i{{font-style:normal;color:#b6bec9}}
.pair em{{font-style:normal;margin-left:2px}}
.metrics{{text-align:right;min-width:150px}}
.metrics .de{{font-weight:600}} .metrics .score{{color:#1a7f4b;margin-left:10px}}
.metrics small{{display:block;color:#98a2b0;font-size:11px}}
.tag{{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;margin-top:4px}}
.tag.ok{{background:#e5f5ec;color:#1a7f4b}} .tag.seed{{background:#fff3d9;color:#9a6700}}
code{{font-size:11px;color:#374151}}
</style></head><body>
<h1>饮料品牌匹配结果</h1>
<p class="lead">{title} → 多色联合 CIEDE2000：衣服色板与品牌包装色板双向加权比较，
下方逐色配对展示每个衣服色命中了哪个包装色。综合色差越小越搭。</p>
{photos}
<div class="panel q">
  <div class="dom" style="background:{html.escape(query.get('dominant_hex', query.get('hex', '#000000')))}"></div>
  <div><div style="font-size:18px;font-weight:600">{html.escape(query.get('dominant_hex', query.get('hex', '-')))}</div>
  <div class="hint">{html.escape(conf_line)}</div></div>
  <div class="palette">{palette}</div>
</div>
<div class="hint" style="margin-bottom:14px">「图片实测」= 由品牌图片提色；「人工占位」= 尚未放图，用种子色匹配。
放入图片并执行 <code>scan</code> 后自动升级。本页含上传照片，属本地私有文件，请勿提交到公开仓库。</div>
{''.join(cards)}
</body></html>"""
    config.MATCH_RESULT_HTML.write_text(html_doc, encoding="utf-8")
    return paths
