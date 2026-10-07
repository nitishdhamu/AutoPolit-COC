"""Runtime readiness checks for live bot sessions.

A real unattended session must not start until the OCR executable
is verified to be installed and available.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_live_runtime(config: dict, project_root: Path) -> list[str]:
    """Return all reasons a live session run would be unsafe."""
    errors: list[str] = []
    ocr_cfg = config.get("ocr", {})
    executable = Path(
        ocr_cfg.get("tesseract_cmd", ocr_cfg.get("tesseract_path", "tesseract"))
    )
    if not executable.exists():
        errors.append(f"Tesseract executable not found: {executable}")

    return errors
