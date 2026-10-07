"""Multi-signal screen detector for Clash of Clans.

Detects active game screens using a combination of:
1. Declarative OCR text signatures in normalized regions.
2. Dominant landmark color profiles (HSV masks, brightness, grass/terrain).
3. Frame stability checks.
Completely independent of OpenCV template image files and machine learning models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

from backends.coordinates import box_to_device_px
from bot.ocr import GameOCR

logger = logging.getLogger(__name__)


@dataclass
class ScreenDetectionResult:
    """Outcome of a screen detection attempt."""
    screen_name: str
    confidence: float
    matched_signals: List[str] = field(default_factory=list)
    is_stable: bool = True

    def __bool__(self) -> bool:
        return self.screen_name != "unknown" and self.confidence >= 0.5


class ScreenDetector:
    """Multi-signal screen classifier with declarative confidence scoring."""

    def __init__(self, config: dict[str, Any], ocr: Optional[GameOCR] = None) -> None:
        self.config = config
        self.ocr = ocr or GameOCR(config)
        self._last_screenshot: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # Frame Stability
    # ------------------------------------------------------------------

    def check_frame_stability(
        self,
        frame1: np.ndarray,
        frame2: np.ndarray,
        diff_threshold: float = 0.05,
    ) -> bool:
        """Return True if frame1 and frame2 are visually stable (not animating/transitioning)."""
        if frame1 is None or frame2 is None:
            return True
        if frame1.shape != frame2.shape:
            return False

        small1 = cv2.resize(cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY), (160, 90))
        small2 = cv2.resize(cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY), (160, 90))

        diff = cv2.absdiff(small1, small2)
        mean_diff = float(np.mean(diff)) / 255.0
        return mean_diff <= diff_threshold

    # ------------------------------------------------------------------
    # Landmark Color Analyzers
    # ------------------------------------------------------------------

    @staticmethod
    def is_mostly_black(image: np.ndarray, threshold_mean_v: float = 25.0) -> bool:
        """Check if screen is predominantly black (e.g. Supercell splash)."""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        return float(np.mean(hsv[:, :, 2])) < threshold_mean_v

    @staticmethod
    def has_loading_bar(image: np.ndarray) -> bool:
        """Check for magenta/purple horizontal loading progress bar in lower viewport."""
        h, w = image.shape[:2]
        # Look in y: [0.80, 0.96], x: [0.20, 0.80]
        lower_hsv = cv2.cvtColor(
            image[int(h * 0.80) : int(h * 0.96), int(w * 0.20) : int(w * 0.80)],
            cv2.COLOR_BGR2HSV,
        )
        if lower_hsv.size == 0:
            return 0
        p_mask = cv2.inRange(lower_hsv, (140, 110, 90), (168, 255, 255))
        cnts, _ = cv2.findContours(p_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return any(
            cv2.boundingRect(c)[2] >= 70 and 8 <= cv2.boundingRect(c)[3] <= 60
            for c in cnts
        )

    @staticmethod
    def get_green_grass_percentage(image: np.ndarray) -> float:
        """Measure percentage of green grass pixels in the central village area."""
        h, w = image.shape[:2]
        crop = image[int(h * 0.20) : int(h * 0.80), int(w * 0.20) : int(w * 0.80)]
        if crop.size == 0:
            return 0.0
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (35, 40, 40), (85, 255, 255))
        return float(np.count_nonzero(mask)) / float(mask.size) * 100.0

    # ------------------------------------------------------------------
    # Main Classifier
    # ------------------------------------------------------------------

    def detect_screen(
        self,
        screenshot: np.ndarray,
        prev_screenshot: Optional[np.ndarray] = None,
    ) -> ScreenDetectionResult:
        """Classify active game screen with confidence score and matched signals."""
        if screenshot is None or screenshot.size == 0:
            return ScreenDetectionResult("unknown", 0.0)

        is_stable = True
        if prev_screenshot is not None:
            is_stable = self.check_frame_stability(prev_screenshot, screenshot)

        h, w = screenshot.shape[:2]

        # 1. Supercell Splash / Black screen
        if self.is_mostly_black(screenshot):
            return ScreenDetectionResult(
                "supercell_splash",
                0.95,
                ["mean_brightness_black"],
                is_stable,
            )

        # 2. Loading Screen (magenta loading bar)
        if self.has_loading_bar(screenshot):
            return ScreenDetectionResult(
                "loading",
                0.90,
                ["loading_bar_geometry"],
                is_stable,
            )

        # 3. Center Dialog Checks (Gem dialog, Disconnect/Reload)
        center_crop = screenshot[int(h * 0.25) : int(h * 0.75), int(w * 0.25) : int(w * 0.75)]
        center_gray = cv2.cvtColor(center_crop, cv2.COLOR_BGR2GRAY)
        center_text = self.ocr.read_text(center_gray).lower()
        if not center_text:
            # Fallback to sparse OCR if line reading was empty
            try:
                import pytesseract
                center_text = pytesseract.image_to_string(center_gray).lower()
            except Exception:
                pass

        # Check Gem Purchase Dialog
        for phrase in ("buy the missing", "resources with gems", "not enough resources", "use gems"):
            if phrase in center_text:
                return ScreenDetectionResult(
                    "gem_dialog", 0.99, [f"gem_phrase_{phrase}"], is_stable
                )

        # Check Disconnect / Reload
        for phrase in ("connection lost", "reload game", "anyone there", "disconnected", "try again", "out of sync"):
            if phrase in center_text:
                return ScreenDetectionResult(
                    "disconnected", 0.95, [f"disconnect_phrase_{phrase}"], is_stable
                )

        # Check Battle Results
        for phrase in ("total damage", "damage:", "troops expended", "return home", "stars earned", "victory", "defeat"):
            if phrase in center_text:
                return ScreenDetectionResult(
                    "battle_results", 0.92, [f"results_phrase_{phrase}"], is_stable
                )

        # 4. Supercell ID Account Menu (Upper/Center dialog)
        upper_center = screenshot[int(h * 0.05) : int(h * 0.50), int(w * 0.15) : int(w * 0.85)]
        try:
            import pytesseract
            scid_text = pytesseract.image_to_string(cv2.cvtColor(upper_center, cv2.COLOR_BGR2GRAY)).lower()
        except Exception:
            scid_text = ""

        if any(k in scid_text for k in ("switch id", "supercell", "log in with", "switch account")):
            return ScreenDetectionResult("supercell_id", 0.95, ["text_supercell_id"], is_stable)

        # 5. Army Training Screen
        if any(k in scid_text for k in ("train troops", "brew spells", "quick train", "army")):
            return ScreenDetectionResult("army", 0.90, ["text_army_menu"], is_stable)

        # 6. Active Battle (Ends in, Available Loot, Surrender, End Battle)
        top_crop = screenshot[0 : int(h * 0.20), 0 : int(w * 0.60)]
        try:
            import pytesseract
            top_text = pytesseract.image_to_string(cv2.cvtColor(top_crop, cv2.COLOR_BGR2GRAY)).lower()
        except Exception:
            top_text = ""

        if any(k in top_text for k in ("battle ends in", "ends in:", "available loot", "loot:")):
            return ScreenDetectionResult("battle", 0.95, ["battle_hud_text"], is_stable)

        # Bottom-left check for Battle End / Surrender
        bl_crop = screenshot[int(h * 0.70) : h, 0 : int(w * 0.25)]
        try:
            import pytesseract
            bl_text = pytesseract.image_to_string(cv2.cvtColor(bl_crop, cv2.COLOR_BGR2GRAY)).lower()
        except Exception:
            bl_text = ""

        if any(k in bl_text for k in ("end battle", "surrender", "nd batti")):
            return ScreenDetectionResult("battle", 0.90, ["text_end_battle_or_surrender"], is_stable)

        # 7. Scout Screen ('Next' button bottom right)
        br_crop = screenshot[int(h * 0.70) : h, int(w * 0.75) : w]
        try:
            import pytesseract
            br_text = pytesseract.image_to_string(cv2.cvtColor(br_crop, cv2.COLOR_BGR2GRAY)).lower()
        except Exception:
            br_text = ""

        if "next" in br_text or "find a match" in br_text:
            return ScreenDetectionResult("attack_scout", 0.90, ["text_scout_next"], is_stable)

        # 8. Village classification (Home Village vs Builder Base)
        # Measured green grass percentage in central area
        grass_pct = self.get_green_grass_percentage(screenshot)

        # Home Village has distinct green grass (>= 2.0%)
        if grass_pct >= 2.0:
            return ScreenDetectionResult(
                "home", 0.85, [f"day_grass_pct={grass_pct:.1f}%"], is_stable
            )

        # Builder Base has nocturnal / slate terrain (< 2.0% green grass)
        return ScreenDetectionResult(
            "builder_base", 0.80, [f"nocturnal_terrain_grass_pct={grass_pct:.1f}%"], is_stable
        )

    def detect_current_screen(self, screenshot: np.ndarray) -> str:
        """Convenience method returning the screen name string."""
        return self.detect_screen(screenshot).screen_name

    # ------------------------------------------------------------------
    # Specialized Boolean Queries
    # ------------------------------------------------------------------

    def is_home_screen(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "home"

    def is_builder_base(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "builder_base"

    def is_battle_active(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "battle"

    def is_battle_results(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "battle_results"

    def is_supercell_id(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "supercell_id"

    def is_loading_or_splash(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name in ("loading", "supercell_splash")

    def is_disconnected(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "disconnected"

    def is_gem_dialog(self, screenshot: np.ndarray) -> bool:
        return self.detect_screen(screenshot).screen_name == "gem_dialog"
