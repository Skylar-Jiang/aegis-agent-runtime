"""Experiment API — delegates to experiments_provider for V2 schema support."""

from .experiments_provider import router  # noqa: F401 — re-export for existing main.py registration
