"""Built-in deterministic candidate methods."""

from __future__ import annotations

from retarget_agent.protocols import CandidateMethod
from retarget_agent.registry import Registry

from .crop import ProtectionCropMethod
from .direct_warp import DirectWarpMethod
from .mesh import FullMeshMethod
from .mesh_legacy import AxisAlignedMeshMethod
from .seam import FullSeamMethod, SeamScaleMethod
from .seam_limited import LimitedSeamMethod


def built_in_methods() -> Registry[CandidateMethod]:
    registry: Registry[CandidateMethod] = Registry("method")
    for method in (
        DirectWarpMethod(),
        ProtectionCropMethod(),
        FullSeamMethod(),
        FullMeshMethod(),
        SeamScaleMethod(),
        LimitedSeamMethod(),
        AxisAlignedMeshMethod(),
    ):
        registry.register(method.method_id, method)
    return registry


__all__ = [
    "AxisAlignedMeshMethod",
    "DirectWarpMethod",
    "FullMeshMethod",
    "FullSeamMethod",
    "LimitedSeamMethod",
    "ProtectionCropMethod",
    "SeamScaleMethod",
    "built_in_methods",
]
