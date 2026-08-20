"""Small explicit plugin registry with duplicate protection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, plugin_id: str, plugin: T) -> None:
        if plugin_id in self._items:
            raise ValueError(f"duplicate {self.kind} plugin id: {plugin_id}")
        self._items[plugin_id] = plugin

    def get(self, plugin_id: str) -> T:
        try:
            return self._items[plugin_id]
        except KeyError as error:
            available = ", ".join(sorted(self._items)) or "<none>"
            message = f"unknown {self.kind} plugin {plugin_id!r}; available: {available}"
            raise KeyError(message) from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def values(self) -> Iterable[T]:
        return self._items.values()
