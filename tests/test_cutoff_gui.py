"""Exercise adjustable cutoffs through the real desktop controls and exports."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from hashlib import sha256
import time

from PIL import Image
import pytest
from PySide6.QtWidgets import QApplication

import pngvector.core as core
from pngvector.gui import MainWindow


@pytest.fixture(scope='module')
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    desktop = MainWindow()
    yield desktop
    desktop.selection_timer.stop()
    if desktop.worker and desktop.worker.isRunning():
        desktop.worker.cancel()
        desktop.worker.wait(5000)
    desktop.close()


def wait_until(app, condition):
    deadline = time.monotonic() + 15
    while not condition():
        app.processEvents()
        assert time.monotonic() < deadline, 'Desktop did not finish the requested update'
        time.sleep(.01)
    app.processEvents()


def selected_pixel(window, band):
    return window.line_selection_view.items[0].pixmap().toImage().pixelColor(band * 12 + 6, 12).name()


def make_bands(tmp_path):
    # Solid dark/gray/light regions plus faint dark/light and hidden RGB.
    bands = [(30, 255), (130, 255), (230, 255), (30, 64), (230, 64), (30, 0), (230, 0)]
    image = Image.new('RGBA', (12 * len(bands), 24))
    for index, (brightness, alpha) in enumerate(bands):
        image.paste((brightness, brightness, brightness, alpha), (index * 12, 0, (index + 1) * 12, 24))
    source = tmp_path / 'features.png'
    image.save(source)
    return source


def test_cutoff_controls_sync_and_stay_available_in_pixel_mode(window):
    controls = [window.line_threshold, window.line_threshold_slider,
                window.line_alpha_threshold, window.line_alpha_slider]
    assert all(not control.isEnabled() for control in controls)
    window.line_color.setCurrentIndex(window.line_color.findData('black'))
    window.mode.setCurrentIndex(window.mode.findData('pixel'))
    assert all(control.isEnabled() for control in controls)
    window.line_threshold_slider.setValue(42)
    assert window.line_threshold.value() == 42
    window.line_threshold.setValue(231)
    assert window.line_threshold_slider.value() == 231
    window.line_alpha_slider.setValue(1)
    assert window.line_alpha_threshold.value() == 1
    window.line_alpha_threshold.setValue(222)
    assert window.line_alpha_slider.value() == 222
    window.line_color.setCurrentIndex(window.line_color.findData('all'))
    assert all(not control.isEnabled() for control in controls)
    assert window.alpha.isEnabled()


@pytest.mark.parametrize('colour,initial,adjusted,faint_band', [
    ('black', 100, 180, 3), ('white', 180, 100, 4),
])
def test_live_cutoff_recovers_features_and_matches_new_export(app, window, tmp_path, colour, initial, adjusted, faint_band):
    source = make_bands(tmp_path)
    window.add_files([str(source)])
    window.files.setCurrentRow(0)
    window.folder.setText(str(tmp_path / 'out'))
    window.line_color.setCurrentIndex(window.line_color.findData(colour))
    window.mode.setCurrentIndex(window.mode.findData('pixel'))
    window.set_preview_background('#203050')
    window.line_threshold.setValue(initial)
    window.refresh_line_selection()
    assert selected_pixel(window, 1) == '#203050'
    window.line_threshold_slider.setValue(adjusted)
    # Check the scheduled live update as well as the exported result.
    assert window.selection_timer.isActive()
    line_colour = '#000000' if colour == 'black' else '#ffffff'
    wait_until(app, lambda: selected_pixel(window, 1) == line_colour)
    assert selected_pixel(window, faint_band) == '#203050'
    window.line_alpha_slider.setValue(1)
    window.refresh_line_selection()
    assert selected_pixel(window, faint_band) == line_colour
    assert selected_pixel(window, 5) == '#203050'
    assert selected_pixel(window, 6) == '#203050'
    live_pixels = window.line_selection_view.items[0].pixmap().toImage()

    window.start_conversion()
    assert window.worker.options.line_threshold == adjusted
    assert window.worker.options.line_alpha_threshold == 1
    wait_until(app, lambda: not window.worker.isRunning())
    assert window.current_result is not None, window.log.toPlainText()
    result = window.current_result
    with Image.open(result.preview.with_name('features.preview-alpha.png')) as rendered:
        composited = Image.alpha_composite(Image.new('RGBA', rendered.size, '#203050'), rendered.convert('RGBA'))
        for band in range(7):
            assert composited.getpixel((12 * band + 6, 12))[:3] == live_pixels.pixelColor(12 * band + 6, 12).getRgb()[:3]
    before = {path: sha256(path.read_bytes()).hexdigest() for path in [result.output, result.reference, result.preview, *result.extras]}
    window.line_threshold_slider.setValue(initial)
    window.line_alpha_slider.setValue(128)
    window.refresh_line_selection()
    assert selected_pixel(window, 1) == '#203050'
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())


def test_selection_preview_rejects_large_source_before_decoding(monkeypatch):
    monkeypatch.setattr(core, '_png_info', lambda source: (2001, 2000))

    def unexpected_decode(*args):
        pytest.fail('Oversized live selection was decoded')

    monkeypatch.setattr(core, '_load', unexpected_decode)
    with pytest.raises(core.LargeImageError, match='4 million'):
        core.load_selection_source('oversized.png')


def test_clearing_source_clears_live_selection(window, tmp_path):
    source = make_bands(tmp_path)
    window.add_files([str(source)])
    window.files.setCurrentRow(0)
    window.line_color.setCurrentIndex(window.line_color.findData('white'))
    window.refresh_line_selection()
    assert window.line_selection_view.items
    window.clear_files()
    window.refresh_line_selection()
    assert not window.line_selection_view.items


def test_cutoff_controls_fit_smaller_window_without_horizontal_scrolling(app, window):
    from PySide6.QtWidgets import QScrollArea
    window.resize(1068, 736)
    window.line_color.setCurrentIndex(window.line_color.findData('black'))
    window.show()
    app.processEvents()
    scroll = window.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    for control in [window.line_threshold, window.line_alpha_threshold]:
        center = control.mapTo(scroll.viewport(), control.rect().center())
        assert scroll.viewport().rect().contains(center)


def test_selection_ignores_physical_dpi_for_override_compatible_sources(tmp_path):
    source = tmp_path / 'unequal dpi.png'
    Image.new('RGBA', (12, 12), (0, 0, 0, 255)).save(source, dpi=(72, 144))
    selected_source = core.load_selection_source(source)
    assert selected_source.size == (12, 12)
    result = core.convert(source, tmp_path / 'out', core.Options(mode='pixel', line_color='black', dpi=96))
    assert result.dpi == 96
