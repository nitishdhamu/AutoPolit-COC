"""CLI tool to capture tagged screenshot samples for OCR and detector tests."""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
import time
from pathlib import Path

import cv2

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backends import create_backend
from core.config_loader import ConfigLoader

logger = logging.getLogger("capture_samples")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture tagged screenshot samples from PC or ADB device"
    )
    parser.add_argument(
        "--backend",
        choices=["pc", "adb", "mock"],
        default=None,
        help="Backend to use (default: active_backend from config.yaml)",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="sample",
        help="Tag / label prefix for captured screenshots (e.g., 'home', 'scid', 'attack')",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of screenshots to capture (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds to wait between consecutive captures (default: 1.0)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="tests/samples",
        help="Target directory to save captured screenshots (default: 'tests/samples')",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to config file (default: 'config.yaml')",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()

    cfg_path = PROJECT_ROOT / args.config
    try:
        cfg = ConfigLoader(cfg_path).config
    except Exception as e:
        logger.warning("Could not load config from %s (%s); using default empty config", cfg_path, e)
        cfg = {}

    backend_name = args.backend or cfg.get("active_backend", "pc")
    logger.info("Initializing backend: %s", backend_name)

    try:
        backend = create_backend(backend_name, cfg)
    except Exception as e:
        logger.error("Failed to create backend '%s': %s", backend_name, e)
        return 1

    out_dir = PROJECT_ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Saving screenshots to: %s", out_dir)

    try:
        if not backend.connect():
            logger.error("Failed to connect backend '%s'", backend_name)
            return 1

        for i in range(args.count):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{args.tag}_{timestamp}_{i+1:02d}.png"
            filepath = out_dir / filename

            try:
                frame = backend.get_screenshot()
            except Exception as e:
                logger.error("Error capturing screenshot #%d: %s", i + 1, e)
                continue

            h, w = frame.shape[:2]
            cv2.imwrite(str(filepath), frame)
            logger.info(
                "Captured [%d/%d]: %s (%dx%d px)",
                i + 1, args.count, filepath.name, w, h
            )

            if i < args.count - 1 and args.interval > 0:
                time.sleep(args.interval)

    finally:
        backend.disconnect()
        logger.info("Backend disconnected")

    return 0


if __name__ == "__main__":
    sys.exit(main())
