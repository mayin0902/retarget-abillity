"""External image-generation interface and built-in adapters."""

from retarget_agent.providers.base import (
    AIGCGenerationRequest,
    AIGCProvider,
    AIGCProviderError,
    AIGCProviderFactory,
    AIGCProviderResult,
    AIGCProviderRuntime,
)
from retarget_agent.providers.seedream import (
    SeedDreamAIGCAdapter,
    SeedDreamErrorCode,
    SeedDreamGenerationRequest,
    SeedDreamGenerationResult,
    SeedDreamProvider,
    SeedDreamProviderConfig,
    SeedDreamProviderError,
    create_seedream_aigc_adapter,
)

__all__ = [
    "AIGCGenerationRequest",
    "AIGCProvider",
    "AIGCProviderError",
    "AIGCProviderFactory",
    "AIGCProviderResult",
    "AIGCProviderRuntime",
    "SeedDreamAIGCAdapter",
    "SeedDreamErrorCode",
    "SeedDreamGenerationRequest",
    "SeedDreamGenerationResult",
    "SeedDreamProvider",
    "SeedDreamProviderConfig",
    "SeedDreamProviderError",
    "create_seedream_aigc_adapter",
]
