"""
Unit tests for the OM grid figure's position layout.

The defect this file exists for: until v4.6.0 `build_om_grid_figure` truncated
to nine positions while its title and coverage summary still counted every
frame, so a ten-point wafer's P10 was segmented, averaged, and silently
missing from the panels. Ten points now draw as the 2-3-3-2 wafer map.

The renders use small synthetic FrameResults -- layout is about counts and
cells, not about any real segmentation.
"""

import numpy as np
import pytest

from modules.optical.processing.contrast import FrameResult
from modules.optical.viz.om_grid import _grid_units, _row_layout, build_om_grid_figure


def _fake_frame(point: int, ref_label: str = "2L",
                labels=("Below 2L", "Bilayer", "Above 2L")) -> FrameResult:
    h, w = 24, 32
    rgb = np.full((h, w, 3), 180.0)
    return FrameResult(
        name=f"50X-{point}", point=point, frame_type="rectangular",
        ref_label=ref_label, labels=labels,
        percentages=(5.0, 90.0, 5.0),
        contrast_below=-6.0, contrast_above=4.0,
        components_below=3, components_above=2,
        original=rgb, overlay=rgb.copy(), valid=np.ones((h, w), dtype=bool),
        hist_centers=np.linspace(150, 210, 50),
        hist_counts=np.full(50, 10.0),
        mode=180.0, sigma_l=1.0, sigma_r=1.0, sigma_noise=1.0,
        threshold_low=170.0, threshold_high=190.0, shoulder=False,
    )


def test_ten_points_lay_out_as_the_wafer_map():
    assert _row_layout(10) == (2, 3, 3, 2)


def test_other_counts_keep_the_three_wide_rows():
    assert _row_layout(9) == (3, 3, 3)
    assert _row_layout(7) == (3, 3, 1)
    assert _row_layout(6) == (3, 3)
    assert _row_layout(1) == (1,)


def test_grid_units_divide_pairs_centring_and_histograms():
    """units/3 (a pair), units/6 (a centred two-pair row) and units/n (one
    histogram) must all come out whole; 9 must give the historical 18."""
    assert _grid_units(9) == 18
    assert _grid_units(10) == 30
    for n in range(1, 13):
        units = _grid_units(n)
        assert units % 6 == 0
        assert units % n == 0


def test_every_position_of_a_ten_point_wafer_is_drawn():
    """Ten frames render four panel rows, not a truncated three: the ten-point
    figure must come out taller than the nine-point one built the same way."""
    assert _height([_fake_frame(p) for p in range(1, 11)]) > \
        _height([_fake_frame(p) for p in range(1, 10)])


def test_ten_points_render_with_histograms_too():
    png = build_om_grid_figure([_fake_frame(p) for p in range(1, 11)],
                               sample_name="T", show_histograms=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _height(frames):
    from PIL import Image
    import io

    png = build_om_grid_figure(frames, sample_name="T", show_histograms=False)
    return Image.open(io.BytesIO(png)).height


def test_the_wafer_map_is_detected_from_point_numbers_not_frame_count():
    """A ten-point wafer missing P7 hands over nine frames, but it is still a
    ten-point wafer: it must draw the four-row 2-3-3-2 map with P7 an empty
    cell, not collapse into the nine-point grid -- which would silently shift
    P8..P10 one cell over."""
    ten_with_gap = [_fake_frame(p) for p in range(1, 11) if p != 7]
    ten_full = [_fake_frame(p) for p in range(1, 11)]
    nine = [_fake_frame(p) for p in range(1, 10)]

    assert _height(ten_with_gap) == _height(ten_full)
    assert _height(ten_with_gap) > _height(nine)


def test_a_sparse_nine_point_wafer_keeps_its_three_rows():
    """The archive wafer carrying only positions 4-6 must still draw rows one
    and two -- position 4 belongs in row 2, not in the top-left cell."""
    assert _height([_fake_frame(p) for p in (4, 5, 6)]) == \
        _height([_fake_frame(p) for p in range(1, 7)])


def test_the_suptitle_names_the_reference_from_labels_not_ref_label():
    """The defect this guards: the suptitle used to read `ref_label` directly,
    so a low_coverage frame (labels relabelled "Substrate", ref_label still
    the raw preset key like "1L") would print "1L reference" in its own
    title instead of "Substrate reference". Two frame sets differing only in
    `ref_label`, with identical `labels`, must render byte-identical PNGs --
    confirming the title (and everything else) no longer depends on
    `ref_label` at all."""
    from modules.optical.viz.om_grid import build_om_grid_figure

    labels = ("Below Substrate", "Substrate", "Monolayer")
    as_1L = [_fake_frame(p, ref_label="1L", labels=labels) for p in range(1, 4)]
    as_2L = [_fake_frame(p, ref_label="2L", labels=labels) for p in range(1, 4)]

    png_1L = build_om_grid_figure(as_1L, sample_name="T", show_histograms=False)
    png_2L = build_om_grid_figure(as_2L, sample_name="T", show_histograms=False)

    assert png_1L == png_2L


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
