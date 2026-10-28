"""Regression coverage for single-colour export's display-only background control."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from hashlib import sha256

from PIL import Image
import pytest
from PySide6.QtWidgets import QApplication

from pngvector.core import Options, convert
from pngvector.gui import MainWindow


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def pixel(view, x, y, layer=0):
    return view.items[layer].pixmap().toImage().pixelColor(x, y).name()


@pytest.mark.parametrize('filename', [
    'feature.preview.png.extra.png',
    'feature.reference.png.extra.png',
])
def test_preview_background_handles_embedded_sidecar_suffixes(app, tmp_path, filename):
    source = tmp_path / filename
    image = Image.new('RGBA', (30, 20), (255, 255, 255, 0))
    for x in range(10, 30):
        for y in range(20):
            image.putpixel((x, y), (255, 255, 255, 255) if x < 20 else (220, 60, 20, 255))
    image.save(source)
    result = convert(source, tmp_path / 'out', Options(mode='pixel', background='#203050'))
    metrics = deepcopy(result.metrics)
    fingerprints = {path: sha256(path.read_bytes()).hexdigest()
                    for path in result.output.parent.iterdir() if path.is_file()}
    window = MainWindow()
    try:
        window.current_result = result
        window.refresh_images()
        # Existing comparison-background behaviour is retained until a display
        # background is explicitly chosen, including on a freshly opened window.
        assert result.comparison_background == '#203050'
        for view in (window.original_view, window.converted_view, window.overlay_view):
            assert pixel(view, 2, 10) == '#203050'
        assert pixel(window.overlay_view, 2, 10, layer=1) == '#203050'

        window.set_zoom(2.5)
        window.blend.setValue(73)
        window.set_preview_background('#112233')
        for view in (window.original_view, window.converted_view, window.overlay_view):
            assert pixel(view, 2, 10) == '#112233'
            # All-colour opaque artwork remains opaque over any background.
            assert pixel(view, 15, 10) == '#ffffff'
            assert pixel(view, 25, 10) == '#dc3c14'
            assert view.transform().m11() == 2.5
        assert pixel(window.overlay_view, 2, 10, layer=1) == '#112233'
        assert pixel(window.overlay_view, 15, 10, layer=1) == '#ffffff'
        assert window.overlay_view.items[1].opacity() == .73

        window.diff.setChecked(True)
        assert pixel(window.converted_view, 2, 10) == '#000000'
        assert pixel(window.converted_view, 15, 10) == '#000000'
        window.diff.setChecked(False)
        assert pixel(window.converted_view, 2, 10) == '#112233'
        assert window.converted_view.transform().m11() == 2.5
        assert window.overlay_view.items[1].opacity() == .73
        assert result.metrics == metrics
        assert {path: sha256(path.read_bytes()).hexdigest() for path in fingerprints} == fingerprints
    finally:
        window.close()


def test_options_preserve_existing_positional_arguments():
    options = Options('pixel', 'vtracer', 9, .3, 5, 2, 'retain', '#123456', 120, 1000, True)
    assert (options.mode, options.tracer, options.detail, options.smoothing,
            options.color_precision, options.min_component, options.alpha_policy,
            options.background, options.dpi, options.max_pixels, options.force_large) == (
                'pixel', 'vtracer', 9, .3, 5, 2, 'retain', '#123456', 120, 1000, True)
    assert options.line_color == 'all'
    assert options.line_threshold == 128
