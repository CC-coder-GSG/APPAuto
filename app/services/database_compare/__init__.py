"""Read-only SQLite comparison, independent of the web application."""

from .engine import compare_databases

__all__ = ["compare_databases"]
