from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = PROJECT_ROOT / "scripts" / "bootstrap_windows.ps1"


def _write_fake_pip(package_root: Path) -> None:
    pip_package = package_root / "pip"
    pip_package.mkdir(parents=True)
    (pip_package / "__init__.py").write_text("", encoding="utf-8")
    (pip_package / "__main__.py").write_text(
        """
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


arguments = sys.argv[1:]
if arguments[:2] == ["config", "list"]:
    print(":env:.index-url='https://mirror-one.example.test/repository/pypi/simple'")
    raise SystemExit(0)

log_path = Path(os.environ["FAKE_PIP_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")

mode = os.environ["FAKE_PIP_MODE"]
if mode == "success":
    print("Successfully installed demo")
    raise SystemExit(0)

if mode == "no-match":
    print("ERROR: Could not find a version that satisfies the requirement demo==1")
    print("ERROR: No matching distribution found for demo==1")
    raise SystemExit(1)

if mode == "ssl-then-success":
    trusted_hosts = [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == "--trusted-host"
    ]
    if "mirror-one.example.test" in trusted_hosts:
        print("Successfully installed demo after trusted-host retry")
        raise SystemExit(0)
    print("Looking in indexes: https://mirror-one.example.test/repository/pypi/simple")
    print(
        "Could not fetch URL https://mirror-one.example.test/repository/pypi/simple/demo/: "
        "SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] "
        "self-signed certificate in certificate chain"
    )
    raise SystemExit(1)

raise SystemExit(f"Unknown FAKE_PIP_MODE: {mode}")
""".lstrip(),
        encoding="utf-8",
    )


def _invoke_pip_function(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    source = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8-sig")
    assert "function Invoke-PipInstall" in source, (
        "bootstrap_windows.ps1 must expose one Invoke-PipInstall function for all pip installs"
    )

    fake_packages = tmp_path / "fake-packages"
    _write_fake_pip(fake_packages)
    log_path = tmp_path / "pip-invocations.jsonl"
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PIP_LOG": str(log_path),
            "FAKE_PIP_MODE": mode,
            "PIP_EXTRA_INDEX_URL": (
                "https://mirror-two.example.test/repository/pypi/simple"
            ),
            "PIP_FIND_LINKS": (
                r"D:\Company Project\retarget-ability\local_data\bootstrap-wheels"
            ),
            "PIP_INDEX_URL": "https://mirror-one.example.test/repository/pypi/simple",
            "PYTHONPATH": str(fake_packages),
        }
    )
    command = (
        f". '{BOOTSTRAP_SCRIPT}'; "
        f"Invoke-PipInstall -Executable '{Path(sys.executable)}' "
        "-InstallArguments @('demo==1') -Label 'Test pip installation'"
    )
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _read_invocations(log_path: Path) -> list[list[str]]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows Bootstrap contract")
def test_pip_install_retries_ssl_failure_with_detected_trusted_host(tmp_path: Path) -> None:
    result = _invoke_pip_function(tmp_path, "ssl-then-success")

    assert result.returncode == 0, result.stdout + result.stderr
    invocations = _read_invocations(tmp_path / "pip-invocations.jsonl")
    assert len(invocations) == 2
    assert invocations[0] == ["install", "demo==1"]
    assert "--trusted-host" in invocations[1]
    trusted_hosts = [
        invocations[1][index + 1]
        for index, value in enumerate(invocations[1][:-1])
        if value == "--trusted-host"
    ]
    assert trusted_hosts == [
        "mirror-one.example.test",
        "mirror-two.example.test",
    ]
    assert "d" not in trusted_hosts
    assert "TLS certificate verification is disabled" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows Bootstrap contract")
def test_pip_install_does_not_retry_non_ssl_resolution_failure(tmp_path: Path) -> None:
    result = _invoke_pip_function(tmp_path, "no-match")

    assert result.returncode != 0
    invocations = _read_invocations(tmp_path / "pip-invocations.jsonl")
    assert len(invocations) == 1
    assert "No matching distribution" in result.stdout
    assert "Test pip installation failed with exit code 1" in result.stderr


def test_all_bootstrap_pip_installs_use_the_ssl_aware_wrapper() -> None:
    source = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8-sig")

    assert source.count("Invoke-PipInstall $Python") == 3
    assert "Invoke-Checked $Python @('-m', 'pip', 'install'" not in source
    assert "pip==25.2" in source
