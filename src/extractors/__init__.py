"""Source-browser extractor registry."""

from __future__ import annotations

from typing import Type

from .arc import ArcExtractor
from .base import (
    BrowserExtractor,
    BrowserExtractorError,
    ExportData,
    FolderRecord,
    SpaceRecord,
    TabRecord,
)
from .brave import BraveExtractor
from .chrome import ChromeExtractor
from .edge import EdgeExtractor
from .firefox import FirefoxExtractor
from .safari import SafariExtractor

# Order matters: this is the order the source picker presents them.
EXTRACTORS: tuple[Type[BrowserExtractor], ...] = (
    ArcExtractor,
    ChromeExtractor,
    EdgeExtractor,
    BraveExtractor,
    FirefoxExtractor,
    SafariExtractor,
)


def by_name(name: str) -> Type[BrowserExtractor]:
    for cls in EXTRACTORS:
        if cls.name == name:
            return cls
    raise KeyError(f"unknown source browser: {name!r}")


__all__ = [
    "ArcExtractor",
    "BraveExtractor",
    "BrowserExtractor",
    "BrowserExtractorError",
    "ChromeExtractor",
    "EXTRACTORS",
    "EdgeExtractor",
    "ExportData",
    "FirefoxExtractor",
    "FolderRecord",
    "SafariExtractor",
    "SpaceRecord",
    "TabRecord",
    "by_name",
]
