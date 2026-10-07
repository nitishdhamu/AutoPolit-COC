"""Unit tests for coordinate conversions and normalization."""

from __future__ import annotations

import unittest

import numpy as np

from backends.coordinates import (
    box_px_to_normalized,
    box_to_device_px,
    crop_normalized,
    to_device_px,
    to_normalized,
)


class CoordinateTests(unittest.TestCase):
    def setUp(self):
        self.width = 1920
        self.height = 1080

    def test_to_device_px_origin_and_max(self):
        px, py = to_device_px(0.0, 0.0, self.width, self.height)
        self.assertEqual((px, py), (0, 0))

        px, py = to_device_px(1.0, 1.0, self.width, self.height)
        self.assertEqual((px, py), (1919, 1079))

    def test_to_device_px_center(self):
        px, py = to_device_px(0.5, 0.5, self.width, self.height)
        self.assertEqual((px, py), (960, 540))

    def test_to_device_px_clamping(self):
        px, py = to_device_px(-0.5, 1.5, self.width, self.height)
        self.assertEqual((px, py), (0, 1079))

    def test_to_normalized(self):
        nx, ny = to_normalized(960, 540, self.width, self.height)
        self.assertAlmostEqual(nx, 0.5, places=4)
        self.assertAlmostEqual(ny, 0.5, places=4)

        nx, ny = to_normalized(0, 0, self.width, self.height)
        self.assertAlmostEqual(nx, 0.0)
        self.assertAlmostEqual(ny, 0.0)

    def test_round_trip(self):
        orig_nx, orig_ny = 0.25, 0.75
        px, py = to_device_px(orig_nx, orig_ny, self.width, self.height)
        re_nx, re_ny = to_normalized(px, py, self.width, self.height)
        self.assertAlmostEqual(orig_nx, re_nx, places=2)
        self.assertAlmostEqual(orig_ny, re_ny, places=2)

    def test_box_to_device_px(self):
        # 10% from left, 20% from top, 30% width, 40% height
        x, y, w, h = box_to_device_px((0.1, 0.2, 0.3, 0.4), self.width, self.height)
        self.assertEqual(x, 192)
        self.assertEqual(y, 216)
        self.assertEqual(w, 576)
        self.assertEqual(h, 432)

    def test_box_px_to_normalized(self):
        nx, ny, nw, nh = box_px_to_normalized((192, 216, 576, 432), self.width, self.height)
        self.assertAlmostEqual(nx, 0.1, places=3)
        self.assertAlmostEqual(ny, 0.2, places=3)
        self.assertAlmostEqual(nw, 0.3, places=3)
        self.assertAlmostEqual(nh, 0.4, places=3)

    def test_crop_normalized(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        # Set a 20x40 patch at (50:70, 40:80) to value 255
        image[50:70, 40:80] = 255

        # Crop normalized box (x=40/200=0.2, y=50/100=0.5, w=40/200=0.2, h=20/100=0.2)
        cropped = crop_normalized(image, (0.2, 0.5, 0.2, 0.2))
        self.assertEqual(cropped.shape, (20, 40, 3))
        self.assertTrue(np.all(cropped == 255))

    def test_crop_normalized_empty_guard(self):
        empty_img = np.zeros((0, 0, 3), dtype=np.uint8)
        cropped = crop_normalized(empty_img, (0.1, 0.1, 0.5, 0.5))
        self.assertEqual(cropped.size, 0)


if __name__ == "__main__":
    unittest.main()
