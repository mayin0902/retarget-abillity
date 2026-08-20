# ruff: noqa: E501
from __future__ import annotations

import csv
import html
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_FOLDERS = REPO_ROOT / "deliverables" / "_archive" / "folders"
SOURCE = ARCHIVE_FOLDERS / "movie60-rule-anchored-v6-final-20260819"
ROUTE_SOURCE = (
    ARCHIVE_FOLDERS
    / "movie60-strict-aigc-20260819-final"
    / "route-comparisons"
    / "tasks"
)
CURRENT_DRAFT = ARCHIVE_FOLDERS / "movie60-review-draft-20260819" / "02-review-20"
OUTPUT = REPO_ROOT / "deliverables" / "movie60-review-next"
ALL60 = OUTPUT / "all60"
FOCUS20 = OUTPUT / "focus20"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "#f1f2f4")
    x = (size[0] - fitted.width) // 2
    y = (size[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return canvas


def _final_image(task_dir: Path, row: dict[str, str]) -> Path:
    method = row["final_method"]
    preferred = (
        task_dir / f"02_rule_top1_{method}.png"
        if row["agent_overrode_rule"].lower() == "false"
        else task_dir / f"03_qwen_challenger_{method}.png"
    )
    if preferred.exists():
        return preferred
    matches = list(task_dir.glob(f"0[23]_*_{method}.png"))
    if len(matches) != 1:
        raise RuntimeError(f"Cannot resolve final image for {row['task_id']}: {matches}")
    return matches[0]


def _source_image(task_dir: Path) -> Path:
    matches = [path for path in task_dir.glob("00_source.*") if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one source image in {task_dir}, got {matches}")
    return matches[0]


def _comparison(
    source_path: Path,
    final_path: Path,
    output_path: Path,
    row: dict[str, str],
) -> None:
    width, height = 1920, 1080
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(38)
    label_font = _font(31)
    meta_font = _font(25)
    grade_color = {"A": "#16834a", "B": "#2468b4", "C": "#cf7b00", "D": "#c73535"}.get(
        row["final_grade"], "#333333"
    )

    draw.rectangle((0, 0, width, 100), fill="#111820")
    draw.text((42, 25), row["task_id"], font=title_font, fill="white")
    meta = f"{row['scene_category']} · {row['phase']} · 最终方法 {row['final_method']}"
    draw.text((width - 42, 34), meta, font=meta_font, fill="#d9dde2", anchor="ra")

    panel_size = (900, 850)
    with Image.open(source_path) as source:
        canvas.paste(_fit(source, panel_size), (30, 160))
    with Image.open(final_path) as final:
        canvas.paste(_fit(final, panel_size), (990, 160))

    draw.text((30, 115), "原图", font=label_font, fill="#222222")
    draw.text((990, 115), "最终选择", font=label_font, fill="#222222")
    draw.rounded_rectangle((1690, 112, 1888, 158), radius=12, fill=grade_color)
    draw.text(
        (1789, 134),
        f"机器等级 {row['final_grade']}",
        font=meta_font,
        fill="white",
        anchor="mm",
    )
    draw.text(
        (30, 1035),
        "提示：该等级是冻结机器预审，不是人工金标；请按原尺寸查看后再评分。",
        font=meta_font,
        fill="#525a64",
        anchor="lm",
    )
    canvas.save(output_path, quality=90, optimize=True)


def _write_task_readme(task_output: Path, row: dict[str, str]) -> None:
    route_note = (
        "- `evidence/route/`：该任务的 Rule / Agent / AIGC 专项对比与旧评分。\n"
        if (ROUTE_SOURCE / row["task_id"]).exists()
        else ""
    )
    text = f"""# {row['task_id']}

- 场景：`{row['scene_category']}`
- Split：`{row['phase']}`
- 最终方法：`{row['final_method']}`
- 冻结机器等级：`{row['final_grade']}`
- A/B 通过：`{row['passed_ab']}`
- 请求 AIGC：`{row['aigc_requested']}`

先看 `02_comparison.jpg`，需要确认细节时分别打开 `00_source.jpg` 与 `01_final.png`。

机器等级不是人工金标。

- `evidence/machine/`：七候选总览、Rule 排名、Qwen 决策、高清局部复核和原始 JSON。
{route_note}
"""
    (task_output / "README.md").write_text(text, encoding="utf-8")


def _write_html(rows: list[dict[str, str]]) -> None:
    cards = []
    for row in rows:
        task_id = html.escape(row["task_id"])
        cards.append(
            f"""
            <article class="card" data-grade="{html.escape(row['final_grade'])}" data-scene="{html.escape(row['scene_category'])}">
              <a href="tasks/{task_id}/02_comparison.jpg"><img loading="lazy" src="tasks/{task_id}/02_comparison.jpg" alt="{task_id}"></a>
              <div class="body">
                <h2>{task_id}</h2>
                <p><span class="grade grade-{html.escape(row['final_grade'])}">{html.escape(row['final_grade'])}</span>
                {html.escape(row['scene_category'])} · {html.escape(row['final_method'])} · {html.escape(row['phase'])}</p>
                <p><a href="tasks/{task_id}/00_source.jpg">原图</a> · <a href="tasks/{task_id}/01_final.png">最终图</a> · <a href="tasks/{task_id}/README.md">说明与证据</a></p>
              </div>
            </article>"""
        )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movie60 最终结果</title>
<style>
body{{margin:0;background:#f4f5f7;color:#171a1f;font:18px/1.55 "Microsoft YaHei",sans-serif}}header{{background:#111820;color:white;padding:32px 5vw}}h1{{margin:0 0 8px;font-size:36px}}header p{{margin:0;color:#d9dde2}}nav{{padding:18px 5vw;background:white;position:sticky;top:0;z-index:2;border-bottom:1px solid #ddd}}button{{font:inherit;padding:9px 18px;margin-right:8px;border:1px solid #b9bec5;background:white;border-radius:4px;cursor:pointer}}button:hover{{border-color:#c7000b;color:#c7000b}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px;padding:28px 5vw 60px}}.card{{background:white;border:1px solid #e0e2e5;box-shadow:0 3px 14px #0000000b}}.card img{{width:100%;display:block}}.body{{padding:18px 20px}}h2{{font-size:20px;margin:0 0 10px;word-break:break-all}}a{{color:#1769aa;text-decoration:none}}.grade{{display:inline-block;color:white;font-weight:700;min-width:30px;text-align:center;margin-right:8px}}.grade-A{{background:#16834a}}.grade-B{{background:#2468b4}}.grade-C{{background:#cf7b00}}.grade-D{{background:#c73535}}.hidden{{display:none}}
</style></head><body><header><h1>Movie60 最终结果</h1><p>60张原图与冻结机器最终选择。机器等级不是人工金标。</p></header>
<nav><button data-filter="all">全部 60</button><button data-filter="A">A</button><button data-filter="B">B</button><button data-filter="C">C</button></nav>
<main>{''.join(cards)}</main>
<script>document.querySelectorAll('button').forEach(b=>b.onclick=()=>{{const f=b.dataset.filter;document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hidden',f!=='all'&&c.dataset.grade!==f));}});</script>
</body></html>"""
    (ALL60 / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"Refusing to overwrite existing output: {OUTPUT}")
    ALL60.mkdir(parents=True)
    (ALL60 / "tasks").mkdir()

    with (SOURCE / "all-task-results.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 60 or len({row["task_id"] for row in rows}) != 60:
        raise RuntimeError("Expected exactly 60 unique tasks")

    summary_fields = [
        "task_id",
        "phase",
        "scene_category",
        "final_method",
        "final_grade",
        "passed_ab",
        "agent_overrode_rule",
        "aigc_requested",
        "wall_seconds",
    ]
    with (ALL60 / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows({field: row[field] for field in summary_fields} for row in rows)

    review_fields = [
        "task_id",
        "phase",
        "scene_category",
        "machine_method",
        "machine_grade",
        "human_grade",
        "human_reason",
        "human_confirmed",
    ]
    with (ALL60 / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "task_id": row["task_id"],
                    "phase": row["phase"],
                    "scene_category": row["scene_category"],
                    "machine_method": row["final_method"],
                    "machine_grade": row["final_grade"],
                    "human_grade": "",
                    "human_reason": "",
                    "human_confirmed": "false",
                }
            )

    for row in rows:
        source_task = SOURCE / "tasks" / row["task_id"]
        task_output = ALL60 / "tasks" / row["task_id"]
        task_output.mkdir()
        source_image = _source_image(source_task)
        final_image = _final_image(source_task, row)
        if source_image.suffix.lower() in {".jpg", ".jpeg"}:
            shutil.copy2(source_image, task_output / "00_source.jpg")
        else:
            with Image.open(source_image) as source:
                ImageOps.exif_transpose(source).convert("RGB").save(
                    task_output / "00_source.jpg", quality=96, optimize=True
                )
        shutil.copy2(final_image, task_output / "01_final.png")
        _comparison(source_image, final_image, task_output / "02_comparison.jpg", row)
        machine_evidence = task_output / "evidence" / "machine"
        shutil.copytree(source_task, machine_evidence)
        route_source = ROUTE_SOURCE / row["task_id"]
        if route_source.exists():
            shutil.copytree(route_source, task_output / "evidence" / "route")
        _write_task_readme(task_output, row)

    shutil.copy2(SOURCE / "all-task-results.json", ALL60 / "machine-summary.json")
    shutil.copy2(SOURCE / "report.json", ALL60 / "machine-report.json")
    _write_html(rows)
    (ALL60 / "README.md").write_text(
        """# Movie60 最终结果（60张）

这是当前最简的 60 张结果浏览目录。

1. 双击 `index.html`，可以按 A/B/C 筛选并浏览全部原图与最终图。
2. `tasks/<task_id>/02_comparison.jpg` 是原图与最终选择的并排大图。
3. `00_source.jpg` 与 `01_final.png` 用于原尺寸复核。
4. 同一个任务目录的 `evidence/` 保存候选、Rule 排名、Qwen 判断、高清复核、JSON；证据不与图片分家。
5. `summary.csv` 是 60 张机器结果摘要，`review.csv` 是完整 60 张人工评分表。

这里的等级来自冻结机器预审，不是人工金标。请优先查看60张结果，再填写人工表。
""",
        encoding="utf-8",
    )

    shutil.copytree(ROUTE_SOURCE, FOCUS20 / "tasks")
    shutil.copy2(CURRENT_DRAFT / "codex.csv", FOCUS20 / "codex.csv")
    shutil.copy2(CURRENT_DRAFT / "review.csv", FOCUS20 / "review.csv")
    shutil.copy2(CURRENT_DRAFT / "guide.md", FOCUS20 / "guide.md")
    (FOCUS20 / "README.md").write_text(
        """# Movie60 重点复评（20张）

这里是从60张中选出的20张困难路由/AIGC样本，与完整60张明确分开。

- `tasks/<task_id>/`：原图、Rule、Agent、AIGC（若回图）、拼图和旧评分JSON。
- `review.csv`：你当前要填写的20张人工复评表。
- `codex.csv`：Codex逐图建议，不是人工金标。
- `guide.md`：20张详细理由和建议规则改进。

先看每个任务的 `collage.jpg`，有争议时打开单张高清图片。无AIGC回图的任务视觉等级记N/A。
""",
        encoding="utf-8",
    )
    (OUTPUT / "README.md").write_text(
        """# Movie60 当前工作区

当前只需要使用本目录：

- `all60/`：完整60张最终结果。双击 `all60/index.html` 浏览；每张图片和完整机器证据放在同一任务目录。
- `focus20/`：20张困难样本专项复评，含Rule、Agent、AIGC和人工表。

`deliverables/_archive/` 只保存旧实验包与ZIP，不是当前工作入口。
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
