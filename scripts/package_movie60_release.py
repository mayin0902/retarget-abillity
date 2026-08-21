from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

METHODS = {
    "crop",
    "direct_warp",
    "mesh",
    "mesh_full",
    "seam",
    "seam_full",
    "seam_scale",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_RELEASE_ASSET_BYTES = 2 * 1024**3
PACKAGE_ROOT = Path("movie60-review")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Entry:
    source: Path
    archive_path: Path
    group: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_path(relative: Path) -> Path:
    return PACKAGE_ROOT / relative


def _add_file(entries: list[Entry], source: Path, relative: Path, group: str) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    entries.append(Entry(source=source, archive_path=_archive_path(relative), group=group))


def _add_tree(
    entries: list[Entry], source_root: Path, relative_root: Path, group: str
) -> None:
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        _add_file(entries, source, relative_root / source.relative_to(source_root), group)


def _task_sources(task_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in task_dir.glob("00_source.*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _collect(
    workspace: Path,
    repository: Path,
    agent_evidence: tuple[Path, ...] = (),
) -> tuple[list[Entry], list[Entry]]:
    all60 = workspace / "all60"
    focus20 = workspace / "focus20"
    tasks_root = all60 / "tasks"
    task_dirs = sorted(path for path in tasks_root.iterdir() if path.is_dir())
    if len(task_dirs) != 60:
        raise ValueError(f"expected 60 task directories, found {len(task_dirs)}")

    core: list[Entry] = []
    evidence: list[Entry] = []

    for name in ("README.md", "V2_PLAN.md", "start-review.bat"):
        _add_file(core, workspace / name, Path(name), "workspace")
    _add_tree(core, workspace / "rules-v1", Path("rules-v1"), "rules")
    _add_tree(core, repository / "strategies", Path("strategies"), "strategy-bundles")
    v3_reviews = repository / "docs" / "reviews" / "movie60-v3"
    if v3_reviews.is_dir():
        _add_tree(core, v3_reviews, Path("documentation/movie60-v3"), "strategy-evidence")

    for name in (
        "README.md",
        "summary.csv",
        "candidate-review.csv",
        "review.csv",
        "machine-summary.json",
        "machine-report.json",
    ):
        _add_file(core, all60 / name, Path("all60") / name, "review")
    for name in (
        "README.md",
        "review.csv",
        "codex.csv",
        "guide.md",
        "AIGC.md",
        "aigc-status.csv",
    ):
        _add_file(core, focus20 / name, Path("focus20") / name, "review")
    reports = workspace / "reports"
    if reports.is_dir():
        _add_tree(core, reports, Path("reports"), "human-review-reports")

    documentation = {
        repository / "docs" / "DATA_AND_RESULTS.md": Path("documentation/DATA_AND_RESULTS.md"),
        repository / "docs" / "REVIEW_GUIDE.md": Path("documentation/REVIEW_GUIDE.md"),
        repository / "docs" / "HANDOFF_DETAILED_GUIDE.md": Path(
            "documentation/HANDOFF_DETAILED_GUIDE.md"
        ),
        repository / "docs" / "PLUGIN_STRATEGY_GUIDE.md": Path(
            "documentation/PLUGIN_STRATEGY_GUIDE.md"
        ),
        repository / "docs" / "MOVIE60_V3_RULE_AGENT_GUIDE.md": Path(
            "documentation/MOVIE60_V3_RULE_AGENT_GUIDE.md"
        ),
        repository / "docs" / "reports" / "MOVIE60_STRICT_END_TO_END_REPORT.md": Path(
            "documentation/MOVIE60_TECHNICAL_REPORT.md"
        ),
        repository / "datasets" / "movie_visual_60_v1" / "README.md": Path(
            "documentation/DATASET_CONTRACT.md"
        ),
        repository / "configs" / "movie_visual60_square_v1.yaml": Path(
            "documentation/RUN_CONFIG.yaml"
        ),
    }
    for source, relative in documentation.items():
        _add_file(core, source, relative, "documentation")

    local_dataset = repository / "local_data" / "datasets" / "movie_visual_60_v1"
    if not local_dataset.is_dir():
        local_dataset = workspace / "dataset"
    for name in (
        "dataset.yaml",
        "materialization_summary.json",
        "provenance.csv",
        "seedream_egress_authorization.csv",
        "sources.csv",
        "targets.csv",
        "tasks.csv",
    ):
        _add_file(core, local_dataset / name, Path("dataset") / name, "dataset_contract")

    for task_dir in task_dirs:
        task_relative = Path("all60/tasks") / task_dir.name
        sources = _task_sources(task_dir)
        if len(sources) != 1:
            raise ValueError(
                f"{task_dir.name}: expected one root source image, found {len(sources)}"
            )
        _add_file(core, sources[0], task_relative / sources[0].name, "source")
        for name in ("README.md", "01_final.png"):
            _add_file(core, task_dir / name, task_relative / name, "result")

        candidates = sorted((task_dir / "candidates").glob("*.png"))
        if {path.stem for path in candidates} != METHODS:
            raise ValueError(f"{task_dir.name}: candidate method set is incomplete")
        for candidate in candidates:
            _add_file(
                core,
                candidate,
                task_relative / "candidates" / candidate.name,
                "candidate",
            )

        machine = task_dir / "evidence" / "machine"
        for source in sorted(path for path in machine.rglob("*") if path.is_file()):
            relative = task_relative / "evidence" / "machine" / source.relative_to(machine)
            if source.suffix.lower() in IMAGE_SUFFIXES:
                _add_file(evidence, source, relative, "machine_visual")
            else:
                _add_file(core, source, relative, "machine_reason")

        route = task_dir / "evidence" / "route"
        if route.is_dir():
            for source in sorted(path for path in route.rglob("*") if path.is_file()):
                relative = task_relative / "evidence" / "route" / source.relative_to(route)
                if source.suffix.lower() in IMAGE_SUFFIXES:
                    _add_file(evidence, source, relative, "aigc_visual")
                else:
                    _add_file(core, source, relative, "aigc_reason")

        _add_file(
            evidence,
            task_dir / "02_comparison.jpg",
            task_relative / "02_comparison.jpg",
            "comparison",
        )

    _add_tree(evidence, workspace / "showcase", Path("showcase"), "showcase")
    _add_tree(evidence, focus20 / "tasks", Path("focus20/tasks"), "focus20")

    for evidence_root in agent_evidence:
        evidence_root = evidence_root.resolve()
        if not evidence_root.is_dir():
            raise FileNotFoundError(evidence_root)
        relative_root = Path("v3-agent-evidence") / evidence_root.name
        for source in sorted(path for path in evidence_root.rglob("*") if path.is_file()):
            relative = relative_root / source.relative_to(evidence_root)
            if source.suffix.lower() in IMAGE_SUFFIXES:
                _add_file(evidence, source, relative, "v3-agent-visual")
            else:
                _add_file(core, source, relative, "v3-agent-record")

    core_paths = {entry.archive_path.as_posix() for entry in core}
    evidence_paths = {entry.archive_path.as_posix() for entry in evidence}
    if len(core_paths) != len(core):
        raise ValueError("duplicate core archive paths")
    if len(evidence_paths) != len(evidence):
        raise ValueError("duplicate evidence archive paths")
    return core, evidence


def _review_progress(workspace: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    specs = {
        "all60_candidates": (workspace / "all60" / "candidate-review.csv", "human_grade"),
        "all60_top1": (workspace / "all60" / "review.csv", "human_grade"),
        "focus20_routes": (workspace / "focus20" / "review.csv", "human_rule_grade"),
    }
    for key, (path, grade_column) in specs.items():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        completed = sum(bool((row.get(grade_column) or "").strip()) for row in rows)
        result[key] = {"total": len(rows), "completed": completed}
    return result


def _generated_files(workspace: Path, release_version: str) -> dict[Path, bytes]:
    progress = _review_progress(workspace)
    readme = f"""# Movie60 内部交接数据包 {release_version}

本包用于私有仓库协作。它包含60份原图、420份七方法候选、机器排名与原因、高清中间证据、
AIGC成功/失败记录以及人工评审表。商业素材仅限内部评测，不得公开再分发。

将 core 与 evidence 两个ZIP解压到同一父目录，得到同一个 `movie60-review/`：

- 打开 `showcase/index.html` 快速浏览代表案例；
- 打开 `all60/tasks/<task_id>/` 查看原图、七候选和机器证据；
- 在代码仓库中运行 `start-review.bat` 完成人工评分。

当前人工进度：

- 逐候选：{progress['all60_candidates']['completed']}/{progress['all60_candidates']['total']}；
- Top1兼容表：{progress['all60_top1']['completed']}/{progress['all60_top1']['total']}；
- 重点20路线：{progress['focus20_routes']['completed']}/{progress['focus20_routes']['total']}。

未填写行不是失败，也不是机器标签；它们表示仍待项目人员评审。
"""
    feedback = """# 已确认人工反馈

以下只记录用户已明确表达的结论，不扩写为完整人工金标准：

- `still_003__square-1536`：AIGC结果人工明确为A、可直接使用；旧机器C属于误杀。
- `video_cover_015__square-1536`：用户明确认为Crop和AIGC均通过；精确A/B等级仍应在UI填写。

其他候选必须继续在UI逐张评分，不能把辅助模型建议冒充人工结果。
"""
    suggestions = """# 下一版规则更新建议

1. 目标比例、合理缩放、位置变化和重新排版本身不扣分；只看是否自然、完整、可上传。
2. Warp拉伸必须结合人物、圆形、文字和结构的实际可见形变，不能只看内容是否齐全。
3. Seam/Mesh增加局部形变证据，重点检查脸、身体、文字笔画、商品轮廓和结构线。
4. OCR/YOLO/Logo计数是辅助证据；与高清完整图冲突时记录检测误差并交给人工。
5. AIGC必须在候选自身重新检测，不能把原图坐标直接映射到语义重构图。
6. Rule输出完整排名，Rule Top1强制高清；Agent challenger有明确证据才允许覆盖。
7. Calibration20用于迭代；冻结后Validation40只运行一次。
8. 人工同级不要求机器同分，只统计同级分差中位数/P90和全同级任务range。
"""
    access = """# 访问与再分发边界

- 素材来源为本地内部研究集；本包没有声明开放许可证。
- 本次上传仅限私有GitHub仓库的协作者访问。
- 不得将Release改为公开，也不得转存公开数据集或第三方API，除非逐图获得相应授权。
- API出域授权只适用于已经执行并记录的受控实验，不自动扩展到新的调用。
"""
    progress_json = json.dumps(progress, ensure_ascii=False, indent=2) + "\n"
    return {
        PACKAGE_ROOT / "PACKAGE_README.md": readme.encode("utf-8"),
        PACKAGE_ROOT / "KNOWN_HUMAN_FEEDBACK.md": feedback.encode("utf-8"),
        PACKAGE_ROOT / "NEXT_RULE_UPDATES.md": suggestions.encode("utf-8"),
        PACKAGE_ROOT / "ACCESS_BOUNDARY.md": access.encode("utf-8"),
        PACKAGE_ROOT / "review-progress.json": progress_json.encode("utf-8"),
    }


def _manifest(entries: list[Entry]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("archive_path", "group", "bytes", "sha256"))
    for entry in sorted(entries, key=lambda item: item.archive_path.as_posix()):
        writer.writerow(
            (
                entry.archive_path.as_posix(),
                entry.group,
                entry.source.stat().st_size,
                _sha256(entry.source),
            )
        )
    return output.getvalue().encode("utf-8-sig")


def _write_zip(
    path: Path,
    entries: list[Entry],
    generated: dict[Path, bytes],
    manifest_name: str,
    *,
    package_root: Path = PACKAGE_ROOT,
) -> None:
    if path.exists():
        raise FileExistsError(path)
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        for entry in entries:
            compress = (
                zipfile.ZIP_STORED
                if entry.source.suffix.lower() in IMAGE_SUFFIXES
                else zipfile.ZIP_DEFLATED
            )
            info = _zip_info(entry.archive_path, compress)
            with entry.source.open("rb") as source, archive.open(
                info,
                "w",
                force_zip64=True,
            ) as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        for archive_path, payload in generated.items():
            archive.writestr(_zip_info(archive_path, zipfile.ZIP_DEFLATED), payload)
        archive.writestr(
            _zip_info(package_root / manifest_name, zipfile.ZIP_DEFLATED),
            _manifest(entries),
        )
    if path.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ValueError(f"release asset exceeds GitHub 2 GiB limit: {path}")


def _zip_info(path: Path, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path.as_posix(), date_time=ZIP_TIMESTAMP)
    info.compress_type = compress_type
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def package(
    repository: Path,
    output_dir: Path,
    *,
    release_version: str = "v1",
    workspace: Path | None = None,
    agent_evidence: tuple[Path, ...] = (),
) -> list[Path]:
    repository = repository.resolve()
    workspace = (
        workspace.resolve()
        if workspace is not None
        else repository / "deliverables" / "movie60-review"
    )
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    core, evidence = _collect(workspace, repository, agent_evidence)
    if not release_version.startswith("v") or not release_version[1:].isdigit():
        raise ValueError("release_version must look like v1 or v2")
    generated = _generated_files(workspace, release_version)
    core_zip = output_dir / f"movie60-handoff-{release_version}-core.zip"
    evidence_zip = output_dir / f"movie60-handoff-{release_version}-evidence.zip"
    _write_zip(core_zip, core, generated, "core-manifest.csv")
    _write_zip(evidence_zip, evidence, {}, "evidence-manifest.csv")

    assets = [core_zip, evidence_zip]
    sums = "".join(f"{_sha256(path)}  {path.name}\n" for path in assets)
    sums_path = output_dir / "SHA256SUMS.txt"
    sums_path.write_text(sums, encoding="ascii")
    assets.append(sums_path)
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package the single curated Movie60 handoff as two GitHub Release assets."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "Materialized movie60-review root; defaults to "
            "repository/deliverables/movie60-review."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("local_data/release_assets/movie60-handoff-v1"),
    )
    parser.add_argument("--release-version", default="v1")
    parser.add_argument(
        "--agent-evidence",
        type=Path,
        action="append",
        default=[],
        help="Completed Agent/strict-review directory to include; may be repeated.",
    )
    args = parser.parse_args()
    assets = package(
        args.repository,
        args.output_dir,
        release_version=args.release_version,
        workspace=args.workspace,
        agent_evidence=tuple(args.agent_evidence),
    )
    for path in assets:
        print(f"{path.name}\t{path.stat().st_size}\t{_sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
