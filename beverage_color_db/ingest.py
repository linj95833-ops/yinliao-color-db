"""提色入库 + 品牌主色画像聚合 + 第二阶段匹配预览。"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
from PIL import Image

from . import color, config, db, quality


def ingest(force: bool = False) -> dict[str, int]:
    images = db.get_ok_images(only_missing_color=not force)
    stats = {"images": 0, "colors": 0, "failed": 0, "profiles": 0}
    for row in images:
        path = config.ROOT / row["path"]
        try:
            with Image.open(path) as im:
                # 标准正面图（OFF / 官网 / 本地）包装占满画面，四边就是包装本身，
                # 不能再做背景剔除，否则会把品牌主色抹掉；只有 Commons 兜底图需要去背景。
                remove_bg = row["source"] == "commons"
                colors = color.extract_colors(im.convert("RGB"), remove_background=remove_bg)
            if not colors:
                raise ValueError("no dominant color extracted")
            db.save_color_features(row["id"], colors)
            stats["images"] += 1
            stats["colors"] += len(colors)
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            db.log_fetch("ingest", "color", str(path), False, f"{type(exc).__name__}: {exc}")
    stats["profiles"] = rebuild_profiles()
    return stats


def _parse_refs(value: Any) -> list[str]:
    """reference_hex 兼容 '#RRGGBB'、JSON 数组字符串、YAML 列表。"""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if str(x).startswith("#")]
    s = str(value).strip()
    if s.startswith("["):
        try:
            return [str(x) for x in json.loads(s) if str(x).startswith("#")]
        except (ValueError, TypeError):
            return []
    return [s] if s.startswith("#") else []


def _build_color_vector(rows: list[Any], dominant: dict[str, Any], score) -> list[dict[str, Any]]:
    """把每张图的 Top-N 颜色特征聚合成品牌级多色色板。

    权重 = 图片质量分 × 该色在图中的占比；相近色（CIEDE2000 < 阈值）合并，
    避免同一包装的明暗变体把色板撑爆。首位强制为品牌权威主色。
    """
    per_image: dict[int, list[Any]] = {}
    for r in rows:
        per_image.setdefault(int(r["image_id"]), []).append(r)
    feats: list[dict[str, Any]] = []
    for img_rows in per_image.values():
        img_rows = sorted(img_rows, key=lambda x: x["color_rank"])[:config.MATCH_VECTOR_MAX]
        if not img_rows:
            continue
        img_score = score(img_rows[0])
        for r in img_rows:
            ratio = float(r["ratio"] or 0.0)
            if ratio < config.MATCH_FEATURE_MIN_RATIO:
                continue
            feats.append({
                "hex": r["hex"],
                "lab": [float(r["lab_l"]), float(r["lab_a"]), float(r["lab_b"])],
                "w": (0.10 + 0.90 * img_score) * ratio,
            })
    if not feats:
        return [{"hex": dominant["hex"],
                 "lab": [round(float(v), 2) for v in dominant["lab"]],
                 "ratio": 1.0}]

    feats.sort(key=lambda f: -f["w"])
    groups: list[dict[str, Any]] = []
    for f in feats:
        hit = next(
            (g for g in groups
             if color.ciede2000(f["lab"], g["lab"]) < config.MATCH_MERGE_DELTA_E),
            None,
        )
        if hit is not None:
            hit["w"] += f["w"]
        elif len(groups) < config.MATCH_VECTOR_MAX:
            groups.append(dict(f))
        else:
            near = min(groups, key=lambda g: color.ciede2000(f["lab"], g["lab"]))
            near["w"] += f["w"]
    dom_lab = [float(v) for v in dominant["lab"]]
    # dominant_hex 由「最佳单张的 Top-1」决定（可能经过参考色校正）。把与它相近的
    # 组并成一个并移到首位，避免色板里出现重复主色、也让权威主色成为第一色。
    near_groups = [g for g in groups
                   if color.ciede2000(g["lab"], dom_lab) <= config.MATCH_MERGE_DELTA_E]
    if near_groups:
        target = min(near_groups, key=lambda g: color.ciede2000(g["lab"], dom_lab))
        for g in near_groups:
            if g is not target:
                target["w"] += g["w"]
                groups.remove(g)
        target["hex"], target["lab"] = dominant["hex"], dom_lab
        groups.remove(target)
        groups.insert(0, target)
    else:
        groups.insert(0, {"hex": dominant["hex"], "lab": dom_lab,
                          "w": max(g["w"] for g in groups) * 0.5})
    total = sum(g["w"] for g in groups) or 1.0
    return [
        {"hex": g["hex"], "lab": [round(float(v), 2) for v in g["lab"]],
         "ratio": round(g["w"] / total, 4)}
        for g in groups
    ]


def rebuild_profiles() -> int:
    """把每个品牌下所有图的颜色特征聚合成品牌级多色 color_vector。"""
    built = 0
    with db.connect() as con:
        brands = list(con.execute("SELECT id, key, reference_hex FROM brands"))
        for brand in brands:
            rows = list(con.execute(
                """SELECT cf.image_id, cf.color_rank, cf.hex, cf.lab_l, cf.lab_a, cf.lab_b, cf.ratio,
                          COALESCE(i.trust, 1.0) AS trust, i.quality_json, i.source
                   FROM color_features cf JOIN images i ON i.id = cf.image_id
                   WHERE i.brand_id = ? AND i.status = 'ok'""", (brand["id"],)
            ))
            if not rows:
                continue
            # 用户手动填的图视为权威：一旦存在，只用它们建档（采信模式）。
            user_rows = [r for r in rows if r["source"] == config.USER_IMAGE_SOURCE]
            user_only = bool(user_rows)
            if user_rows:
                rows = user_rows
            # 每张图只取它的「品牌识别色」(color_rank=0) 参与品牌级聚合，
            # 避免把单张图里的次要色（背景、深色液体、混装的其他品牌）混成品牌主色。
            tops = [r for r in rows if r["color_rank"] == 0]
            pool = tops or rows
            def score(r) -> float:
                try:
                    m = json.loads(r["quality_json"] or "{}")
                except (ValueError, TypeError):
                    m = {}
                return quality.priority_score(m, r["trust"] or 1.0)

            # 取最干净的 3 张生成 color_vector；但 dominant_hex 由**最佳单张**决定。
            # 多图等权/加权聚类会被白底、暗背景稀释，宁缺毋滥下用最可信的一张更稳。
            pool = sorted(pool, key=score, reverse=True)[:3]
            # 参考色只用于「画像择优校正」：当质量分最高的那张恰好是限定版配色
            # 或白底稀释色时，若另一张明显更接近人工参考色，则改用它。
            # 参考色不参与采集、不参与第二阶段 CIEDE2000 匹配。
            best = pool[0]
            refs = _parse_refs(brand["reference_hex"])
            if refs and len(pool) > 1:
                try:
                    ref_labs = [color.hex_to_lab(x) for x in refs]

                    def delta(r) -> float:
                        lab = (r["lab_l"], r["lab_a"], r["lab_b"])
                        return min(color.ciede2000(lab, ref) for ref in ref_labs)

                    # 必须是「真的接近」参考色（ΔE<=15）才替换；否则像深灰 vs 深蓝
                    # 这种低亮度色差只有 24，会把品牌色换成更差的近黑色。
                    if delta(best) > 35:
                        closer = min(pool, key=delta)
                        if delta(closer) <= 15 and delta(closer) + 10 < delta(best):
                            best = closer
                except ValueError:
                    pass
            dominant_full = {
                "hex": best["hex"],
                "lab": [best["lab_l"], best["lab_a"], best["lab_b"]],
            }
            # 多色色板：用全部颜色特征（不只是 Top-1）聚合，供联合匹配使用。
            vector = _build_color_vector(rows, dominant_full, score)

            # 一致性：各图主色两两色差均值；样本数：覆盖度
            sample_count = len(pool)
            if sample_count > 1:
                pairs = [
                    color.ciede2000((a["lab_l"], a["lab_a"], a["lab_b"]),
                                    (b["lab_l"], b["lab_a"], b["lab_b"]))
                    for i, a in enumerate(tops) for b in tops[i + 1:]
                ]
                dispersion = float(np.mean(pairs)) if pairs else 0.0
                consistency = max(0.0, 1.0 - dispersion / 40.0)
            else:
                consistency = 0.5
            coverage = min(1.0, sample_count / 3.0)
            confidence = round(0.6 * consistency + 0.4 * coverage, 4)
            # 采信模式：用户手动填的图被视为正确，不因样本少而低估置信度。
            if user_only:
                confidence = round(max(confidence, 0.80), 4)

            db.save_profile(brand["id"], dominant_full, vector, confidence, sample_count)
            built += 1
    return built


def match_color(hex_value: str, top: int = 8) -> list[dict[str, Any]]:
    """兼容入口：给定颜色，按 CIEDE2000 返回最匹配品牌（逻辑在 matcher）。"""
    from . import matcher

    return matcher.match_hex(hex_value, top)
