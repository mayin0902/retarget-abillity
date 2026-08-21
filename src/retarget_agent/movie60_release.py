# ruff: noqa: E501
"""Materialize and validate the single supported Movie60 v3 review workspace.

The public interface intentionally has two operations: build a new immutable
workspace from frozen evidence, and validate a materialized workspace.  All
path conventions and cross-version joins stay behind that interface.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .review_localization import localize_reason_codes, localize_strict_review

METHODS = (
    "crop",
    "direct_warp",
    "mesh",
    "mesh_full",
    "seam",
    "seam_full",
    "seam_scale",
)
RELEASE_ID = "movie60-review-v3"
DATASET_ID = "movie-visual-60-v1"
DATASET_VERSION = "1.0.0"
STRATEGY_ID = "movie60"
STRATEGY_VERSION = "3.2.2"
STRATEGY_SHA256 = "49a74b7132b0efe8cf4b014644db7a56d77b8820df1d21b4388d0a33e81ecd73"
EVALUATION_ID = "movie60-human-aligned-v3-2-2-20260821"
LABEL_SOURCE = "human_screened_large_model_proxy_not_human_ground_truth"


@dataclass(frozen=True)
class Movie60V3Sources:
    """Frozen inputs required to reconstruct the v3 review workspace."""

    repository: Path
    base_workspace: Path
    run: Path
    evaluation: Path
    development_overview: Path
    holdout_overview: Path
    development_advisory: Path
    holdout_advisory: Path
    development_visual_review: Path
    holdout_visual_review: Path

    def resolved(self) -> Movie60V3Sources:
        return Movie60V3Sources(
            **{field: Path(getattr(self, field)).resolve() for field in self.__dataclass_fields__}
        )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _method_from_candidate(candidate_id: str) -> str:
    parts = candidate_id.split("--")
    if len(parts) != 3 or parts[1] not in METHODS:
        raise ValueError(f"invalid Movie60 candidate id: {candidate_id}")
    return parts[1]


def _source_image(task_dir: Path) -> Path:
    matches = sorted(
        path
        for path in task_dir.glob("00_source.*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if len(matches) != 1:
        raise ValueError(f"expected one source image in {task_dir}, found {len(matches)}")
    return matches[0]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    fitted = ImageOps.contain(normalized, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "#f1f2f4")
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def _comparison(
    source_path: Path,
    result_path: Path,
    output_path: Path,
    *,
    task_id: str,
    method: str,
    grade: str,
) -> None:
    canvas = Image.new("RGB", (1920, 1080), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1920, 100), fill="#111820")
    draw.text((42, 25), task_id, font=_font(38), fill="white")
    draw.text(
        (1878, 34),
        f"v3.2.2 · Rule Top1 · {method} · {grade}",
        font=_font(25),
        fill="#d9dde2",
        anchor="ra",
    )
    with Image.open(source_path) as source:
        canvas.paste(_fit(source, (900, 850)), (30, 160))
    with Image.open(result_path) as result:
        canvas.paste(_fit(result, (900, 850)), (990, 160))
    draw.text((30, 115), "原图", font=_font(31), fill="#222222")
    draw.text((990, 115), "当前 Rule Top1", font=_font(31), fill="#222222")
    draw.text(
        (30, 1035),
        "策略 movie60@3.2.2；机器等级是代理指标，不是完整人工金标。",
        font=_font(25),
        fill="#525a64",
        anchor="lm",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=90, optimize=True)


def _percent(value: Any) -> str:
    return "未测" if value is None else f"{float(value) * 100:.1f}%"


def _rule_reason(rank: int, metrics: dict[str, Any]) -> str:
    parts = [
        f"v3.2.2 Rule 分 {float(metrics['quality_score']):.2f}，排名 {rank}/7",
        f"内容保真 {_percent(metrics.get('content_fidelity_score'))}",
        f"视觉完整 {_percent(metrics.get('visual_integrity_score'))}",
        f"构图 {_percent(metrics.get('composition_score'))}",
        f"结构线 {_percent(metrics.get('structure_line_similarity'))}",
        f"变换安全 {_percent(metrics.get('transform_safety_score'))}",
    ]
    for label, key in (
        ("OCR字符召回", "ocr_character_recall"),
        ("人物数量保留", "person_count_preservation"),
        ("人脸数量保留", "face_count_preservation"),
        ("商品数量保留", "product_count_preservation"),
        ("Logo数量保留", "logo_count_preservation"),
    ):
        if metrics.get(key) is not None:
            parts.append(f"{label} {_percent(metrics[key])}")
    if metrics.get("human_alignment_adjustments"):
        parts.append(f"软调整：{metrics['human_alignment_adjustments']}")
    if metrics.get("human_alignment_matched_gates"):
        parts.append(f"门禁：{metrics['human_alignment_matched_gates']}")
    if metrics.get("critical_regressions"):
        parts.append(f"关键退化：{metrics['critical_regressions']}")
    return "；".join(parts) + "。这是可回放代理指标，不是人工金标。"


def _review_summary(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    review = dict(payload.get("invocation", {}).get("review", {}))
    localized = localize_strict_review(review)
    dimensions = [str(value).rstrip("。") for value in localized["dimensions"].values()]
    reason = "；".join([str(localized["summary"]).rstrip("。"), *dimensions]) + "。"
    return (
        str(review.get("overall_grade") or ""),
        str(review.get("directly_usable")).lower(),
        str(review.get("confidence") or ""),
        reason,
    )


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _partition_paths(
    task_id: str,
    sources: Movie60V3Sources,
) -> tuple[str, Path, Path, Path]:
    choices = (
        (
            "development",
            sources.development_overview,
            sources.development_advisory,
            sources.development_visual_review,
        ),
        (
            "proxy_holdout",
            sources.holdout_overview,
            sources.holdout_advisory,
            sources.holdout_visual_review,
        ),
    )
    matches = [item for item in choices if (item[2] / "decisions" / f"{task_id}.json").is_file()]
    if len(matches) != 1:
        raise ValueError(f"{task_id}: expected exactly one v3.2.2 partition")
    return matches[0]


def _visual_reviews(visual_root: Path, task_id: str) -> dict[str, dict[str, Any]]:
    root = visual_root / "candidate-reviews" / task_id
    payloads = [_read_json(path) for path in sorted(root.glob("*.json"))]
    by_candidate = {str(payload["candidate_id"]): payload for payload in payloads}
    if len(by_candidate) != 3:
        raise ValueError(f"{task_id}: expected three high-resolution candidate reviews")
    return by_candidate


def _copy_visual_evidence(visual_root: Path, task_id: str, destination: Path) -> None:
    mappings = (
        ("candidate-reviews", "reviews"),
        ("candidate-sheets", "sheets/candidates"),
        ("pair-reviews", "pair-reviews"),
        ("pair-sheets", "sheets/pairs"),
    )
    for source_name, target_name in mappings:
        source = visual_root / source_name / task_id
        if not source.is_dir():
            raise FileNotFoundError(source)
        shutil.copytree(source, destination / target_name)


def _write_all60_index(output: Path, rows: list[dict[str, str]]) -> None:
    cards = []
    for row in rows:
        task_id = row["task_id"]
        grade = row["final_grade"]
        cards.append(
            f'<article data-grade="{grade}" data-scene="{row["scene_category"]}">'
            f'<a href="tasks/{task_id}/02_comparison.jpg"><img loading="lazy" '
            f'src="tasks/{task_id}/02_comparison.jpg" alt="{task_id}"></a>'
            f"<div><h2>{task_id}</h2><p><b>{grade}</b> · {row['final_method']} · "
            f'{row["evaluation_partition"]}</p><p><a href="tasks/{task_id}/00_source.jpg">原图</a> '
            f'· <a href="tasks/{task_id}/01_final.png">Rule Top1</a> · '
            f'<a href="tasks/{task_id}/README.md">证据说明</a></p></div></article>'
        )
    page = (
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movie60 v3 全部结果</title><style>
body{margin:0;background:#f4f5f7;color:#171a1f;font:18px/1.55 "Microsoft YaHei",sans-serif}
header{background:#111820;color:#fff;padding:30px 5vw}h1{font-size:34px;margin:0 0 8px}
nav{padding:16px 5vw;background:#fff;position:sticky;top:0;border-bottom:1px solid #ddd;z-index:2}
button{font:inherit;padding:9px 18px;margin-right:8px;background:#fff;border:1px solid #bbb;cursor:pointer}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(410px,1fr));gap:24px;padding:28px 5vw 60px}
article{background:#fff;border:1px solid #ddd}article img{display:block;width:100%}article div{padding:16px 20px}
h2{font-size:20px;word-break:break-all}a{color:#1769aa;text-decoration:none}.hidden{display:none}
</style></head><body><header><h1>Movie60 v3 · 60 张当前结果</h1>
<p>策略 movie60@3.2.2；Rule 主选，Agent 仅提供建议。机器等级不是人工金标。</p></header>
<nav><button data-filter="all">全部 60</button><button data-filter="A">A</button>
<button data-filter="B">B</button><button data-filter="C">C</button><button data-filter="D">D</button></nav>
<main>"""
        + "".join(cards)
        + """</main><script>
document.querySelectorAll('button').forEach(b=>b.onclick=()=>{const f=b.dataset.filter;
document.querySelectorAll('article').forEach(c=>c.classList.toggle('hidden',f!=='all'&&c.dataset.grade!==f));});
</script></body></html>"""
    )
    (output / "index.html").write_text(page, encoding="utf-8")


def _copy_runtime(repository: Path, output: Path) -> None:
    template = repository / "release_templates" / RELEASE_ID
    if not template.is_dir():
        raise FileNotFoundError(template)
    shutil.copytree(template, output, dirs_exist_ok=True)
    runtime_package = output / "_runtime" / "src" / "retarget_agent"
    runtime_package.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "models.py", "movie60_review_app.py"):
        _copy_file(repository / "src" / "retarget_agent" / name, runtime_package / name)
    shutil.copytree(
        repository / "src" / "retarget_agent" / "web_movie60",
        runtime_package / "web_movie60",
    )
    for batch in output.glob("*.bat"):
        text = batch.read_text(encoding="utf-8").replace("\r\n", "\n")
        batch.write_bytes(text.replace("\n", "\r\n").encode("ascii"))


def _legacy_index(output: Path) -> None:
    text = """# 历史版本索引

本目录只解释旧版本，不复制旧中间资产，也不作为当前启动入口。

| 版本 | 主要用途 | 已知问题 | 如何查看 |
|---|---|---|---|
| movie60-review-v1 | 第一份内部人工评审包 | 旧 Rule/Agent；人工记录较少 | 私有 GitHub Release 标签 `movie60-review-v1` |
| movie60-review-v2 | 60×7 候选、Focus20 AIGC 和早期 v3 证据 | `all60` 主表仍混有旧评分；启动脚本绑定外部仓库 | 私有 GitHub Release 标签 `movie60-review-v2`；本机原目录仍保留 |
| v3/v3.1/v3.2 研究证据 | Rule/Agent 策略迭代 | 同集代理校准，不能视作独立人工验证 | 代码仓 `docs/reviews/movie60-v3/` 与 Git 历史 |

当前唯一推荐入口是数据包根目录的 `START_HERE.html` 和 `START_REVIEW.bat`。
旧包没有被删除或覆盖；如需审计，通过 Release 标签或 Git Commit 查找。
"""
    path = output / "legacy" / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def materialize_movie60_review_v3(
    sources: Movie60V3Sources,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a new v3 workspace without mutating any source or older release."""

    sources = sources.resolved()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    required_dirs = tuple(Path(getattr(sources, field)) for field in sources.__dataclass_fields__)
    if not all(path.is_dir() for path in required_dirs):
        missing = [str(path) for path in required_dirs if not path.is_dir()]
        raise FileNotFoundError(f"missing Movie60 v3 source directories: {missing}")

    base_all60 = sources.base_workspace / "all60"
    old_summary = _read_csv(base_all60 / "summary.csv")
    old_candidates = _read_csv(base_all60 / "candidate-review.csv")
    if len(old_summary) != 60 or len(old_candidates) != 420:
        raise ValueError("base workspace must contain exactly 60 tasks and 420 candidates")
    old_candidate_map = {(row["task_id"], row["method"]): row for row in old_candidates}
    old_summary_map = {row["task_id"]: row for row in old_summary}
    task_ids = sorted(old_summary_map)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="movie60-v3-build-", dir=output_dir.parent) as temp:
        root = Path(temp) / RELEASE_ID
        all60 = root / "all60"
        (all60 / "tasks").mkdir(parents=True)
        _copy_runtime(sources.repository, root)
        _legacy_index(root)

        output_candidates: list[dict[str, str]] = []
        output_summary: list[dict[str, str]] = []
        partition_counts = {"development": 0, "proxy_holdout": 0}

        for task_id in task_ids:
            partition, overview_root, advisory_root, visual_root = _partition_paths(
                task_id, sources
            )
            partition_counts[partition] += 1
            decision = _read_json(advisory_root / "decisions" / f"{task_id}.json")
            overview = _read_json(overview_root / "decisions" / f"{task_id}.json")
            reviews = _visual_reviews(visual_root, task_id)
            ranking = [str(value) for value in decision["rule_complete_ranking"]]
            agent_ranking = [str(value) for value in overview["candidate_ranking"]]
            if len(ranking) != 7 or set(ranking) != set(agent_ranking):
                raise ValueError(f"{task_id}: incomplete or inconsistent rankings")

            source_task = base_all60 / "tasks" / task_id
            task_output = all60 / "tasks" / task_id
            candidate_output = task_output / "candidates"
            candidate_output.mkdir(parents=True)
            source_image = _source_image(source_task)
            with Image.open(source_image) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(
                    task_output / "00_source.jpg", quality=96, optimize=True
                )

            current_evidence = task_output / "evidence" / "current-v3.2.2"
            _write_json(current_evidence / "decision.json", decision)
            _write_json(current_evidence / "overview-decision.json", overview)
            _copy_visual_evidence(visual_root, task_id, current_evidence)

            rank_payload: list[dict[str, Any]] = []
            metrics_by_candidate: dict[str, dict[str, Any]] = {}
            for rank, candidate_id in enumerate(ranking, 1):
                method = _method_from_candidate(candidate_id)
                metric_payload = _read_json(sources.evaluation / "metrics" / f"{candidate_id}.json")
                metrics = dict(metric_payload["metrics"])
                metrics_by_candidate[candidate_id] = metrics
                candidate_source = sources.run / "candidates" / task_id / method / "candidate.png"
                candidate_target = candidate_output / f"{method}.png"
                _copy_file(candidate_source, candidate_target)
                _write_json(
                    current_evidence / "candidate-metrics" / f"{method}.json",
                    metric_payload,
                )
                rank_payload.append(
                    {
                        "rank": rank,
                        "candidate_id": candidate_id,
                        "method": method,
                        "quality": metrics["quality_score"],
                        "proxy_grade": metrics["proxy_grade"],
                    }
                )
            _write_json(current_evidence / "rule-ranking.json", {"ranking": rank_payload})

            for rank, candidate_id in enumerate(ranking, 1):
                method = _method_from_candidate(candidate_id)
                metrics = metrics_by_candidate[candidate_id]
                prior = old_candidate_map[(task_id, method)]
                review = reviews.get(candidate_id)
                if review is None:
                    agent_grade = agent_usable = agent_confidence = ""
                    agent_reason = (
                        f"Agent 总览排名 {agent_ranking.index(candidate_id) + 1}/7；"
                        "该候选未进入本轮高清 Rule Top1/双 Challenger 复核，不生成独立等级。"
                    )
                    agent_scope = "七候选总览"
                else:
                    agent_grade, agent_usable, agent_confidence, agent_reason = _review_summary(
                        review
                    )
                    agent_scope = "高清单候选复核"
                roles = []
                if candidate_id == decision["rule_top1_candidate_id"]:
                    roles.append("Rule Top1")
                if candidate_id == decision["agent_proposed_candidate_id"]:
                    roles.append("Agent建议Top1")
                if candidate_id in decision.get("agent_challenger_candidate_ids", []):
                    roles.append("Agent Challenger")
                if candidate_id == decision["selected_candidate_id"]:
                    roles.append("部署最终选择")
                output_candidates.append(
                    {
                        "task_id": task_id,
                        "phase": old_summary_map[task_id]["phase"],
                        "evaluation_partition": partition,
                        "scene_category": old_summary_map[task_id]["scene_category"],
                        "candidate_id": candidate_id,
                        "method": method,
                        "image_sha256": _sha256(candidate_output / f"{method}.png"),
                        "dataset_version": f"{DATASET_ID}@{DATASET_VERSION}",
                        "strategy_version": f"{STRATEGY_ID}@{STRATEGY_VERSION}",
                        "strategy_sha256": STRATEGY_SHA256,
                        "evaluation_id": EVALUATION_ID,
                        "label_source": LABEL_SOURCE,
                        "rule_rank": str(rank),
                        "rule_quality": f"{float(metrics['quality_score']):.12f}",
                        "rule_grade": str(metrics["proxy_grade"]).removeprefix("proxy_").upper(),
                        "rule_reason": _rule_reason(rank, metrics),
                        "rule_ocr_recall": ""
                        if metrics.get("ocr_character_recall") is None
                        else str(metrics["ocr_character_recall"]),
                        "rule_person_preservation": ""
                        if metrics.get("person_count_preservation") is None
                        else str(metrics["person_count_preservation"]),
                        "rule_face_preservation": ""
                        if metrics.get("face_count_preservation") is None
                        else str(metrics["face_count_preservation"]),
                        "rule_product_preservation": ""
                        if metrics.get("product_count_preservation") is None
                        else str(metrics["product_count_preservation"]),
                        "rule_logo_preservation": ""
                        if metrics.get("logo_count_preservation") is None
                        else str(metrics["logo_count_preservation"]),
                        "agent_rank": str(agent_ranking.index(candidate_id) + 1),
                        "agent_role": " + ".join(roles) if roles else "普通候选",
                        "agent_review_scope": agent_scope,
                        "agent_grade": agent_grade,
                        "agent_directly_usable": agent_usable,
                        "agent_confidence": agent_confidence,
                        "agent_reason": agent_reason,
                        "agent_reason_codes": ";".join(
                            str(value) for value in overview.get("reason_codes", [])
                        ),
                        "agent_reason_codes_zh": ";".join(
                            localize_reason_codes(
                                str(value) for value in overview.get("reason_codes", [])
                            )
                        ),
                        "final_selected": str(
                            candidate_id == decision["selected_candidate_id"]
                        ).lower(),
                        "model_advice_grade": prior.get("model_advice_grade", ""),
                        "model_advice_reason": prior.get("model_advice_reason", ""),
                        "model_advice_scope": prior.get("model_advice_scope", "待高清复核"),
                        "model_advice_source": prior.get("model_advice_source", ""),
                        "human_grade": prior.get("human_grade", ""),
                        "human_reason": prior.get("human_reason", ""),
                        "human_issue_codes": prior.get("human_issue_codes", ""),
                        "human_confirmed": prior.get("human_confirmed", "false"),
                        "reviewer_id": prior.get("reviewer_id", ""),
                        "updated_at": prior.get("updated_at", ""),
                    }
                )

            selected_id = str(decision["selected_candidate_id"])
            selected_method = _method_from_candidate(selected_id)
            selected_metrics = metrics_by_candidate[selected_id]
            _copy_file(
                candidate_output / f"{selected_method}.png",
                task_output / "01_final.png",
            )
            _comparison(
                task_output / "00_source.jpg",
                task_output / "01_final.png",
                task_output / "02_comparison.jpg",
                task_id=task_id,
                method=selected_method,
                grade=str(selected_metrics["proxy_grade"]).removeprefix("proxy_").upper(),
            )
            task_readme = f"""# {task_id}

- Dataset：`{DATASET_ID}@{DATASET_VERSION}`
- Strategy：`{STRATEGY_ID}@{STRATEGY_VERSION}`
- Evaluation：`{EVALUATION_ID}`
- 分区：`{partition}`
- 当前部署选择：`{selected_method}`（Rule Top1）

`candidates/` 保存七张完整候选。`evidence/current-v3.2.2/` 只保存当前 Rule、
Agent 总览、高清三候选复核、配对复核与逐候选指标。历史证据没有混入本任务目录。

机器等级是代理判断，不是独立人工真值；人工评分保存在 `../../candidate-review.csv`。
"""
            (task_output / "README.md").write_text(task_readme, encoding="utf-8")
            output_summary.append(
                {
                    "task_id": task_id,
                    "phase": old_summary_map[task_id]["phase"],
                    "evaluation_partition": partition,
                    "scene_category": old_summary_map[task_id]["scene_category"],
                    "final_method": selected_method,
                    "final_grade": str(selected_metrics["proxy_grade"])
                    .removeprefix("proxy_")
                    .upper(),
                    "final_quality": f"{float(selected_metrics['quality_score']):.12f}",
                    "passed_ab": str(
                        str(selected_metrics["proxy_grade"]) in {"proxy_a", "proxy_b"}
                    ),
                    "agent_method": _method_from_candidate(
                        str(decision["agent_proposed_candidate_id"])
                    ),
                    "agent_grade": str(decision.get("agent_grade") or ""),
                    "agent_overrode_rule": str(bool(decision["agent_overrode_rule"])),
                    "aigc_requested": str(bool(decision["request_external_aigc"])),
                    "strategy_version": f"{STRATEGY_ID}@{STRATEGY_VERSION}",
                    "evaluation_id": EVALUATION_ID,
                    "label_source": LABEL_SOURCE,
                    "wall_seconds": str(decision["task_review_wall_seconds"]),
                }
            )

        _write_csv(all60 / "candidate-review.csv", output_candidates)
        _write_csv(all60 / "summary.csv", output_summary)
        top1_rows = []
        candidate_map = {(row["task_id"], row["method"]): row for row in output_candidates}
        for row in output_summary:
            candidate = candidate_map[(row["task_id"], row["final_method"])]
            top1_rows.append(
                {
                    "task_id": row["task_id"],
                    "phase": row["phase"],
                    "scene_category": row["scene_category"],
                    "machine_method": row["final_method"],
                    "machine_grade": row["final_grade"],
                    "human_grade": candidate["human_grade"],
                    "human_reason": candidate["human_reason"],
                    "human_issue_codes": candidate["human_issue_codes"],
                    "human_confirmed": candidate["human_confirmed"],
                    "reviewer_id": candidate["reviewer_id"],
                    "updated_at": candidate["updated_at"],
                }
            )
        _write_csv(all60 / "review.csv", top1_rows)
        _write_all60_index(all60, output_summary)

        human_count = sum(row["human_grade"] in {"A", "B", "C", "D"} for row in output_candidates)
        version = {
            "schema_version": "1.0",
            "release_id": RELEASE_ID,
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "run_id": sources.run.name,
            "evaluation_id": EVALUATION_ID,
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "strategy_sha256": STRATEGY_SHA256,
            "deployment_route": "rule_primary_agent_advisory",
            "label_source": LABEL_SOURCE,
            "independent_human_validation": False,
            "task_count": 60,
            "candidate_count": 420,
            "human_reviewed_candidate_count": human_count,
            "partition_counts": partition_counts,
            "focus20_aigc_evidence_version": "movie60-focus20-aigc-v1-preserved",
        }
        _write_json(root / "VERSION.json", version)
        _write_json(all60 / "machine-summary.json", version)
        _write_json(
            all60 / "machine-report.json",
            {
                **version,
                "development_report": "documentation/movie60-v3/agent-v3-2-2-development/report.json",
                "proxy_holdout_report": "documentation/movie60-v3/agent-v3-2-2-proxy-holdout/report.json",
                "deployment_freeze": "documentation/movie60-v3/deployment-freeze.json",
            },
        )
        (all60 / "README.md").write_text(
            """# Movie60 v3 全部 60 张

这是当前唯一主结果表：60 个 Task、每个 Task 七张候选，共 420 张。

- `summary.csv`：v3.2.2 Rule 主选和 Agent 建议摘要；
- `candidate-review.csv`：v3.2.2 逐候选分数、Agent 证据、已有人工记录；
- `tasks/<task_id>/`：原图、七候选、当前 Top1 和当前版本证据；
- `index.html`：无需 Python 的只读浏览页。

旧评分没有混入本目录。机器建议不是独立人工金标。
""",
            encoding="utf-8",
        )

        for name in ("focus20", "dataset"):
            source = sources.base_workspace / name
            if not source.is_dir():
                raise FileNotFoundError(source)
            shutil.copytree(source, root / name)
        with (root / "focus20" / "README.md").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n\n> v3说明：这里保留的是已经实际产生的受控 AIGC 实验图，"
                "没有重新调用付费 API；其旧机器分仅作历史对照。\n"
            )

        documentation = root / "documentation"
        shutil.copytree(
            sources.repository / "docs" / "reviews" / "movie60-v3", documentation / "movie60-v3"
        )
        for name in (
            "DEVELOPER_OPERATION_MANUAL.md",
            "DEVELOPER_OPERATION_MANUAL_DETAILED.md",
            "DEVELOPER_ALGORITHM_PRINCIPLES.md",
            "DEVELOPER_ALGORITHM_REFERENCE.md",
            "REVIEW_GUIDE.md",
        ):
            _copy_file(sources.repository / "docs" / name, documentation / name)
        shutil.copytree(
            sources.repository / "strategies" / "movie60" / "v3_2_2",
            root / "strategy" / "movie60-v3.2.2",
        )
        shutil.move(str(root), output_dir)

    return validate_movie60_review_v3(output_dir)


def validate_movie60_review_v3(root: Path) -> dict[str, Any]:
    """Fail closed when a v3 workspace is ambiguous, incomplete, or stale."""

    root = root.resolve()
    version = _read_json(root / "VERSION.json")
    expected = {
        "release_id": RELEASE_ID,
        "dataset_version": DATASET_VERSION,
        "evaluation_id": EVALUATION_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_sha256": STRATEGY_SHA256,
        "task_count": 60,
        "candidate_count": 420,
    }
    for key, value in expected.items():
        if version.get(key) != value:
            raise ValueError(f"VERSION.json {key} mismatch: {version.get(key)!r} != {value!r}")
    summary = _read_csv(root / "all60" / "summary.csv")
    candidates = _read_csv(root / "all60" / "candidate-review.csv")
    if len(summary) != 60 or len({row["task_id"] for row in summary}) != 60:
        raise ValueError("v3 workspace must contain exactly 60 unique tasks")
    if (
        len(candidates) != 420
        or len({(row["task_id"], row["method"]) for row in candidates}) != 420
    ):
        raise ValueError("v3 workspace must contain exactly 420 unique candidates")
    for row in candidates:
        if row["strategy_version"] != f"{STRATEGY_ID}@{STRATEGY_VERSION}":
            raise ValueError("candidate-review.csv contains a non-current strategy row")
        if row["evaluation_id"] != EVALUATION_ID:
            raise ValueError("candidate-review.csv contains a non-current evaluation row")
    for row in summary:
        task_dir = root / "all60" / "tasks" / row["task_id"]
        candidates_dir = task_dir / "candidates"
        if {path.stem for path in candidates_dir.glob("*.png")} != set(METHODS):
            raise ValueError(f"{row['task_id']}: seven-candidate image set is incomplete")
        if not all(
            (task_dir / name).is_file()
            for name in ("00_source.jpg", "01_final.png", "02_comparison.jpg")
        ):
            raise ValueError(f"{row['task_id']}: source/final/comparison set is incomplete")
        evidence = task_dir / "evidence" / "current-v3.2.2"
        if not all(
            (evidence / name).is_file()
            for name in ("decision.json", "overview-decision.json", "rule-ranking.json")
        ):
            raise ValueError(f"{row['task_id']}: current evidence is incomplete")
    required_windows = (
        "START_HERE.html",
        "01_CONFIGURE_PIP_MIRROR_FIRST.md",
        "INSTALL_WINDOWS.bat",
        "START_REVIEW.bat",
        "STOP_REVIEW.bat",
        "OPEN_RESULTS.bat",
        "_runtime/install_windows.ps1",
        "_runtime/stop_review.ps1",
        "_runtime/run_review_ui.py",
        "_runtime/src/retarget_agent/movie60_review_app.py",
    )
    if not all((root / name).is_file() for name in required_windows):
        raise ValueError("v3 workspace is missing a Windows entry point")
    return {
        "status": "valid",
        "release_id": RELEASE_ID,
        "task_count": 60,
        "candidate_count": 420,
        "human_reviewed_candidate_count": sum(
            row["human_grade"] in {"A", "B", "C", "D"} for row in candidates
        ),
    }


__all__ = [
    "Movie60V3Sources",
    "materialize_movie60_review_v3",
    "validate_movie60_review_v3",
]
