"""Shared scene identifiers used by public interfaces and workflow adapters."""

from __future__ import annotations

SCENE_CATEGORIES = frozenset(
    {
        "film_still",
        "movie_poster",
        "person",
        "product",
        "unspecified",
        "video_cover",
    }
)


def normalize_scene(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in SCENE_CATEGORIES:
        raise ValueError(
            "scene must be one of: " + ", ".join(sorted(SCENE_CATEGORIES))
        )
    return normalized


__all__ = ["SCENE_CATEGORIES", "normalize_scene"]
