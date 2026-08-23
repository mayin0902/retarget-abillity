from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirement_lines(path: Path) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_company_model_extra_matches_authoritative_windows_pins() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = tuple(project["project"]["optional-dependencies"]["company-models"])
    authoritative = _requirement_lines(
        PROJECT_ROOT / "requirements" / "company-models-windows.txt"
    )

    assert all("==" in requirement for requirement in authoritative)
    assert sorted(extra, key=str.casefold) == sorted(authoritative, key=str.casefold)
