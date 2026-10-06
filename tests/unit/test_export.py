"""Unit tests for core.io.export (figure rasterization and output filenames).

prompt_save_path()/prompt_save_folder() are excluded: both spawn a real
tkinter subprocess dialog and are not practically unit-testable -- the batch
image export button that uses prompt_save_folder() is covered through
AppTest instead, in tests/integration/test_sidebar_batch_export_flow.py.
Fit-results CSVs are covered by test_results_csv.py.
"""

import plotly.graph_objects as go

import pytest

from core.io.export import export_figure_html, export_figure_png, create_filename


class TestExportFigureHtml:
    def test_produces_standalone_html(self):
        fig = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[1, 4, 9])])
        html = export_figure_html(fig)
        assert html.strip().lower().startswith("<!doctype html>") or "<html" in html.lower()
        assert "plotly" in html.lower()


class TestCreateFilename:
    def test_strips_txt_extension(self):
        assert create_filename("sample_raman.txt", "fit", "csv") == "sample_raman_fit.csv"

    def test_no_extension_to_strip(self):
        assert create_filename("sample", "preview", "png") == "sample_preview.png"


class TestExportFigurePng:
    """Plotly 6 deprecated ``to_image(engine=...)`` and Plotly 7 removed it.

    Passing it raised ``TypeError: to_image() got an unexpected keyword
    argument 'engine'`` on every Sample Report build under Plotly 7, so these
    fakes take the strict signature and would fail again if it came back.
    """

    class _Figure:
        def __init__(self):
            self.calls = []

        def to_image(self, *, format, width, height, scale):
            self.calls.append(
                {"format": format, "width": width, "height": height, "scale": scale}
            )
            return b"PNG-BYTES"

    def test_passes_no_engine_argument(self):
        fig = self._Figure()
        assert export_figure_png(fig, width=800, height=400, scale=1.0) == b"PNG-BYTES"
        assert fig.calls == [
            {"format": "png", "width": 800, "height": 400, "scale": 1.0}
        ]

    def test_wraps_failures_in_runtimeerror_keeping_the_cause(self):
        class _Broken:
            def to_image(self, **kwargs):
                raise ValueError("chrome not found")

        with pytest.raises(RuntimeError, match="PNG export failed") as excinfo:
            export_figure_png(_Broken())
        assert isinstance(excinfo.value.__cause__, ValueError)
