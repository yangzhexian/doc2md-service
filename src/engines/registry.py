"""Converter engine registry.

Engines self-register when their modules are imported. The service imports
all built-in engines at startup so `get_engine` can look them up by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .base import BaseConverterEngine

_REGISTRY: dict[str, Type["BaseConverterEngine"]] = {}


def register_engine(cls: Type["BaseConverterEngine"]) -> Type["BaseConverterEngine"]:
    """Decorator used by engine modules to register themselves."""
    if not cls.name:
        raise ValueError(f"Engine class {cls.__name__} must define a non-empty name")
    _REGISTRY[cls.name] = cls
    return cls


def get_engine(name: str) -> Type["BaseConverterEngine"] | None:
    return _REGISTRY.get(name)


def list_engines() -> list[str]:
    return list(_REGISTRY.keys())


def all_supported_extensions() -> frozenset[str]:
    """Union of every registered engine's supported extensions."""
    result: set[str] = set()
    for cls in _REGISTRY.values():
        result |= cls.supported_extensions
    return frozenset(result)


# Import built-in engines so they register themselves.
def _import_builtins() -> None:
    from . import markitdown, mineru  # noqa: F401


_import_builtins()
