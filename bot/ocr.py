"""
Tesseract OCR engine specialised for Clash of Clans game text.

Handles three distinct use-cases:

* **Numbers** — resource amounts like ``1,420,350`` or ``1 420 350``.
* **Timers** — upgrade/boost countdowns like ``5d 12h``, ``3h 45m``, ``12m 30s``.
* **General text** — building names, labels, button text.

Each reader applies tailored preprocessing (grayscale → resize → threshold →
optional denoise) and Tesseract whitelists to maximise accuracy on the
stylised CoC fonts.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Optional

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger(__name__)


class GameOCR:
    """Tesseract-based OCR tuned for Clash of Clans UI text.

    Parameters
    ----------
    config : dict
        Bot configuration dict.  Reads settings from the ``ocr`` and
        ``ocr.preprocessing`` sections.
    """

    def __init__(self, config: dict) -> None:
        """Configure Tesseract path and preprocessing pipeline."""
        ocr_cfg = config.get("ocr", {})

        # Tesseract executable path
        tess_cmd: str = ocr_cfg.get(
            "tesseract_cmd",
            ocr_cfg.get(
                "tesseract_path", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            ),
        )
        pytesseract.pytesseract.tesseract_cmd = tess_cmd

        # Whitelists
        self._digit_whitelist: str = ocr_cfg.get("digit_whitelist", "0123456789")
        self._timer_whitelist: str = ocr_cfg.get("timer_whitelist", "0123456789dhms ")

        # Preprocessing settings
        prep = ocr_cfg.get("preprocessing", {})
        self._scale_factor: int = int(prep.get("scale_factor", 2))
        self._threshold_value: int = int(prep.get("threshold_value", 150))
        self._denoise: bool = bool(prep.get("denoise", True))

        logger.info(
            "GameOCR initialised — tesseract=%s, scale=%dx, threshold=%d, denoise=%s",
            tess_cmd,
            self._scale_factor,
            self._threshold_value,
            self._denoise,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_number(self, image: np.ndarray) -> Optional[int]:
        """Read an integer from a cropped image region.

        Designed for resource counters (gold, elixir, dark elixir).
        Handles common OCR artefacts: spaces, commas, periods used as
        thousand separators.

        Parameters
        ----------
        image : np.ndarray
            Cropped BGR or grayscale image of the number region.

        Returns
        -------
        int or None
            Parsed integer, or ``None`` if OCR produced no usable digits.
        """
        try:
            processed = self._preprocess(image)
            custom_config = (
                f"--oem 0 --psm 7 "
                f"-c tessedit_char_whitelist={self._digit_whitelist}"
            )
            raw_text: str = pytesseract.image_to_string(
                processed, config=custom_config
            ).strip()

            if not raw_text:
                logger.debug("read_number: empty OCR result")
                return None

            # Strip all non-digit characters (spaces, commas, periods, etc.)
            digits = re.sub(r"[^\d]", "", raw_text)

            if not digits:
                logger.debug("read_number: no digits in '%s'", raw_text)
                return None

            value = int(digits)
            logger.debug("read_number: '%s' → %d", raw_text, value)
            return value

        except Exception as e:
            logger.error("read_number failed: %s", e)
            return None

    def read_timer(self, image: np.ndarray) -> Optional[timedelta]:
        """Read a countdown timer and parse it into a :class:`timedelta`.

        Supports formats: ``5d 12h``, ``3h 45m``, ``12m 30s``, ``1d 14h``,
        ``45s``, ``2d``, etc.

        Parameters
        ----------
        image : np.ndarray
            Cropped BGR or grayscale image of the timer text.

        Returns
        -------
        timedelta or None
            Parsed duration, or ``None`` if the text cannot be interpreted.
        """
        try:
            processed = self._preprocess(image)
            custom_config = (
                f"--oem 0 --psm 7 "
                f"-c tessedit_char_whitelist={self._timer_whitelist}"
            )
            raw_text: str = pytesseract.image_to_string(
                processed, config=custom_config
            ).strip().lower()

            if not raw_text:
                logger.debug("read_timer: empty OCR result")
                return None

            return self._parse_timer(raw_text)

        except Exception as e:
            logger.error("read_timer failed: %s", e)
            return None

    def read_text(self, image: np.ndarray) -> str:
        """General-purpose text reading for building names and labels.

        Parameters
        ----------
        image : np.ndarray
            Cropped BGR or grayscale image containing text.

        Returns
        -------
        str
            Recognised text (may be empty on failure).
        """
        try:
            processed = self._preprocess(image)
            custom_config = "--oem 3 --psm 7"
            raw_text: str = pytesseract.image_to_string(
                processed, config=custom_config
            ).strip()
            logger.debug("read_text: '%s'", raw_text)
            return raw_text

        except Exception as e:
            logger.error("read_text failed: %s", e)
            return ""

    def find_text_location(
        self,
        text: str,
        image: np.ndarray,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int] | None:
        """Locate visible *text* and return its bounding box.

        This uses Tesseract word-position output rather than the numeric OCR
        preprocessing pipeline.  It accepts a multi-word query and compares
        case-insensitively after normalising punctuation and whitespace.
        Coordinates are returned in the original full-image coordinate space.
        """
        if not text.strip():
            return None
        try:
            offset_x = offset_y = 0
            source = image
            if region is not None:
                x, y, w, h = region
                if w <= 0 or h <= 0:
                    raise ValueError(f"Invalid OCR region: {region}")
                source = image[y:y + h, x:x + w]
                offset_x, offset_y = x, y
            if source.size == 0:
                return None

            gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY) if len(source.shape) == 3 else source
            data = pytesseract.image_to_data(
                gray,
                config="--oem 3 --psm 11",
                output_type=pytesseract.Output.DICT,
            )
            query = self._normalise_text(text)
            words: list[tuple[str, int, int, int, int]] = []
            for i, raw in enumerate(data["text"]):
                normalised = self._normalise_text(raw)
                if normalised:
                    words.append((normalised, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))

            joined = " ".join(word[0] for word in words)
            if query not in joined:
                return None
            query_words = query.split()
            for start in range(len(words) - len(query_words) + 1):
                candidate = " ".join(word[0] for word in words[start:start + len(query_words)])
                if candidate == query:
                    selected = words[start:start + len(query_words)]
                    left = min(word[1] for word in selected) + offset_x
                    top = min(word[2] for word in selected) + offset_y
                    right = max(word[1] + word[3] for word in selected) + offset_x
                    bottom = max(word[2] + word[4] for word in selected) + offset_y
                    return left, top, right - left, bottom - top
            return None
        except Exception as exc:
            logger.warning("find_text_location(%r) failed: %s", text, exc)
            return None

    # ------------------------------------------------------------------
    # Preprocessing pipeline
    # ------------------------------------------------------------------

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Standard preprocessing pipeline for game text.

        1. Convert to grayscale (if not already).
        2. Resize by ``scale_factor`` using ``INTER_CUBIC`` interpolation.
        3. Binary threshold (inverted) to produce white-on-black text.
        4. Optional fastNlMeansDenoising.

        Parameters
        ----------
        image : np.ndarray
            Input image (BGR or grayscale).

        Returns
        -------
        np.ndarray
            Preprocessed grayscale image ready for Tesseract.
        """
        # 1. Grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        # 2. Upscale for small text
        if self._scale_factor > 1:
            gray = cv2.resize(
                gray,
                None,
                fx=self._scale_factor,
                fy=self._scale_factor,
                interpolation=cv2.INTER_CUBIC,
            )

        # 3. Binary threshold
        _, binary = cv2.threshold(
            gray,
            self._threshold_value,
            255,
            cv2.THRESH_BINARY,
        )

        # 4. Denoise
        if self._denoise:
            try:
                binary = cv2.fastNlMeansDenoising(binary, None, 10, 7, 21)
            except Exception as e:
                logger.debug("Denoise step skipped: %s", e)

        return binary

    # ------------------------------------------------------------------
    # Timer parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_text(text: str) -> str:
        """Return a case-insensitive OCR-comparison form of *text*."""
        return " ".join(re.findall(r"[a-z0-9]+", text.lower()))

    @staticmethod
    def _parse_timer(text: str) -> Optional[timedelta]:
        """Parse a CoC-style timer string into a :class:`timedelta`.

        Handles patterns like ``5d 12h``, ``3h 45m``, ``12m 30s``, ``1d``.

        Parameters
        ----------
        text : str
            Lowercase timer text, e.g. ``"5d 12h"``.

        Returns
        -------
        timedelta or None
            Parsed duration, or ``None`` if no time components found.
        """
        days = hours = minutes = seconds = 0

        day_match = re.search(r"(\d+)\s*d", text)
        hour_match = re.search(r"(\d+)\s*h", text)
        min_match = re.search(r"(\d+)\s*m", text)
        sec_match = re.search(r"(\d+)\s*s", text)

        if day_match:
            days = int(day_match.group(1))
        if hour_match:
            hours = int(hour_match.group(1))
        if min_match:
            minutes = int(min_match.group(1))
        if sec_match:
            seconds = int(sec_match.group(1))

        if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
            logger.debug("_parse_timer: no time components in '%s'", text)
            return None

        td = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        logger.debug("_parse_timer: '%s' → %s", text, td)
        return td
