"""Backends package for Clash of Clans bot."""

from __future__ import annotations

from typing import Any

from backends.base import Backend
from backends.pc_backend import PCBackend
from backends.adb_backend import ADBBackend
from backends.mock_backend import MockBackend
from backends.dry_run import DryRunBackend

__all__ = ["Backend", "PCBackend", "ADBBackend", "MockBackend", "DryRunBackend", "create_backend"]


def create_backend(name: str, config: dict[str, Any]) -> Backend:
    """Create and return a configured backend instance.

    Parameters
    ----------
    name : str
        Backend identifier: 'pc', 'adb', 'mock', 'dry_run'.
    config : dict[str, Any]
        Top-level bot configuration dictionary.

    Returns
    -------
    Backend
        Configured Backend instance.

    Raises
    ------
    ValueError
        If backend name is unsupported.
    """
    backend_key = name.strip().lower()

    if backend_key == "pc":
        return PCBackend(config)

    elif backend_key == "adb":
        return ADBBackend(config)

    elif backend_key == "mock":
        return MockBackend(config)

    elif backend_key == "dry_run":
        # Check if underlying backend is specified
        target_name = config.get("backends", {}).get("dry_run", {}).get("target_backend", "pc")
        if target_name == "dry_run":
            target_name = "pc"
        try:
            base_backend = create_backend(target_name, config)
        except Exception:
            base_backend = MockBackend(config)
        return DryRunBackend(config=config, wrapped=base_backend)

    else:
        raise ValueError(
            f"Unknown backend '{name}'. Available backends: 'pc', 'adb', 'mock', 'dry_run'"
        )
