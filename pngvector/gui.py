"""Local desktop front end; conversions run on a cancellable worker thread."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
import sys

from PIL import Image, ImageChops, ImageEnhance
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget, QAbstractItemView, QGroupBox, QScrollArea,
)

from .core import (Options, convert, estimate, Cancelled, ConversionError,
                   AlphaPolicyRequired, extract_lines, load_selection_source)


def _pixmap(path_or_image):
    if isinstance(path_or_image, (str, Path)):
        return QPixmap(str(path_or_image))
    image = path_or_image.convert('RGBA')
    raw = image.tobytes('raw', 'RGBA')
    return QPixmap.fromImage(QImage(raw, image.width, image.height,
                                    image.width * 4, QImage.Format.Format_RGBA8888).copy())


class ImageView(QGraphicsView):
    """Shared scene coordinates, explicit zoom, synchronized panning."""
    zoom_requested = Signal(float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setBackgroundBrush(QColor('#c7c7c7'))
        self.items = []

    def set_images(self, base, overlay=None):
        self.scene().clear()
        self.items = [self.scene().addPixmap(_pixmap(base))]
        if overlay is not None:
            self.items.append(self.scene().addPixmap(_pixmap(overlay)))
            self.items[-1].setOpacity(.5)
        self.setSceneRect(self.items[0].boundingRect())

    def set_zoom(self, factor):
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(center)

    def wheelEvent(self, event):
        self.zoom_requested.emit(1.25 if event.angleDelta().y() > 0 else .8)
        event.accept()


class FileList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


class ConversionWorker(QThread):
    progress = Signal(float, str)
    complete = Signal(object)
    error = Signal(str)
    question = Signal(str, object)
    summary = Signal(int, int, bool)

    def __init__(self, files, folder, options):
        super().__init__()
        self.files, self.folder, self.options = files, folder, options
        self.stop = Event()
        self.answer_ready = Event()
        self.answer_value = None

    def cancel(self):
        self.stop.set()
        self.answer_ready.set()

    def answer(self, value):
        self.answer_value = value
        self.answer_ready.set()

    def ask(self, kind, data):
        self.answer_value = None
        self.answer_ready.clear()
        self.question.emit(kind, data)
        while not self.answer_ready.wait(.1):
            if self.stop.is_set():
                return None
        return self.answer_value

    def run(self):
        succeeded = failed = 0
        total = len(self.files)
        for index, source in enumerate(self.files):
            if self.stop.is_set():
                break
            try:
                options = self.options
                details = estimate(source, options)
                self.progress.emit(index / total, f'{Path(source).name}: {details["pixels"]:,} pixels; '
                                   f'estimated vector size {details["estimated_bytes"] / 1_000_000:.1f} MB '
                                   '(estimate, content dependent).')
                if details['pixels'] > 16_000_000:
                    raise ConversionError('Images over 16 million pixels exceed the memory safety limit. Reduce source dimensions explicitly before conversion.')
                if details.get('large') and not options.force_large:
                    if not self.ask('large', {'source': source, **details}):
                        self.stop.set()
                        break
                    options = replace(options, force_large=True)
                if details.get('partial_alpha') and options.alpha_policy == 'ask' and options.line_color == 'all':
                    policy = self.ask('alpha', {'source': source, 'background': options.background})
                    if policy is None:
                        self.stop.set()
                        break
                    options = replace(options, alpha_policy=policy)
                def perform(active_options):
                    return convert(source, self.folder, active_options,
                                   progress=lambda fraction, message, i=index:
                                   self.progress.emit((i + fraction) / total, f'{Path(source).name}: {message}'),
                                   cancelled=self.stop.is_set)
                try:
                    result = perform(options)
                except AlphaPolicyRequired:
                    # Header-only estimates of large images may not inspect alpha.
                    # The pipeline checks decoded alpha before creating outputs.
                    if options.alpha_policy != 'ask':
                        raise
                    policy = self.ask('alpha', {'source': source, 'background': options.background})
                    if policy is None:
                        self.stop.set()
                        break
                    options = replace(options, alpha_policy=policy)
                    result = perform(options)
                succeeded += 1
                self.complete.emit(result)
            except Cancelled:
                self.stop.set()
                break
            except Exception as exc:
                failed += 1
                self.error.emit(f'{source}\n{type(exc).__name__}: {exc}')
        self.summary.emit(succeeded, failed, self.stop.is_set())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('PNG Vector — local PNG to editable EPS')
        self.resize(1280, 900)
        self.settings = QSettings('PNGVector', 'PNGVector')
        self.worker = None
        self.results = {}
        self.current_result = None
        self.zoom = 1.0
        self.syncing = False
        self._selection_cache = None
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.setInterval(120)
        self.selection_timer.timeout.connect(self.refresh_line_selection)
        self.setAcceptDrops(True)
        main = QWidget()
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)
        left = QWidget()
        controls = QVBoxLayout(left)
        splitter.addWidget(left)
        controls.addWidget(QLabel('Drop PNG files below, or add files. Select a result to inspect it.'))
        self.files = FileList()
        self.files.setMinimumHeight(90)
        self.files.files_dropped.connect(self.add_files)
        self.files.currentRowChanged.connect(self.show_selected)
        controls.addWidget(self.files)
        row = QHBoxLayout()
        for label, callback in [('Add PNGs…', self.pick_files), ('Remove selected', self.remove_files), ('Clear', self.clear_files)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        controls.addLayout(row)
        row = QHBoxLayout()
        self.folder = QLineEdit(str(self.settings.value('output', str(Path.cwd() / 'converted'))))
        self.folder.setPlaceholderText('Output folder')
        self.folder.setToolTip('Outputs get unique names. Source files are never modified.')
        row.addWidget(self.folder)
        button = QPushButton('Output folder…')
        button.clicked.connect(self.pick_folder)
        row.addWidget(button)
        controls.addLayout(row)

        group = QGroupBox('Conversion and fidelity')
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode = QComboBox()
        self.mode.addItem('Trace artwork — editable vector paths (approximation)', 'smooth')
        self.mode.addItem('Exact pixel grid — merged vector rectangles', 'pixel')
        form.addRow('Mode', self.mode)
        self.line_color = QComboBox()
        for label, value in [('All colours', 'all'), ('Black lines only — other pixels transparent', 'black'), ('White lines only — other pixels transparent', 'white')]:
            self.line_color.addItem(label, value)
        form.addRow('Output colours', self.line_color)
        self.line_threshold = QSpinBox()
        self.line_threshold.setRange(0, 255)
        self.line_threshold.setValue(128)
        self.line_threshold.setToolTip('Black keeps brightness below this cutoff; white keeps brightness at or above it. Adjust while inspecting the Line selection tab.')

        def cutoff_row(spin):
            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(spin.minimum(), spin.maximum())
            slider.setValue(spin.value())
            slider.setToolTip(spin.toolTip())
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            spin.valueChanged.connect(self.line_options_changed)
            row.addWidget(slider, 1)
            row.addWidget(spin)
            return row, slider

        cutoff, self.line_threshold_slider = cutoff_row(self.line_threshold)
        form.addRow('Line brightness cutoff (0–255)', cutoff)
        self.line_alpha_threshold = QSpinBox()
        self.line_alpha_threshold.setRange(1, 255)
        self.line_alpha_threshold.setValue(128)
        self.line_alpha_threshold.setToolTip('Lower this cutoff to retain faint, partially transparent strokes. Kept strokes become opaque. Fully transparent pixels always stay transparent.')
        cutoff, self.line_alpha_slider = cutoff_row(self.line_alpha_threshold)
        form.addRow('Minimum line opacity (1–255)', cutoff)
        self.line_hint = QLabel()
        self.line_hint.setWordWrap(True)
        form.addRow(self.line_hint)
        self.tracer = QComboBox()
        for label, value in [('Automatic (Potrace for black/white)', 'auto'), ('VTracer — colour', 'vtracer'), ('Potrace — black/white only', 'potrace')]:
            self.tracer.addItem(label, value)
        form.addRow('Tracing engine', self.tracer)
        self.detail = QSpinBox()
        self.detail.setRange(0, 10)
        self.detail.setValue(6)
        self.detail.setToolTip('Higher values retain more detail during curve fitting when smoothing is above zero. At zero smoothing traced contours are unsimplified. Tracing cannot recover an exact original vector.')
        form.addRow('Detail preservation (0–10)', self.detail)
        self.smoothing = QDoubleSpinBox()
        self.smoothing.setRange(0, 1)
        self.smoothing.setSingleStep(.1)
        self.smoothing.setValue(0)
        self.smoothing.setToolTip('Zero preserves unsimplified VTracer contours, including pixel stair steps, thin lines and dots. Higher values fit curves and can change corners and small features. Lower path counts can reduce fidelity.')
        form.addRow('Curve smoothing (0–1)', self.smoothing)
        self.precision = QSpinBox()
        self.precision.setRange(1, 8)
        self.precision.setValue(8)
        self.precision.setToolTip('8 retains full channel precision in colour tracing. Lower precision merges colours.')
        form.addRow('Colour precision (bits/channel)', self.precision)
        self.components = QSpinBox()
        self.components.setRange(0, 10000)
        self.components.setValue(0)
        self.components.setToolTip('Destructive cleanup: 0 disables it. Potrace uses pixel area; VTracer uses its native speckle setting. Effects differ by engine and may erase punctuation or merge details.')
        form.addRow('Small-component cleanup (0 = off)', self.components)
        self.alpha = QComboBox()
        self.alpha.addItem('Ask when partial transparency is detected', 'ask')
        self.alpha.addItem('Composite partial alpha onto selected background → EPS', 'composite')
        self.alpha.addItem('Retain partial alpha → pixel-vector SVG + PDF, no EPS', 'retain')
        form.addRow('Partial transparency', self.alpha)
        self.background = QPushButton('#ffffff')
        self.background.clicked.connect(self.pick_background)
        form.addRow('Composite / comparison background', self.background)
        self.use_dpi = QCheckBox('Override PNG DPI')
        self.use_dpi.setToolTip('Otherwise use PNG resolution metadata or 96 DPI.')
        self.dpi = QDoubleSpinBox()
        self.dpi.setRange(1, 9600)
        self.dpi.setValue(96)
        self.dpi.setEnabled(False)
        self.use_dpi.toggled.connect(self.dpi.setEnabled)
        dpirow = QHBoxLayout()
        dpirow.addWidget(self.use_dpi)
        dpirow.addWidget(self.dpi)
        form.addRow(dpirow)
        self.preserve = QPushButton('Restore Preserve detail defaults')
        self.preserve.clicked.connect(self.preserve_defaults)
        form.addRow(self.preserve)
        for combo in [self.mode, self.line_color, self.tracer, self.alpha]:
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        # Keep conversion settings reachable on smaller laptop displays.
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(group)
        settings_scroll.setMinimumHeight(200)
        controls.addWidget(settings_scroll, 1)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        note = QLabel('Tracing is an approximation. Zero smoothing retains unsimplified contours; pixel stair steps can remain. Text is traced as shapes; fonts are never substituted. '
                      'Lower path counts can lose detail. Exact pixel mode retains stair-stepped edges and may create huge files. '
                      'Fully transparent areas remain unpainted unless you explicitly composite partial alpha.')
        note.setWordWrap(True)
        controls.addWidget(note)
        runrow = QHBoxLayout()
        self.start = QPushButton('Convert all queued PNGs')
        self.start.clicked.connect(self.start_conversion)
        self.cancel = QPushButton('Cancel')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_conversion)
        runrow.addWidget(self.start)
        runrow.addWidget(self.cancel)
        controls.addLayout(runrow)

        right = QWidget()
        previews = QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setSizes([480, 800])
        toolbar_hint = QLabel('Both previews: drag to pan; wheel or buttons to zoom')
        toolbar_hint.setWordWrap(True)
        previews.addWidget(toolbar_hint)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        for label, callback in [('−', lambda: self.change_zoom(.8)), ('+', lambda: self.change_zoom(1.25)), ('100%', lambda: self.set_zoom(1)), ('Fit', self.fit_preview)]:
            button = QPushButton(label)
            button.setFixedWidth(48)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.zoom_label = QLabel('100%')
        toolbar.addWidget(self.zoom_label)
        self.preview_background = QPushButton('Preview background…')
        self.preview_colour = None  # Use each conversion's background until explicitly changed.
        self.preview_background.clicked.connect(self.pick_preview_background)
        self.preview_background.setToolTip('Changes the viewing background only; exported artwork is unchanged.')
        toolbar.addWidget(self.preview_background)
        previews.addLayout(toolbar)
        self.tabs = QTabWidget()
        previews.addWidget(self.tabs, 1)
        side = QWidget()
        side_layout = QHBoxLayout(side)
        self.original_view = ImageView()
        self.converted_view = ImageView()
        for title, view in [('Original (comparison background)', self.original_view), ('Final output rendered by interpreter', self.converted_view)]:
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            box.addWidget(view)
            side_layout.addLayout(box)
        self.tabs.addTab(side, 'Synchronized views')
        overlay = QWidget()
        overlay_layout = QVBoxLayout(overlay)
        self.overlay_view = ImageView()
        overlay_layout.addWidget(self.overlay_view, 1)
        overlay_layout.addWidget(QLabel('Overlay blend: original ← → converted'))
        self.blend = QSlider(Qt.Orientation.Horizontal)
        self.blend.setRange(0, 100)
        self.blend.setValue(50)
        self.blend.valueChanged.connect(self.update_blend)
        overlay_layout.addWidget(self.blend)
        self.tabs.addTab(overlay, 'Overlay comparison')
        selection = QWidget()
        selection_layout = QVBoxLayout(selection)
        self.selection_note = QLabel('Choose black or white lines and select a PNG to preview the cutoffs before converting.')
        self.selection_note.setWordWrap(True)
        selection_layout.addWidget(self.selection_note)
        self.line_selection_view = ImageView()
        self.line_selection_view.zoom_requested.connect(lambda factor: self.line_selection_view.scale(factor, factor))
        selection_layout.addWidget(self.line_selection_view, 1)
        fit_selection = QPushButton('Fit selection preview')
        fit_selection.clicked.connect(self.fit_line_selection)
        selection_layout.addWidget(fit_selection)
        self.tabs.addTab(selection, 'Line selection')
        self.diff = QCheckBox('Show amplified (4×) RGB difference')
        self.diff.setToolTip('Shows absolute RGB difference in the converted pane using the current preview background.')
        self.diff.toggled.connect(self.refresh_images)
        previews.addWidget(self.diff)
        self.report = QLabel('Convert a PNG to inspect the final EPS. EPS is rendered with Ghostscript at matching canvas dimensions.\n'
                             'Metrics are diagnostic; inspect lettering, holes, thin lines, margins and seams at high zoom.')
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        previews.addWidget(self.report)
        for view in [self.original_view, self.converted_view, self.overlay_view]:
            view.zoom_requested.connect(self.change_zoom)
            view.horizontalScrollBar().valueChanged.connect(lambda _, v=view: self.sync_pan(v))
            view.verticalScrollBar().valueChanged.connect(lambda _, v=view: self.sync_pan(v))
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        outer.addWidget(self.progress)
        self.status = QLabel('Ready. All processing stays on this computer; no account or upload is used.')
        outer.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        outer.addWidget(self.log)
        self.line_color.currentIndexChanged.connect(self.line_options_changed)
        self.line_options_changed()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def add_files(self, paths):
        existing = {self.files.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.files.count())}
        for path in paths:
            p = Path(path).absolute()
            if p.suffix.lower() != '.png' or not p.is_file():
                self.log.appendPlainText(f'Not added: {p}. Choose an existing PNG file.')
                continue
            if str(p) not in existing:
                self.files.addItem(p.name)
                self.files.item(self.files.count() - 1).setData(Qt.ItemDataRole.UserRole, str(p))
                self.files.item(self.files.count() - 1).setToolTip(str(p))
                existing.add(str(p))

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose PNG images', '', 'PNG images (*.png *.PNG)')
        self.add_files(paths)

    def remove_files(self):
        if self.worker and self.worker.isRunning():
            return
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def clear_files(self):
        if not self.worker or not self.worker.isRunning():
            self.files.clear()

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose output folder', self.folder.text())
        if folder:
            self.folder.setText(folder)

    def pick_background(self):
        colour = QColorDialog.getColor(QColor(self.background.text()), self, 'Choose compositing background')
        if colour.isValid():
            self.background.setText(colour.name())
            self.background.setStyleSheet(f'background-color: {colour.name()}; color: {"black" if colour.lightness() > 128 else "white"}')

    def pick_preview_background(self):
        colour = QColorDialog.getColor(QColor(self.effective_preview_colour()), self, 'Choose preview background (display only)')
        if colour.isValid():
            self.set_preview_background(colour.name())

    def set_preview_background(self, colour):
        colour = QColor(colour)
        if not colour.isValid():
            return
        self.preview_colour = colour.name()
        for view in [self.original_view, self.converted_view, self.overlay_view]:
            view.setBackgroundBrush(QColor(self.preview_colour))
        self.refresh_images()
        self.selection_timer.start()

    def effective_preview_colour(self):
        return self.preview_colour or (self.current_result.comparison_background if self.current_result else self.background.text())

    def line_options_changed(self):
        enabled = self.line_color.currentData() != 'all'
        for control in [self.line_threshold, self.line_threshold_slider, self.line_alpha_threshold, self.line_alpha_slider]:
            control.setEnabled(enabled)
        self.alpha.setEnabled(not enabled)
        if enabled:
            direction = 'Raise brightness to keep more dark features.' if self.line_color.currentData() == 'black' else 'Lower brightness to keep more light features.'
            self.line_hint.setText(direction + ' Lower minimum opacity to keep faint strokes. Check the Line selection tab; reconvert to update vector files.')
        else:
            self.line_hint.setText('All colours preserves the source colours and uses the selected transparency policy.')
        self.selection_timer.start()

    def refresh_line_selection(self):
        row = self.files.currentRow()
        if self.line_color.currentData() == 'all' or row < 0:
            self.line_selection_view.scene().clear()
            self.line_selection_view.items = []
            self.selection_note.setText('Choose black or white lines and select a PNG to preview the cutoffs before converting.')
            return
        source = Path(self.files.item(row).data(Qt.ItemDataRole.UserRole))
        try:
            stat = source.stat()
            key = (source, stat.st_mtime_ns, stat.st_size)
            changed = self._selection_cache is None or self._selection_cache[0] != key
            if changed:
                self._selection_cache = (key, load_selection_source(source))
            options = Options(line_color=self.line_color.currentData(), line_threshold=self.line_threshold.value(),
                              line_alpha_threshold=self.line_alpha_threshold.value())
            selected = extract_lines(self._selection_cache[1], options)
            kept = selected.getchannel('A').histogram()[255]
            # A neutral background keeps either line colour visible by default.
            background = self.preview_colour or '#808080'
            display = Image.alpha_composite(Image.new('RGBA', selected.size, background), selected)
            self.line_selection_view.setBackgroundBrush(QColor(background))
            self.line_selection_view.set_images(display)
            if changed:
                self.fit_line_selection()
            self.selection_note.setText(f'{source.name}: {kept:,} of {selected.width * selected.height:,} pixels kept. '
                                        'Live pixel selection before tracing; smoothing and cleanup may change the final vector. '
                                        'Existing exports stay unchanged until you convert again.')
        except (ConversionError, OSError, ValueError) as exc:
            self.line_selection_view.scene().clear()
            self.line_selection_view.items = []
            self._selection_cache = None
            self.selection_note.setText(f'Cannot preview selection: {exc}')

    def fit_line_selection(self):
        rect = self.line_selection_view.sceneRect()
        if self.line_selection_view.items and not rect.isEmpty():
            self.line_selection_view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def preserve_defaults(self):
        self.detail.setValue(6)
        self.smoothing.setValue(0)
        self.precision.setValue(8)
        self.components.setValue(0)

    def mode_changed(self):
        smooth = self.mode.currentData() == 'smooth'
        for widget in [self.tracer, self.detail, self.smoothing, self.precision, self.components, self.preserve]:
            widget.setEnabled(smooth)

    def start_conversion(self):
        if not self.files.count():
            QMessageBox.information(self, 'Add images', 'Use Add PNGs or drag PNG files into the queue.')
            return
        if not self.folder.text().strip():
            QMessageBox.information(self, 'Choose output folder', 'Choose a writable output folder before converting.')
            return
        files = [self.files.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.files.count())]
        options = Options(line_color=self.line_color.currentData(), line_threshold=self.line_threshold.value(),
                          line_alpha_threshold=self.line_alpha_threshold.value(), mode=self.mode.currentData(), tracer=self.tracer.currentData(),
                          detail=self.detail.value(), smoothing=self.smoothing.value(),
                          color_precision=self.precision.value(), min_component=self.components.value(),
                          alpha_policy=self.alpha.currentData(), background=self.background.text(),
                          dpi=self.dpi.value() if self.use_dpi.isChecked() else None)
        self.settings.setValue('output', self.folder.text())
        self.worker = ConversionWorker(files, self.folder.text(), options)
        self.worker.progress.connect(self.on_progress)
        self.worker.complete.connect(self.on_complete)
        self.worker.error.connect(self.on_error)
        self.worker.question.connect(self.on_question)
        self.worker.summary.connect(self.on_summary)
        self.worker.finished.connect(self.worker_finished)
        self.start.setEnabled(False)
        self.cancel.setEnabled(True)
        self.progress.setValue(0)
        self.log.appendPlainText(f'Starting {len(files)} file(s). Existing outputs receive unique names.')
        self.worker.start()

    def cancel_conversion(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText('Cancelling; waiting for the active stage to stop safely…')
            self.cancel.setEnabled(False)

    def on_question(self, kind, data):
        if not self.worker or self.worker.stop.is_set():
            return
        if kind == 'large':
            message = (f'{Path(data["source"]).name}: {data["width"]:,} × {data["height"]:,} pixels.\n'
                       f'Estimated vector output: {data["estimated_bytes"] / 1_000_000:.1f} MB '
                       '(content dependent; actual file can be larger).\n'
                       f'Estimated working memory: {data.get("estimated_memory_bytes", 0) / 1_000_000_000:.2f} GB '
                       '(planning estimate; not a memory guarantee).\n\n'
                       'This job may take substantial memory and time. You can cancel while it runs. Continue?')
            answer = QMessageBox.question(self, 'Large vector job', message,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                          QMessageBox.StandardButton.Cancel)
            self.worker.answer(answer == QMessageBox.StandardButton.Yes)
        else:
            box = QMessageBox(self)
            box.setWindowTitle('Partial transparency requires your choice')
            box.setText(f'{Path(data["source"]).name} contains partially transparent pixels.\n\n'
                        'EPS cannot represent general partial transparency. Choose explicitly: composite onto '
                        f'{data["background"]} and produce EPS, or retain alpha as pixel-vector SVG + PDF '
                        '(no EPS; this retains the pixel grid, not smooth traced curves).\n\n'
                        'Fully transparent areas are otherwise left unpainted.')
            composite = box.addButton(f'Composite onto {data["background"]} → EPS', QMessageBox.ButtonRole.AcceptRole)
            retain = box.addButton('Retain alpha → SVG + PDF', QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            self.worker.answer('composite' if box.clickedButton() == composite else
                               'retain' if box.clickedButton() == retain else None)

    def on_progress(self, fraction, message):
        self.progress.setValue(round(max(0, min(1, fraction)) * 1000))
        self.status.setText(message)
        if 'estimated vector size' in message:
            self.log.appendPlainText(message)

    def on_error(self, message):
        self.log.appendPlainText('FAILED: ' + message)

    def on_complete(self, result):
        self.results[str(Path(result.source).absolute())] = result
        self.current_result = result
        for index in range(self.files.count()):
            if self.files.item(index).data(Qt.ItemDataRole.UserRole) == str(Path(result.source).absolute()):
                self.files.setCurrentRow(index)
                break
        self.log.appendPlainText(f'Created {result.output}\n' + '\n'.join(f'Warning: {w}' for w in result.warnings))
        self.refresh_images()
        self.fit_preview()

    def on_summary(self, succeeded, failed, cancelled):
        message = f'{succeeded} converted; {failed} failed.' + (' Cancelled.' if cancelled else ' Finished.')
        self.status.setText(message)
        self.log.appendPlainText(message)
        if not cancelled:
            self.progress.setValue(1000)

    def worker_finished(self):
        self.start.setEnabled(True)
        self.cancel.setEnabled(False)

    def show_selected(self, row):
        self.selection_timer.start()
        if row >= 0:
            source = self.files.item(row).data(Qt.ItemDataRole.UserRole)
            if source in self.results:
                self.current_result = self.results[source]
                self.refresh_images()

    def refresh_images(self):
        result = self.current_result
        if result is None:
            return
        def display_image(path):
            alpha_path = path.with_name(path.stem + '-alpha' + path.suffix)
            with Image.open(alpha_path if alpha_path.exists() else path) as image:
                rgba = image.convert('RGBA')
                return Image.alpha_composite(Image.new('RGBA', rgba.size, self.effective_preview_colour()), rgba).convert('RGB')
        reference = display_image(result.reference)
        preview = display_image(result.preview)
        views = [self.original_view, self.converted_view, self.overlay_view]
        centers = [view.mapToScene(view.viewport().rect().center()) for view in views]
        self.syncing = True
        try:
            self.original_view.set_images(reference)
            if self.diff.isChecked():
                difference = ImageEnhance.Brightness(ImageChops.difference(reference, preview)).enhance(4)
                self.converted_view.set_images(difference)
            else:
                self.converted_view.set_images(preview)
            self.overlay_view.set_images(reference, preview)
            for view, center in zip(views, centers):
                view.setBackgroundBrush(QColor(self.effective_preview_colour()))
                view.set_zoom(self.zoom)
                view.centerOn(center)
            self.update_blend(self.blend.value())
        finally:
            self.syncing = False
        metrics = result.metrics
        changed = metrics.get('changed_fraction', 0)
        alert = 'SUBSTANTIAL DIFFERENCES — inspect the enlarged views. ' if metrics.get('substantial') else ''
        self.report.setText(f'{alert}{result.output.name}\n'
                            f'{result.width} × {result.height} px; {result.dpi:g} DPI; {result.path_count:,} vector shapes. '
                            f'Mean absolute RGB error: {metrics.get("mae", 0):.3f}/255; '
                            f'maximum: {metrics.get("max_error", 0)}; pixels with >8/255 channel error: {changed:.2%}.\n'
                            'Metrics use the conversion-time comparison background. Inspect holes, fine lines, seams, lettering and margins.\n'
                            + '\n'.join(result.warnings))

    def update_blend(self, value):
        if len(self.overlay_view.items) > 1:
            self.overlay_view.items[1].setOpacity(value / 100)

    def change_zoom(self, factor):
        self.set_zoom(self.zoom * factor)

    def set_zoom(self, zoom):
        self.zoom = max(.01, min(64., zoom))
        self.syncing = True
        for view in [self.original_view, self.converted_view, self.overlay_view]:
            view.set_zoom(self.zoom)
        self.syncing = False
        self.zoom_label.setText(f'{self.zoom * 100:.0f}%')

    def fit_preview(self):
        rect = self.original_view.sceneRect()
        if rect.width() and rect.height():
            viewport = self.original_view.viewport().rect()
            self.set_zoom(min(viewport.width() / rect.width(), viewport.height() / rect.height()) * .95)
            for view in [self.original_view, self.converted_view, self.overlay_view]:
                view.centerOn(rect.center())

    def sync_pan(self, source):
        if self.syncing:
            return
        self.syncing = True
        center = source.mapToScene(source.viewport().rect().center())
        for view in [self.original_view, self.converted_view, self.overlay_view]:
            if view is not source:
                view.centerOn(center)
        self.syncing = False

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.cancel_conversion()
            self.status.setText('Cancellation requested. Close again after the worker stops.')
            event.ignore()
            return
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('PNG Vector')
    window = MainWindow()
    window.show()
    return app.exec()
