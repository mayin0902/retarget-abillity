from __future__ import annotations

from pathlib import Path

import pytest

from retarget_agent.config import AnalysisConfig
from retarget_agent.protection_detectors import ProtectionDetectorSuite, _load_cn_charset


def test_required_detector_suite_lists_missing_assets(tmp_path: Path) -> None:
    config = AnalysisConfig(detector_mode="required", model_root=str(tmp_path))
    with pytest.raises(FileNotFoundError, match="materialize_analyzer_models"):
        ProtectionDetectorSuite(config)


def test_charset_loader_uses_ast_without_executing_source(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    helper = tmp_path / "crnn.py"
    helper.write_text(
        "class CRNN:\n"
        "    CHARSET_CN_3944 = '''" + "\n".join("字" for _ in range(3001)) + "'''\n"
        f"open({str(marker)!r}, 'w').write('executed')\n",
        encoding="utf-8",
    )
    charset = _load_cn_charset(helper)
    assert len(charset) == 3001
    assert not marker.exists()
