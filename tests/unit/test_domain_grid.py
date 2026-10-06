"""
Unit tests for the domain-size grid figure.

Small synthetic FrameResults and DomainResults, matching
test_om_grid_layout.py's convention -- these tests are about the figure
drawing without raising and reacting correctly to missing data, not about
segmentation itself.
"""

import io

import numpy as np
import pytest
from PIL import Image

from modules.optical.processing.contrast import FrameResult
from modules.optical.processing.domain_size import DomainResult
from modules.optical.viz.domain_grid import build_domain_grid_figure


def _fake_frame(point: int) -> FrameResult:
    h, w = 24, 32
    rgb = np.full((h, w, 3), 180.0)
    return FrameResult(
        name=f"50X-{point}", point=point, frame_type="rectangular",
        ref_label="2L", labels=("Below 2L", "Bilayer", "Above 2L"),
        percentages=(5.0, 90.0, 5.0),
        contrast_below=-6.0, contrast_above=4.0,
        components_below=3, components_above=2,
        original=rgb, overlay=rgb.copy(), valid=np.ones((h, w), dtype=bool),
        hist_centers=np.linspace(150, 210, 50),
        hist_counts=np.full(50, 10.0),
        mode=180.0, sigma_l=1.0, sigma_r=1.0, sigma_noise=1.0,
        threshold_low=170.0, threshold_high=190.0, shoulder=False,
    )


def _fake_domain(
    point: int, n: int, calibrated: bool = False, population_is_large=None,
    area_um2=None,
) -> DomainResult:
    h, w = 24, 32
    label_mask = np.zeros((h, w), dtype=int)
    diam = np.full(n, 3.0, dtype=float)
    empty = np.zeros(0, dtype=float)
    for i in range(n):
        label_mask[10, 10 + i] = i + 1
    return DomainResult(
        point=point, n=n,
        diameters_px=diam if not calibrated else empty,
        diameters_um=diam if calibrated else empty,
        calibrated=calibrated,
        label_mask=label_mask,
        population_is_large=population_is_large,
        area_um2=area_um2,
    )


def _render(frames, domains, **kwargs):
    png = build_domain_grid_figure(frames, domains, sample_name="T", **kwargs)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return png


def _render_and_inspect(frames, domains, monkeypatch, **kwargs):
    """Like `_render`, but also returns every panel caption and the
    suptitle, by intercepting the figure before `build_domain_grid_figure`'s
    own `finally: plt.close(fig)` destroys it."""
    import matplotlib.pyplot as plt

    import modules.optical.viz.domain_grid as domain_grid_module

    captured = []
    monkeypatch.setattr(domain_grid_module.plt, "close", captured.append)
    png = build_domain_grid_figure(frames, domains, sample_name="T", **kwargs)
    fig = captured[0]
    panel_titles = [ax.get_title() for ax in fig.axes if ax.get_title()]
    suptitle = fig.get_suptitle()
    plt.close(fig)
    return png, panel_titles, suptitle


class TestBuildDomainGridFigure:
    def test_renders_a_basic_grid(self):
        frames = [_fake_frame(p) for p in (1, 2, 3)]
        domains = [_fake_domain(p, n=2) for p in (1, 2, 3)]
        _render(frames, domains)

    def test_raises_on_no_frames(self):
        with pytest.raises(ValueError):
            build_domain_grid_figure([], [], sample_name="T")

    def test_a_frame_missing_its_domain_result_still_renders(self):
        """A point present in `frames` but absent from `domain_results` is an
        empty cell, matching build_om_grid_figure's own missing-frame handling
        -- not a crash."""
        _render([_fake_frame(1)], [])

    def test_a_gap_in_points_draws_a_blank_cell_not_a_shift(self):
        frames = [_fake_frame(1), _fake_frame(3)]
        domains = [_fake_domain(1, n=1), _fake_domain(3, n=1)]
        png_with_gap = _render(frames, domains)
        png_full = _render(
            [_fake_frame(p) for p in (1, 2, 3)],
            [_fake_domain(p, n=1) for p in (1, 2, 3)],
        )
        assert Image.open(io.BytesIO(png_with_gap)).size == \
            Image.open(io.BytesIO(png_full)).size

    def test_zero_domains_in_every_frame_still_renders(self):
        frames = [_fake_frame(1)]
        domains = [_fake_domain(1, n=0)]
        _render(frames, domains)

    def test_calibrated_and_uncalibrated_frames_both_render(self):
        frames = [_fake_frame(1), _fake_frame(2)]
        domains = [_fake_domain(1, n=1, calibrated=True), _fake_domain(2, n=1, calibrated=False)]
        _render(frames, domains)

    def test_magnification_label_does_not_raise_when_omitted(self):
        _render([_fake_frame(1)], [_fake_domain(1, n=1)], magnification=None)
        _render([_fake_frame(1)], [_fake_domain(1, n=1)], magnification="50x")

    def test_class_label_does_not_raise_when_omitted_or_given(self):
        _render([_fake_frame(1)], [_fake_domain(1, n=1)], class_label=None)
        _render([_fake_frame(1)], [_fake_domain(1, n=1)], class_label="Bilayer")

    def test_a_split_frame_renders_two_populations(self):
        """A frame whose DomainResult carries population_is_large takes the
        two-colour path instead of the single-colour one -- both must render
        without raising, and a mix of split and unsplit frames in the same
        grid (per-frame automatic splitting) must not crash either."""
        is_large = np.array([False, False, True])
        frames = [_fake_frame(1), _fake_frame(2)]
        domains = [
            _fake_domain(1, n=3, population_is_large=is_large),
            _fake_domain(2, n=2),  # unsplit, in the same grid
        ]
        _render(frames, domains)


class TestNucleationDensity:
    """Density text -- "/(100µm)²" -- shown only when calibrated, always on
    its own new line rather than appended to an existing one."""

    def test_unsplit_calibrated_panel_shows_a_density_line(self, monkeypatch):
        domain = _fake_domain(1, n=4, calibrated=True, area_um2=20.0)
        _, panel_titles, _ = _render_and_inspect(
            [_fake_frame(1)], [domain], monkeypatch,
        )
        (caption,) = [t for t in panel_titles if "/(100µm)²" in t]
        lines = caption.split("\n")
        # Its own line, not appended onto the n=/mean line.
        assert any(line.strip().endswith("/(100µm)²") and "n=" not in line for line in lines)

    def test_uncalibrated_panel_shows_no_density_text(self, monkeypatch):
        domain = _fake_domain(1, n=4, calibrated=False, area_um2=None)
        _, panel_titles, suptitle = _render_and_inspect(
            [_fake_frame(1)], [domain], monkeypatch,
        )
        assert not any("/(100µm)²" in t for t in panel_titles)
        assert "/(100µm)²" not in suptitle

    def test_split_calibrated_panel_shows_both_densities_on_their_own_lines(
        self, monkeypatch
    ):
        is_large = np.array([False, False, True])
        domain = _fake_domain(
            1, n=3, calibrated=True, population_is_large=is_large, area_um2=10.0,
        )
        _, panel_titles, suptitle = _render_and_inspect(
            [_fake_frame(1)], [domain], monkeypatch,
        )
        (caption,) = [t for t in panel_titles if "/(100µm)²" in t]
        lines = caption.split("\n")
        small_density_lines = [
            line for line in lines if line.startswith("small:") and "/(100µm)²" in line
        ]
        large_density_lines = [
            line for line in lines if line.startswith("large:") and "/(100µm)²" in line
        ]
        assert len(small_density_lines) == 1
        assert len(large_density_lines) == 1
        # Not combined onto one line with the other population.
        assert "large" not in small_density_lines[0]

    def test_suptitle_shows_pooled_density_when_calibrated(self, monkeypatch):
        domain = _fake_domain(1, n=4, calibrated=True, area_um2=20.0)
        _, _, suptitle = _render_and_inspect([_fake_frame(1)], [domain], monkeypatch)
        assert "/(100µm)²" in suptitle
