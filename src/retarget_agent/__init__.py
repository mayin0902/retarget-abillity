"""retarget-agent public package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("retarget-engine")
except PackageNotFoundError:  # pragma: no cover - editable source without metadata
    __version__ = "0.4.0"

__all__ = ["__version__"]
