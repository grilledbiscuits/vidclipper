"""VidClipper main window."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPushButton,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from . import APP_NAME, __version__
from .exporter import (ExportError, ExportSettings, ExportWorker,
                       check_free_space, plan_outputs)
from .media import (SUPPORTED_EXTS, MediaInfo, ProbeError, probe,
                    snap_to_keyframe)
from .model import SplitModel
from .naming import DEFAULT_PATTERN
from .quicksplit import by_target_size, equal_parts, every_seconds
from .scanner import KeyframeScanner
from .style import STYLESHEET
from .timecode import fmt_short, fmt_tc, human_size
from .widgets.exportpanel import ExportPanel
from .widgets.mergepanel import MergePanel
from .widgets.player import PlayerWidget
from .widgets.quicksplitpanel import (MODE_EQUAL, MODE_INTERVAL, MODE_SIZE,
                                      QuickSplitPanel)
from .widgets.segments import SegmentsPanel
from .widgets.timeline import TimelineWidget

FILE_FILTER = (
    "Video files (" + " ".join(f"*{e}" for e in SUPPORTED_EXTS) + ");;"
    "All files (*)"
)

SHORTCUT_HELP = f"""<b>{APP_NAME} keyboard shortcuts</b>
<table cellpadding='4'>
<tr><td><b>S</b></td><td>Add a split point at the playhead</td></tr>
<tr><td><b>Space</b></td><td>Play / pause</td></tr>
<tr><td><b>&larr; / &rarr;</b></td><td>Step one frame back / forward</td></tr>
<tr><td><b>Shift + &larr; / &rarr;</b></td><td>Jump one second</td></tr>
<tr><td><b>Delete</b></td><td>Remove the split point nearest the playhead</td></tr>
<tr><td><b>Home / End</b></td><td>Jump to start / end</td></tr>
<tr><td><b>Ctrl + O</b></td><td>Open a video</td></tr>
<tr><td><b>Ctrl + E</b></td><td>Export all segments</td></tr>
<tr><td><b>Ctrl + Z / Ctrl + Shift + Z</b></td><td>Undo / redo a split change</td></tr>
<tr><td><b>Ctrl + M</b></td><td>Switch to the Merge tab</td></tr>
<tr><td><b>Ctrl + 1 / Ctrl + 2</b></td><td>Split tab / Merge tab</td></tr>
</table>
<b>In the Merge tab</b>
<table cellpadding='4'>
<tr><td><b>Drag a clip</b></td><td>Change the merge order</td></tr>
<tr><td><b>Ctrl + Up / Down</b></td><td>Move the selected clip</td></tr>
<tr><td><b>Delete</b></td><td>Remove the selected clips</td></tr>
<tr><td><b>Double-click</b></td><td>Play a clip in your video player</td></tr>
</table>"""


class MainWindow(QMainWindow):
    """Ties the player, timeline, segment list and exporter together."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLESHEET)

        self.settings = QSettings("vidclipper", "VidClipper")
        self.info: MediaInfo | None = None
        self.model = SplitModel(self)
        self.scanner: KeyframeScanner | None = None
        self.worker: ExportWorker | None = None
        self._keyframes: list[float] = []

        app = QApplication.instance()
        if app is not None:
            # Quitting can bypass closeEvent (Ctrl+Q from a dialog, a
            # session logout); stop worker threads either way.
            app.aboutToQuit.connect(self.shutdown_threads)

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        self._connect()
        self._refresh()

    # -- construction -------------------------------------------------
    def _build_ui(self) -> None:
        self.open_btn = QPushButton("Open Video...")
        self.open_btn.setObjectName("primary")
        self.open_btn.setMinimumHeight(34)
        self.open_btn.setFocusPolicy(Qt.NoFocus)

        self.file_label = QLabel("No video loaded - open one or drop a file here")
        self.file_label.setObjectName("fileLabel")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("hint")

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.open_btn)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.file_label)
        titles.addWidget(self.meta_label)
        header.addLayout(titles, 1)

        self.player = PlayerWidget()
        for button in (self.player.play_btn, self.player.back_btn,
                       self.player.fwd_btn):
            button.setFocusPolicy(Qt.NoFocus)

        self.timeline = TimelineWidget(self.model)

        self.add_split_btn = QPushButton("✂  Add Split  (S)")
        self.add_split_btn.setObjectName("primary")
        self.add_split_btn.setMinimumHeight(36)
        self.remove_split_btn = QPushButton("Remove Split at Playhead")
        self.undo_btn = QPushButton("Undo")
        self.clear_btn = QPushButton("Remove All")
        for button in (self.add_split_btn, self.remove_split_btn,
                       self.undo_btn, self.clear_btn):
            button.setFocusPolicy(Qt.NoFocus)

        split_row = QHBoxLayout()
        split_row.setSpacing(8)
        split_row.addWidget(self.add_split_btn, 2)
        split_row.addWidget(self.remove_split_btn, 1)
        split_row.addWidget(self.undo_btn)
        split_row.addWidget(self.clear_btn)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.player, 1)
        left_layout.addWidget(self.timeline)
        left_layout.addLayout(split_row)

        self.segments_panel = SegmentsPanel()
        self.quick_panel = QuickSplitPanel()
        self.export_panel = ExportPanel()

        right = QWidget()
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(QLabel("Segments"))
        right_layout.addWidget(self.segments_panel, 1)
        right_layout.addWidget(self.quick_panel)
        right_layout.addWidget(self.export_panel)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([760, 480])

        split_page = QWidget()
        layout = QVBoxLayout(split_page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.splitter, 1)

        self.merge_panel = MergePanel()

        self.tabs = QTabWidget()
        self.tabs.addTab(split_page, "\u2702  Split")
        self.tabs.addTab(self.merge_panel, "\u29c9  Merge")
        self.tabs.setTabToolTip(0, "Cut one video into segments")
        self.tabs.setTabToolTip(1, "Join several clips into one video")
        self.setCentralWidget(self.tabs)

        self.statusBar().showMessage("Ready")

        last_dir = self.settings.value("out_dir", "", str)
        if last_dir and os.path.isdir(last_dir):
            self.export_panel.set_out_dir(last_dir)
        self.export_panel.pattern_edit.setCurrentText(
            self.settings.value("pattern", DEFAULT_PATTERN, str))

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.act_open = QAction("&Open Video...", self, shortcut="Ctrl+O")
        self.act_open.triggered.connect(self.choose_file)
        self.act_export = QAction("&Export All Segments", self, shortcut="Ctrl+E")
        self.act_export.triggered.connect(self.start_export)
        self.act_add_merge = QAction("Add Files to &Merge...", self)
        self.act_add_merge.triggered.connect(self.add_files_to_merge)
        act_quit = QAction("&Quit", self, shortcut="Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addActions([self.act_open, self.act_export])
        file_menu.addSeparator()
        file_menu.addAction(self.act_add_merge)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        edit_menu = self.menuBar().addMenu("&Edit")
        self.act_add = QAction("Add Split at Playhead", self, shortcut="S")
        self.act_add.triggered.connect(self.add_split)
        self.act_remove = QAction("Remove Split at Playhead", self,
                                  shortcut=QKeySequence.Delete)
        self.act_remove.triggered.connect(self.remove_split_at_playhead)
        self.act_undo = QAction("Undo", self, shortcut="Ctrl+Z")
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = QAction("Redo", self, shortcut="Ctrl+Shift+Z")
        self.act_redo.triggered.connect(self.redo)
        self.act_clear = QAction("Remove All Split Points", self)
        self.act_clear.triggered.connect(self.clear_splits)
        edit_menu.addActions([self.act_add, self.act_remove])
        edit_menu.addSeparator()
        edit_menu.addActions([self.act_undo, self.act_redo, self.act_clear])

        help_menu = self.menuBar().addMenu("&Help")
        act_keys = QAction("Keyboard Shortcuts", self, shortcut="F1")
        act_keys.triggered.connect(
            lambda: QMessageBox.information(self, "Keyboard Shortcuts",
                                            SHORTCUT_HELP))
        act_about = QAction("About VidClipper", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addActions([act_keys, act_about])

    def _build_shortcuts(self) -> None:
        def add(sequence: str, handler) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(handler)

        add("Space", self.player.toggle_play)
        add("Left", lambda: self.player.step_frames(-1))
        add("Right", lambda: self.player.step_frames(1))
        add("Shift+Left", lambda: self.player.nudge(-1.0))
        add("Shift+Right", lambda: self.player.nudge(1.0))
        add("Home", lambda: self.player.set_position(0.0))
        add("Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        add("Ctrl+2", lambda: self.tabs.setCurrentIndex(1))
        add("Ctrl+M", lambda: self.tabs.setCurrentIndex(1))
        add("End", lambda: self.player.set_position(
            max(0.0, self.model.duration - 0.1)))

    def _connect(self) -> None:
        self.open_btn.clicked.connect(self.choose_file)
        self.add_split_btn.clicked.connect(self.add_split)
        self.remove_split_btn.clicked.connect(self.remove_split_at_playhead)
        self.undo_btn.clicked.connect(self.undo)
        self.clear_btn.clicked.connect(self.clear_splits)

        self.player.position_changed.connect(self.timeline.set_position)
        self.player.error_occurred.connect(self._on_player_error)

        self.timeline.seek_requested.connect(self.player.set_position)
        self.timeline.split_removed.connect(self._remove_split_index)
        self.timeline.remove_all_requested.connect(self.clear_splits)
        self.timeline.add_split_requested.connect(self.add_split)
        self.timeline.segment_clicked.connect(self.segments_panel.select_index)

        self.segments_panel.seek_requested.connect(self.player.set_position)
        self.segments_panel.segment_selected.connect(
            self.timeline.set_selected_segment)
        self.segments_panel.remove_split_before.connect(self._remove_split_index)

        self.quick_panel.apply_requested.connect(self.apply_quick_split)
        self.export_panel.export_requested.connect(self.start_export)
        self.export_panel.cancel_requested.connect(self.cancel_export)
        self.export_panel.mode_changed.connect(lambda _: self._refresh())

        self.model.changed.connect(self._refresh)

        self.merge_panel.status_message.connect(
            lambda text: self.statusBar().showMessage(text))
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        if index != 0:
            self.player.pause()
        self.statusBar().showMessage(
            "Merge: add clips, drag them into order, then merge"
            if index == 1 else "Split: add split points, then export")

    def add_files_to_merge(self) -> None:
        """File menu entry: pick clips and jump to the Merge tab."""
        self.tabs.setCurrentIndex(1)
        self.merge_panel.choose_files()

    # -- loading ------------------------------------------------------
    def choose_file(self) -> None:
        start = self.settings.value("last_open_dir", os.path.expanduser("~"), str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", start, FILE_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        """Probe and load `path`, reporting any problem in a dialog."""
        path = os.path.abspath(os.path.expanduser(path))
        try:
            info = probe(path)
        except ProbeError as exc:
            QMessageBox.critical(self, "Cannot open this video", str(exc))
            self.statusBar().showMessage("Could not open the selected file")
            return
        except RuntimeError as exc:   # ffmpeg vanished mid-session
            QMessageBox.critical(self, "ffmpeg not available", str(exc))
            return

        self._stop_scanner()
        self.info = info
        self._keyframes = []
        self.model.set_duration(info.duration)

        self.file_label.setText(os.path.basename(path))
        rate = info.size_bytes / info.duration if info.duration else 0
        self.meta_label.setText(
            f"{info.summary()}  |  {fmt_short(info.duration)}  |  "
            f"{human_size(info.size_bytes)}")
        self.segments_panel.set_rate(rate)
        self.timeline.set_keyframes([])
        self.player.load(path, info.fps)

        if not self.export_panel.out_dir:
            self.export_panel.set_out_dir(os.path.dirname(path))
        self.settings.setValue("last_open_dir", os.path.dirname(path))
        self.setWindowTitle(f"{os.path.basename(path)} - {APP_NAME}")
        self.export_panel.reset_progress()

        self.statusBar().showMessage("Scanning keyframes...")
        self.scanner = KeyframeScanner(path, self)
        self.scanner.ready.connect(self._on_keyframes)
        self.scanner.failed.connect(self._on_keyframes_failed)
        self.scanner.start()
        self._refresh()

    def _stop_scanner(self) -> None:
        if self.scanner is not None:
            self.scanner.stop()
            self.scanner.wait(3000)
            self.scanner = None

    def _on_keyframes(self, path: str, times: list) -> None:
        if not self.info or path != self.info.path:
            return
        self._keyframes = [float(t) for t in times]
        self.info.keyframes = self._keyframes
        self.timeline.set_keyframes(self._keyframes)
        if self._keyframes:
            gaps = [b - a for a, b in zip(self._keyframes, self._keyframes[1:])]
            average = sum(gaps) / len(gaps) if gaps else 0.0
            self.statusBar().showMessage(
                f"{len(self._keyframes)} keyframes "
                f"(about one every {average:.1f}s) - fast cuts snap to these")
        else:
            self.statusBar().showMessage(
                "No keyframe index available; fast cuts may drift. "
                "Use Precise mode for exact cuts.")
        self._refresh()

    def _on_keyframes_failed(self, _path: str, message: str) -> None:
        self.statusBar().showMessage(f"Keyframe scan failed: {message}")

    def _on_player_error(self, message: str) -> None:
        QMessageBox.warning(
            self, "Preview problem",
            "The video preview could not play this file:\n\n"
            f"{message}\n\n"
            "Splitting and exporting may still work, since those use "
            "ffmpeg directly.")

    # -- split editing ------------------------------------------------
    def _require_video(self) -> bool:
        if self.info is None:
            QMessageBox.information(
                self, "No video loaded",
                "Open a video first (Ctrl+O), or drop one onto the window.")
            return False
        return True

    def add_split(self) -> None:
        if not self._require_video():
            return
        position = self.player.position()
        if self.model.add_split(position):
            self.statusBar().showMessage(
                f"Split added at {fmt_tc(position)}  "
                f"({self.model.count()} split point"
                f"{'s' if self.model.count() != 1 else ''})")
        else:
            self.statusBar().showMessage(
                "No split added here - too close to the start, the end, "
                "or an existing split point.")

    def remove_split_at_playhead(self) -> None:
        if not self._require_video():
            return
        if not self.model.count():
            self.statusBar().showMessage("There are no split points to remove.")
            return
        tolerance = max(0.5, self.model.duration * 0.02)
        if self.model.remove_split_near(self.player.position(), tolerance):
            self.statusBar().showMessage("Split point removed")
        else:
            self.statusBar().showMessage(
                "No split point near the playhead - move closer to one, "
                "or right-click it on the timeline.")

    def _remove_split_index(self, index: int) -> None:
        if self.model.remove_split_at_index(index):
            self.statusBar().showMessage("Split point removed")

    def undo(self) -> None:
        if self.model.undo():
            self.statusBar().showMessage("Undone")
        else:
            self.statusBar().showMessage("Nothing to undo")

    def redo(self) -> None:
        if self.model.redo():
            self.statusBar().showMessage("Redone")

    def clear_splits(self) -> None:
        if self.model.clear():
            self.statusBar().showMessage("All split points removed")

    def apply_quick_split(self, mode: int, value: float) -> None:
        if not self._require_video():
            return
        assert self.info is not None
        duration = self.model.duration
        if mode == MODE_EQUAL:
            points = equal_parts(duration, int(value))
            label = f"{int(value)} equal parts"
        elif mode == MODE_INTERVAL:
            points = every_seconds(duration, value)
            label = f"every {value:g}s"
        elif mode == MODE_SIZE:
            points = by_target_size(duration, self.info.size_bytes,
                                    value * 1024 * 1024)
            label = f"~{value:g} MB per segment"
        else:
            return

        if not points:
            QMessageBox.information(
                self, "Quick Split",
                "That setting produces a single segment - the clip is "
                "already shorter than one part.\n\n"
                "Try a smaller interval, more parts, or a smaller target "
                "size.")
            return

        count = self.model.set_splits(points)
        self.statusBar().showMessage(
            f"Quick Split: {label} - {count + 1} segments "
            "(edit or remove any marker before exporting)")

    # -- refresh ------------------------------------------------------
    def _snap_fn(self):
        """Snapping function for fast mode, or None when re-encoding."""
        if self.export_panel.precise or not self._keyframes:
            return None
        return lambda t: snap_to_keyframe(t, self._keyframes)

    def _refresh(self) -> None:
        segments = self.model.segments(self._snap_fn())
        note = ""
        if self.export_panel.precise:
            note = "precise cuts"
        elif self._keyframes:
            note = "keyframe-snapped"
        self.segments_panel.set_segments(segments, note)
        self.timeline.set_segments(segments)
        self.timeline.set_show_keyframes(not self.export_panel.precise)

        has_video = self.info is not None
        busy = self.worker is not None and self.worker.isRunning()
        self.add_split_btn.setEnabled(has_video and not busy)
        self.remove_split_btn.setEnabled(has_video and self.model.count() > 0)
        self.undo_btn.setEnabled(self.model.can_undo())
        self.clear_btn.setEnabled(self.model.count() > 0)
        self.act_export.setEnabled(has_video and not busy)
        self.quick_panel.setEnabled(has_video and not busy)
        self.export_panel.export_btn.setEnabled(has_video)

    # -- export -------------------------------------------------------
    def _settings_for_export(self) -> ExportSettings:
        assert self.info is not None
        return ExportSettings(
            source_path=self.info.path,
            out_dir=self.export_panel.out_dir,
            pattern=self.export_panel.pattern,
            precise=self.export_panel.precise,
            source_ext=self.info.ext,
            source_bytes=self.info.size_bytes,
            source_duration=self.info.duration,
            has_audio=self.info.has_audio,
            overwrite=self.export_panel.overwrite_enabled,
        )

    def start_export(self) -> None:
        if not self._require_video():
            return
        if self.worker is not None and self.worker.isRunning():
            return

        segments = self.model.segments(self._snap_fn())
        if self.model.count() == 0:
            answer = QMessageBox.question(
                self, "No split points",
                "There are no split points, so this exports one file "
                "containing the whole video.\n\nExport anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        settings = self._settings_for_export()
        try:
            plan = plan_outputs(segments, settings)
            check_free_space(segments, settings)
        except ExportError as exc:
            QMessageBox.warning(self, "Cannot export", str(exc))
            return
        except ValueError as exc:      # bad filename pattern
            QMessageBox.warning(self, "Filename pattern problem", str(exc))
            return

        self.settings.setValue("out_dir", settings.out_dir)
        self.settings.setValue("pattern", settings.pattern)
        self.player.pause()

        self.worker = ExportWorker(plan, settings, self)
        self.worker.progress.connect(self.export_panel.set_progress)
        self.worker.finished_all.connect(self._on_export_finished)
        self.export_panel.set_busy(True)
        self.export_panel.set_progress(
            0.0, f"Exporting {len(plan)} segment"
                 f"{'s' if len(plan) != 1 else ''}"
                 f" ({'precise re-encode' if settings.precise else 'fast copy'})...")
        self._refresh()
        self.worker.start()

    def cancel_export(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.export_panel.set_progress(
                self.export_panel.progress.value(), "Cancelling...")
            self.worker.cancel()

    def _on_export_finished(self, result) -> None:
        self.export_panel.set_busy(False)
        self.worker = None
        self._refresh()

        written, errors = result.written, result.errors
        if result.cancelled:
            self.export_panel.set_progress(
                0.0, f"Cancelled - {len(written)} file(s) kept")
            QMessageBox.information(
                self, "Export cancelled",
                f"Export was cancelled.\n\n{len(written)} finished file(s) "
                "were kept; the partially written one was deleted.")
            return

        if errors and not written:
            self.export_panel.set_progress(0.0, "Export failed")
            QMessageBox.critical(
                self, "Export failed",
                "No files were written.\n\n" + "\n".join(errors[:8]))
            return

        if errors:
            self.export_panel.set_progress(
                100.0, f"{len(written)} exported, {len(errors)} failed")
            QMessageBox.warning(
                self, "Export finished with problems",
                f"{len(written)} file(s) exported, {len(errors)} failed:\n\n"
                + "\n".join(errors[:8]))
            return

        total = sum(os.path.getsize(p) for p in written if os.path.exists(p))
        self.export_panel.set_progress(
            100.0, f"Exported {len(written)} file(s) - {human_size(total)}")
        names = "\n".join(os.path.basename(p) for p in written[:12])
        if len(written) > 12:
            names += f"\n... and {len(written) - 12} more"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Export complete")
        box.setText(f"Exported {len(written)} segment(s) to:\n"
                    f"{self.export_panel.out_dir}")
        box.setDetailedText(names)
        open_button = box.addButton("Open Folder", QMessageBox.ActionRole)
        merge_button = box.addButton("Send to Merge", QMessageBox.ActionRole)
        merge_button.setToolTip(
            "Load these segments into the Merge tab, ready to reorder")
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(self.export_panel.out_dir))
        elif box.clickedButton() is merge_button:
            self.send_to_merge(written)

    def send_to_merge(self, paths: list[str]) -> None:
        """Load freshly exported segments into the Merge tab."""
        self.tabs.setCurrentIndex(1)
        self.merge_panel.add_paths(paths)

    # -- drag and drop ------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event)
        if not paths:
            return
        event.acceptProposedAction()
        # Dropping onto the Merge tab adds every file; the Split tab
        # works on one video at a time.
        if self.tabs.currentIndex() == 1:
            self.merge_panel.add_paths(paths)
        elif len(paths) > 1:
            answer = QMessageBox.question(
                self, "Several files dropped",
                f"You dropped {len(paths)} video files.\n\n"
                "Add them all to the Merge tab, or open just the first one "
                "for splitting?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self.tabs.setCurrentIndex(1)
                self.merge_panel.add_paths(paths)
            else:
                self.load_file(paths[0])
        else:
            self.load_file(paths[0])

    @staticmethod
    def _dropped_paths(event) -> list[str]:
        data = event.mimeData()
        if not data.hasUrls():
            return []
        paths = []
        for url in data.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if os.path.splitext(path)[1].lower() in SUPPORTED_EXTS:
                paths.append(path)
        return paths

    @staticmethod
    def _dropped_path(event) -> str | None:
        """First droppable video, or None (kept for the Split tab)."""
        paths = MainWindow._dropped_paths(event)
        return paths[0] if paths else None

    # -- misc ---------------------------------------------------------
    def show_about(self) -> None:
        from .media import ffmpeg_version

        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>"
            "A fast, fully local video splitter.<br><br>"
            "Everything runs on this machine: no uploads, no accounts, "
            "no telemetry.<br><br>"
            f"<small>{ffmpeg_version() or 'ffmpeg not found'}</small>")

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Export in progress",
                "An export is still running. Cancel it and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        merge_worker = self.merge_panel.worker
        if merge_worker is not None and merge_worker.isRunning():
            answer = QMessageBox.question(
                self, "Merge in progress",
                "A merge is still running. Cancel it and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.shutdown_threads()
        self.player.clear()
        event.accept()

    def shutdown_threads(self) -> None:
        """Stop every background thread and wait for it to finish."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
            self.worker = None
        self.merge_panel.shutdown()
        self._stop_scanner()
