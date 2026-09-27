from __future__ import annotations

import typing

from .extension import SnippetManager

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def setup(api: "ExtensionsAPI") -> SnippetManager:
    """Entrypoint for Biscuit to load the extension."""
    ext = SnippetManager(api)
    api.register("snippet_manager", ext)
    return ext
