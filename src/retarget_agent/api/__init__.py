"""Stable in-memory interfaces for embedding Retarget Engine capabilities."""

from ..analysis import ProtectionAnalyzerCore
from .retarget import RetargetResult, generate_candidates, retarget_image
from .scoring import PairScoreResult, score_pair

__all__ = [
    "PairScoreResult",
    "ProtectionAnalyzerCore",
    "RetargetResult",
    "generate_candidates",
    "retarget_image",
    "score_pair",
]
