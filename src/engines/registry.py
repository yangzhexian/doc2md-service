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


def engine_for_extension(file_path: str) -> Type["BaseConverterEngine"] | None:
    """Return the first registered engine that supports the file extension."""
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    for cls in _REGISTRY.values():
        if suffix in cls.supported_extensions:
            return cls
    return None


# Import built-in engines so they register themselves.
def _import_builtins() -> None:
    from . import markitdown, mineru  # noqa: F401


_import_builtins()
