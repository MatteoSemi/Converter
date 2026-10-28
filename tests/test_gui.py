"""Headless desktop checks, including the real threaded conversion pipeline."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import time

import pytest
from PySide6.QtWidgets import QApplication

from pngvector.gui import MainWindow


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def wait_until(app, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            pytest.fail('Desktop conversion did not finish before timeout')
        time.sleep(.01)
    app.processEvents()


def test_desktop_runs_real_conversion_and_comparison(app, fixtures, tmp_path):
    window = MainWindow()
    window.show()
    app.processEvents()
    source = fixtures['wide margins']
    window.add_files([str(source), str(source)])
    assert window.files.count() == 1  # duplicate queue entries are avoided
    window.folder.setText(str(tmp_path))
    assert window.components.value() == 0
    assert window.smoothing.value() == 0
    assert window.precision.value() == 8
    window.start_conversion()
    wait_until(app, lambda: window.worker is not None and not window.worker.isRunning())
    assert window.current_result is not None, window.log.toPlainText()
    assert window.current_result.output.suffix == '.eps'
    assert window.current_result.output.is_file()
    assert window.current_result.preview.is_file()
    assert len(window.original_view.items) == 1
    assert len(window.overlay_view.items) == 2
    window.set_zoom(4)
    assert window.original_view.transform().m11() == 4
    assert window.converted_view.transform().m11() == 4
    window.blend.setValue(73)
    assert window.overlay_view.items[1].opacity() == .73
    window.diff.setChecked(True)
    assert window.converted_view.items[0].pixmap().width() == window.current_result.width
    assert 'Mean absolute RGB error' in window.report.text()
    assert '1 converted; 0 failed.' in window.log.toPlainText()
    window.close()


def test_desktop_batch_reports_corrupt_image_and_continues(app, fixtures, tmp_path):
    window = MainWindow()
    bad = tmp_path / 'broken.png'
    bad.write_bytes(b'not png')
    window.add_files([str(bad), str(fixtures['wide margins'])])
    window.folder.setText(str(tmp_path / 'outputs'))
    window.start_conversion()
    wait_until(app, lambda: window.worker is not None and not window.worker.isRunning())
    text = window.log.toPlainText()
    assert 'FAILED:' in text
    assert '1 converted; 1 failed.' in text
    assert window.current_result.output.exists()
    window.close()


@pytest.mark.parametrize('choice,extension', [('Composite', '.eps'), ('Retain alpha', '.svg')])
def test_desktop_partial_alpha_requires_explicit_dialog_choice(app, fixtures, tmp_path, choice, extension):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    window = MainWindow()
    window.add_files([str(fixtures['partial edges'])])
    window.folder.setText(str(tmp_path))
    observed = []

    def choose():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            assert 'partially transparent' in dialog.text()
            observed.append(dialog.text())
            next(button for button in dialog.buttons() if button.text().startswith(choice)).click()

    timer = QTimer()
    timer.timeout.connect(choose)
    timer.start(10)
    window.start_conversion()
    wait_until(app, lambda: window.worker is not None and not window.worker.isRunning())
    timer.stop()
    assert observed, 'Partially transparent input must prompt before exporting'
    assert window.current_result is not None, window.log.toPlainText()
    assert window.current_result.output.suffix == extension
    if extension == '.svg':
        assert not list(tmp_path.rglob('*.eps'))
        assert list(tmp_path.rglob('*.pdf'))
    window.close()


def test_desktop_large_job_estimate_can_be_cancelled(app, fixtures, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    window = MainWindow()
    window.add_files([str(fixtures['large'])])
    window.folder.setText(str(tmp_path))
    observed = []

    def cancel():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            assert 'Estimated vector output' in dialog.text()
            observed.append(dialog.text())
            dialog.button(QMessageBox.StandardButton.Cancel).click()

    timer = QTimer()
    timer.timeout.connect(cancel)
    timer.start(10)
    window.start_conversion()
    wait_until(app, lambda: window.worker is not None and not window.worker.isRunning())
    timer.stop()
    assert observed
    assert 'Cancelled.' in window.log.toPlainText()
    assert not list(tmp_path.rglob('*.eps'))
    window.close()


def test_large_header_only_estimate_still_prompts_for_partial_alpha(app, fixtures, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    import pngvector.gui as gui
    real_estimate = gui.estimate

    def header_estimate(source, options):
        info = real_estimate(source, options)
        return {**info, 'large': True, 'partial_alpha': None}

    monkeypatch.setattr(gui, 'estimate', header_estimate)
    window = MainWindow()
    window.add_files([str(fixtures['partial edges'])])
    window.folder.setText(str(tmp_path))
    observed = []

    def choose():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, QMessageBox):
            return
        observed.append(dialog.windowTitle())
        if dialog.windowTitle() == 'Large vector job':
            dialog.button(QMessageBox.StandardButton.Yes).click()
        else:
            next(b for b in dialog.buttons() if b.text().startswith('Composite')).click()

    timer = QTimer()
    timer.timeout.connect(choose)
    timer.start(10)
    window.start_conversion()
    wait_until(app, lambda: window.worker is not None and not window.worker.isRunning())
    timer.stop()
    assert observed == ['Large vector job', 'Partial transparency requires your choice']
    assert window.current_result is not None, window.log.toPlainText()
    assert window.current_result.output.suffix == '.eps'
    window.close()
