"""Shared environment variable utilities."""
import os

_TRUTHY = frozenset({"true", "1", "yes", "on"})


def env_flag(name: str) -> bool:
    """Read boolean env var: only true/1/yes/on (case-insensitive) is truthy."""
    return os.getenv(name, "").strip().lower() in _TRUTHY
