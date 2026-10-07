"""Declarative UI element definitions and safe multi-method detection.

The bot has one 1920x1080 design coordinate system.  A UI element can be
located by a stable relative coordinate, an OpenCV template, or visible text.
The resolver returns physical screen coordinates and records which method was
used, so game actions do not each invent their own fallback logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from bot.ocr import GameOCR
    from typing import Any
    from bot.vision import VisionEngine

logger = logging.getLogger(__name__)

DetectionMethod = Literal["fixed", "template", "ocr"]
DESIGN_WIDTH = 1920
DESIGN_HEIGHT = 1080


@dataclass(frozen=True)
class UIElement:
    """A known control and the safe ways it may be located."""

    name: str
    fixed_pos: tuple[int, int] | None = None
    template: str | None = None
    ocr_text: tuple[str, ...] = ()
    region: tuple[int, int, int, int] | None = None
    methods: tuple[DetectionMethod, ...] = ("template", "ocr", "fixed")
    threshold: float = 0.72


# Coordinates retain the pre-existing 1920x1080 values.  They are only used
# when the caller has already established the expected screen state.
UI_ELEMENTS: dict[str, UIElement] = {
    "attack_btn": UIElement("attack_btn", (92, 1005), "attack_btn", ("attack",), (0, 880, 220, 200), ("template", "ocr", "fixed")),
    "settings_gear": UIElement("settings_gear", (1862, 831), "settings_gear", (), (1780, 750, 140, 180), ("template", "fixed")),
    "next_btn": UIElement("next_btn", (1790, 990), "next_btn", ("next",), (1640, 900, 280, 180), ("template", "ocr", "fixed")),
    "return_home_btn": UIElement("return_home_btn", (960, 850), "return_home_btn", ("return home",), None, ("template", "ocr", "fixed")),
    "end_battle_btn": UIElement("end_battle_btn", (100, 650), "end_battle_btn", ("end battle",), (0, 560, 230, 180), ("template", "ocr", "fixed")),
    "bb_find_now_btn": UIElement("bb_find_now_btn", (960, 550), "bb_find_now_btn", ("find now", "battle"), None, ("template", "ocr", "fixed")),
    "scid_open_btn": UIElement("scid_open_btn", (1100, 450), "scid_open_btn", ("open",), None, ("template", "ocr", "fixed")),
    "switch_id_btn": UIElement("switch_id_btn", (960, 700), "switch_id_btn", ("switch",), None, ("template", "ocr", "fixed")),
    "reload_game_btn": UIElement("reload_game_btn", None, "reload_game_btn", ("reload", "ok"), None, ("template", "ocr")),
}


class UIResolver:
    """Resolve declared elements without issuing clicks.

    The caller remains responsible for checking the expected screen state and
    performing the click.  This separation keeps failed detection fail-closed.
    """

    def __init__(
        self,
        screen: ScreenCapture,
        vision: VisionEngine,
        ocr: GameOCR,
        config: dict,
    ) -> None:
        detection = config.get("detection", {})
        self._screen = screen
        self._vision = vision
        self._ocr = ocr
        self._log_method = bool(detection.get("log_detection_method", True))

    def find(
        self, element_name: str, screenshot: np.ndarray | None = None
    ) -> tuple[int, int] | None:
        """Return the element centre in physical screen coordinates.

        A missing or unrecognised element is not guessed.  The only coordinate
        fallback is an element-specific, viewport-scaled design coordinate.
        """
        element = UI_ELEMENTS.get(element_name)
        if element is None:
            raise KeyError(f"Unknown UI element: {element_name}")
        shot = screenshot

        for method in element.methods:
            if method == "template" and element.template:
                shot = shot if shot is not None else self._screen.capture_screenshot()
                match = self._vision.find(shot, element.template, element.threshold)
                if match is not None:
                    x, y, w, h = match
                    return self._found(element, method, (x + w // 2, y + h // 2))
            elif method == "ocr" and element.ocr_text:
                shot = shot if shot is not None else self._screen.capture_screenshot()
                for text in element.ocr_text:
                    match = self._ocr.find_text_location(text, shot, element.region)
                    if match is not None:
                        x, y, w, h = match
                        return self._found(element, method, (x + w // 2, y + h // 2))
            elif method == "fixed" and element.fixed_pos is not None:
                return self._found(element, method, self._scale_design_point(element.fixed_pos))

        logger.warning("Unable to locate UI element '%s'", element.name)
        return None

    def _scale_design_point(self, point: tuple[int, int]) -> tuple[int, int]:
        """Map a 1920x1080 point into the currently visible game viewport."""
        viewport = self._screen.get_game_viewport()
        if viewport is None:
            # A full-screen capture is still safer than hard-coding desktop
            # pixels; scale against the configured capture dimensions.
            left = top = 0
            width, height = self._screen.width, self._screen.height
        else:
            left, top, width, height = viewport
        return (
            left + round(point[0] * width / DESIGN_WIDTH),
            top + round(point[1] * height / DESIGN_HEIGHT),
        )

    def _found(
        self, element: UIElement, method: DetectionMethod, point: tuple[int, int]
    ) -> tuple[int, int]:
        if self._log_method:
            logger.debug("UI element '%s' found via %s at %s", element.name, method, point)
        return point
