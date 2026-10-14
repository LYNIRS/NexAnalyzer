"""Unit tests for modules.optical.processing.scale_bar (µm/px from a burned-in bar)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from modules.optical.processing.scale_bar import (
    _font_paths,
    detect_scale_bar,
    read_scale_label,
    um_per_native_px,
)

W, H = 1024, 768


def _film(seed=0):
    """Speckled grey film, like a CVD sample at 100x: bright dots, some saturated."""
    rng = np.random.default_rng(seed)
    img = rng.normal(0.62, 0.06, (H, W))
    ys, xs = rng.integers(0, H, 4000), rng.integers(0, W, 4000)
    img[ys, xs] = 1.0
    return np.clip(img, 0, 1)


def _with_bar(label="20 µm", bar_len=193, light=True, seed=0, x0=15, y0=693):
    """A film image with a boxed scale bar burned in, as the microscope exports it."""
    img = Image.fromarray((_film(seed) * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(img)
    box, ink = ((50, 50, 50), (255, 255, 255)) if light else ((235, 235, 235), (10, 10, 10))
    draw.rectangle([x0 - 7, y0 - 8, x0 + bar_len + 8, y0 + 66], fill=box)
    draw.rectangle([x0, y0, x0 + bar_len - 1, y0 + 14], fill=ink)
    if label:
        font = ImageFont.truetype(_font_paths()[0], 30)
        draw.text((x0 + 40, y0 + 20), label, font=font, fill=ink)
    return img


class TestDetectScaleBar:
    def test_measures_light_bar_to_the_pixel(self):
        bar = detect_scale_bar(_with_bar())
        assert bar is not None and bar.light
        assert (bar.x0, bar.x1, bar.y0, bar.y1) == (15, 207, 693, 707)
        assert bar.length_px == pytest.approx(193, abs=0.01)

    def test_measures_dark_bar_on_light_box(self):
        bar = detect_scale_bar(_with_bar(light=False, bar_len=250, x0=600, y0=40))
        assert bar is not None and not bar.light
        assert bar.length_px == pytest.approx(250, abs=0.01)

    def test_subpixel_length_from_antialiased_end(self):
        arr = np.asarray(_with_bar(bar_len=193)).copy()
        arr[693:708, 208] = (50 + 255) // 2  # a half-covered end pixel
        assert detect_scale_bar(arr).length_px == pytest.approx(193.5, abs=0.05)

    def test_film_without_a_bar_returns_none(self):
        for seed in range(3):
            img = Image.fromarray((_film(seed) * 255).astype(np.uint8))
            assert detect_scale_bar(img) is None


class TestReadScaleLabel:
    @pytest.mark.parametrize(
        "label, um",
        [("20 µm", 20.0), ("10 µm", 10.0), ("5 µm", 5.0), ("0.5 µm", 0.5),
         ("50 um", 50.0), ("500 nm", 0.5), ("100 µm", 100.0), ("2 mm", 2000.0)],
    )
    def test_reads_label(self, label, um):
        img = _with_bar(label=label)
        result = read_scale_label(img, detect_scale_bar(img))
        assert result is not None
        assert result.length_um == pytest.approx(um)

    def test_missing_label_returns_none(self):
        img = _with_bar(label=None)
        assert read_scale_label(img, detect_scale_bar(img)) is None

    def test_label_without_unit_returns_none(self):
        img = _with_bar(label="20")
        assert read_scale_label(img, detect_scale_bar(img)) is None


class TestUmPerNativePx:
    def test_rescales_a_downsized_export_to_native_pixels(self):
        # VBCA13's "Scale bar.png": 1024 wide, 193 px bar for 20 µm.
        assert um_per_native_px(20, 193, 1024, 2240) == pytest.approx(0.047372, abs=1e-6)

    def test_native_image_is_unscaled(self):
        assert um_per_native_px(20, 400, 2240, 2240) == pytest.approx(0.05)

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            um_per_native_px(20, 0, 1024)
