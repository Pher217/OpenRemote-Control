"""Shared HTML utilities for ORC apps.

Plain-Python utility — no Django imports, no models.
"""


def _esc(s: str) -> str:
    """Minimal HTML escape: &, <, > → named entities."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
