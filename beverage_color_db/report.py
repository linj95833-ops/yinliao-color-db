"""质检报告 + CC BY-SA 署名清单生成。"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import color, config, db


def _rows(sql: str, args=()) -> list:
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, args)]


def build_report() -> dict[str, Any]:
    brands = _rows("SELECT * FROM brands ORDER BY key")
    images = _rows("SELECT * FROM images WHERE status = 'ok' ORDER BY brand_id, id")
    profiles = {r["brand_id"]: r for r in _rows("SELECT * FROM brand_color_profile")}
    logs = _rows("SELECT ok, message FROM fetch_log")

    by_brand: dict[int, list[dict]] = {}
    for im in images:
        by_brand.setdefault(im["brand_id"], []).append(im)

    per_brand = []
    for b in brands:
        ims = by_brand.get(b["id"], [])
        p = profiles.get(b["id"])
        # 一个品牌可能有多种包装配色（如王老吉红罐 / 绿盒），参考色允许填多个
        ref_raw = b.get("reference_hex")
        if isinstance(ref_raw, str) and ref_raw.strip().startswith("["):
            try:
                refs = [str(x) for x in json.loads(ref_raw)]
            except (ValueError, TypeError):
                refs = []
        elif isinstance(ref_raw, (list, tuple)):
            refs = [str(x) for x in ref_raw]
        elif ref_raw:
            refs = [str(ref_raw)]
        else:
            refs = []
        warn = None
        if p and refs:
            try:
                d = min(color.ciede2000(color.hex_to_lab(r),
                                        (p["lab_l"], p["lab_a"], p["lab_b"])) for r in refs)
                if d > 25:
                    warn = f"与全部参考主色最小色差 {d:.1f}，建议复核（可能是另一种包装配色或场景照）"
            except ValueError:
                warn = "reference_hex 格式无效"
        per_brand.append({
            "key": b["key"], "name_cn": b["name_cn"], "region": b["region"],
            "category": b["category"], "images": len(ims),
            "dominant_hex": p["dominant_hex"] if p else None,
            "reference_hex": "/".join(refs) or None,
            "avg_bg_score": round(sum(x["bg_score"] if x["bg_score"] is not None else 1.0
                                      for x in ims) / len(ims), 3) if ims else None,
            "confidence": p["confidence"] if p else None,
            "samples": p["sample_count"] if p else 0,
            "warning": warn,
        })

    sources = Counter(im["source"] for im in images)
    licenses = Counter(im["license"] or "UNKNOWN" for im in images)
    regions = Counter(
        b["region"] for b in per_brand if b["images"] > 0
    )
    failures = Counter(
        (r["message"] or "").split(":")[0] for r in logs if not r["ok"]
    )
    missing = [b["key"] for b in per_brand if b["images"] == 0]

    dominants = []
    for im in images:
        try:
            dominants.append(json.loads(im.get("quality_json") or "{}")["dominant"])
        except (ValueError, TypeError, KeyError):
            pass
    rejected_by_gate = sum(n for reason, n in failures.items()
                           if reason.startswith("quality gate"))

    report = {
        "generated_at": db.utcnow(),
        "summary": {
            "total_brands": len(brands),
            "brands_with_images": sum(1 for b in per_brand if b["images"] > 0),
            "brands_missing_images": len(missing),
            "total_images": len(images),
            "total_color_features": _rows("SELECT COUNT(*) AS n FROM color_features")[0]["n"],
            "profiles_built": len(profiles),
            "images_by_source": dict(sources),
            "images_by_license": dict(licenses),
            "brands_with_images_by_region": dict(regions),
            "fetch_failures_by_reason": dict(failures),
            # 新方案下 OFF 正面图 bg_score 天然偏低（四角就是包装），
            # 用 trust / 质检拒收数 / 主色集中度衡量质量才不误导。
            "avg_trust": (
                round(sum(im["trust"] if im["trust"] is not None else 1.0
                          for im in images) / len(images), 3) if images else 0.0
            ),
            "avg_dominant_ratio": (
                round(sum(dominants) / len(dominants), 3) if dominants else 0.0
            ),
            "quality_gate_rejected": rejected_by_gate,
            "avg_background_score": (
                round(sum(im["bg_score"] if im["bg_score"] is not None else 1.0
                          for im in images) / len(images), 3)
                if images else 0.0
            ),
        },
        "missing_brands": missing,
        "per_brand": per_brand,
    }

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORT_DIR / "quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (config.REPORT_DIR / "quality.md").write_text(_markdown(report), encoding="utf-8")
    config.ATTRIBUTION_FILE.write_text(_attribution(images), encoding="utf-8")
    build_gallery(images, profiles)
    return report


def build_gallery(images: list[dict], profiles: dict[int, dict]) -> str:
    """生成可视化复核页：每张入库图片的缩略图 + 主色块 + 图源/授权/质量分。

    缩略图内联为 base64，HTML 单文件可直接打开，不依赖 data/images 的相对路径。
    """
    import base64
    import io

    from PIL import Image

    from . import quality

    by_brand: dict[int, list[str]] = {}
    for im in images:
        try:
            with Image.open(config.ROOT / im["path"]) as src:
                thumb = src.convert("RGB")
                thumb.thumbnail((190, 190), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                thumb.save(buf, "JPEG", quality=72)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except (OSError, ValueError):
            continue
        try:
            metrics = json.loads(im.get("quality_json") or "{}")
        except (ValueError, TypeError):
            metrics = {}
        score = quality.priority_score(metrics, im.get("trust") or 1.0)
        lic = (im.get("license") or "UNKNOWN").split("—")[0].strip()
        card = (
            '<figure class="card">'
            f'<img src="data:image/jpeg;base64,{b64}" alt="{im["path"]}" loading="lazy">'
            f'<figcaption><span class="tag">{im["source"]}</span>'
            f'<span class="tag">{lic}</span>'
            f'<span class="score">质量分 {score:.3f}</span><br>'
            f'<code>{im["width"]}×{im["height"]}</code> · '
            f'<span class="path">{im["path"].rsplit("/", 1)[-1]}</span>'
            '</figcaption></figure>'
        )
        by_brand.setdefault(im["brand_id"], []).append(card)

    names = {r["id"]: (r["key"], r["name_cn"]) for r in _rows("SELECT id, key, name_cn FROM brands")}
    sections = []
    for bid, cards in sorted(by_brand.items(), key=lambda x: names.get(x[0], ("", ""))[0]):
        key, cn = names.get(bid, (str(bid), ""))
        p = profiles.get(bid)
        swatch = ""
        if p:
            swatch = (f'<span class="swatch" style="background:{p["dominant_hex"]}"></span>'
                      f'<code>{p["dominant_hex"]}</code>')
        sections.append(
            f'<section><h2>{cn} <small>{key}</small> '
            f'<span class="count">{len(cards)} 张</span> {swatch}</h2>'
            f'<div class="grid">{"".join(cards)}</div></section>'
        )

    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>饮料品牌图片库 · 入库图片复核</title><style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px;background:#f7f8fa;color:#1c2026;
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
h1{font-size:22px;margin:0 0 6px}
.lead{color:#5b6472;margin:0 0 26px}
section{margin-bottom:30px}
h2{font-size:16px;margin:0 0 12px;display:flex;align-items:center;gap:10px;
 border-bottom:1px solid #e3e6eb;padding-bottom:8px}
h2 small{color:#8a93a0;font-weight:400;font-size:12px}
.count{font-size:12px;font-weight:400;color:#5b6472;background:#e9edf3;
 padding:2px 9px;border-radius:10px}
.swatch{width:18px;height:18px;border-radius:4px;border:1px solid #cbd1da;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
.card{margin:0;background:#fff;border:1px solid #e3e6eb;border-radius:9px;
 overflow:hidden;box-shadow:0 1px 3px rgba(20,30,50,.05)}
.card img{width:100%;height:170px;object-fit:contain;background:#fafbfc;display:block}
figcaption{padding:8px 10px;font-size:11px;color:#5b6472}
.tag{display:inline-block;background:#eef2f7;border-radius:4px;padding:1px 6px;margin:0 4px 4px 0}
.score{color:#1a7f4b}
.path{color:#8a93a0}
code{font-size:11px;color:#374151}
</style></head><body>
<h1>饮料品牌图片库 · 入库图片复核</h1>
<p class="lead">每张图都是最终入库的成品。缩略图内联，可离线打开；点开卡片可核对图源、授权与质量分。
如发现脏图，删除对应文件后运行 <code>reconcile</code> 并重新 <code>ingest --force</code>。</p>
""" + "".join(sections) + "</body></html>"

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORT_DIR / "gallery.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def _markdown(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        "# 饮料品牌图片库 · 质检报告", "",
        f"生成时间：{r['generated_at']}", "",
        "## 总览", "",
        "| 指标 | 值 |", "| --- | --- |",
        f"| 品牌总数 | {s['total_brands']} |",
        f"| 有图品牌 | {s['brands_with_images']} |",
        f"| 缺图品牌 | {s['brands_missing_images']} |",
        f"| 图片总数 | {s['total_images']} |",
        f"| 颜色特征行数 | {s['total_color_features']} |",
        f"| 品牌主色画像 | {s['profiles_built']} |",
        f"| 平均图源可信度 | {s['avg_trust']} |",
        f"| 平均主色集中度 | {s['avg_dominant_ratio']} |",
        f"| 质检闸门拒收 | {s['quality_gate_rejected']} |",
        f"| 按图源 | {s['images_by_source']} |",
        f"| 按授权 | {s['images_by_license']} |",
        f"| 有图品牌(国内/海外) | {s['brands_with_images_by_region']} |",
        "", "## 品牌明细", "",
        "| key | 名称 | 地区 | 品类 | 图片数 | 主色 | 参考色 | 置信度 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for b in r["per_brand"]:
        lines.append(
            f"| {b['key']} | {b['name_cn']} | {b['region']} | {b['category']} | "
            f"{b['images']} | {b['dominant_hex'] or '-'} | {b['reference_hex'] or '-'} | "
            f"{b['confidence'] if b['confidence'] is not None else '-'} | {b['warning'] or ''} |"
        )
    lines += ["", "## 缺图品牌（需补本地素材或手工 URL）", ""]
    lines.append(", ".join(r["missing_brands"]) or "无")
    lines += ["", "## 抓取失败原因分布", ""]
    for reason, n in (s["fetch_failures_by_reason"] or {"无": 0}).items():
        lines.append(f"- {reason}: {n}")
    return "\n".join(lines) + "\n"


def _attribution(images: list[dict]) -> str:
    lines = [
        "# 图片署名清单（CC BY-SA / 授权合规）", "",
        "本仓库**不分发图片二进制**。图片由 `python -m beverage_color_db.cli fetch` 按需重建，",
        "下表列出每张重建图片对应的来源、授权与署名，满足 CC BY-SA 的署名要求。", "",
        "| 本地文件 | 图源 | 授权 | 署名 | 来源页 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for im in images:
        page = im["source_page"] or im["source_url"] or "-"
        lines.append(
            f"| `{im['path']}` | {im['source']} | {im['license'] or 'UNKNOWN'} | "
            f"{(im['attribution'] or '-').replace('|', '/')} | {page} |"
        )
    lines += [
        "", "## 授权说明", "",
        "- Wikimedia Commons：逐张按 `LicenseShortName` 记录（多为 CC BY-SA / CC BY / 公有领域）。",
        "- Open Food Facts：图片 CC BY-SA 3.0；其数据库为 ODbL，衍生数据库需按 ODbL 提供。",
        "- OFFICIAL-SITE：品牌官网公开产品页，图片版权归品牌方所有；本项目只记录来源页、"
        "不分发二进制，仅作非商用娱乐研究参考，请勿据此主张再分发权利。",
        "- USER_PROVIDED：由使用者自行提供并确认授权，本项目不作授权背书。",
        "- 本项目仅用于非商业娱乐用途；如改变用途，请重新核对每张图片的授权与商标使用规范。",
    ]
    return "\n".join(lines) + "\n"
