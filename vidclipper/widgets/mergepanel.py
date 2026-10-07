"""The Merge tab: collect clips, order them, join them into one file."""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ..media import SUPPORTED_EXTS, MediaInfo, ProbeError, probe
from ..merger import (MergeError, MergeSettings, MergeWorker,
                      compatibility_problems, merge_extension,
                      suggested_output_name, validate)
from ..thumbnails import ThumbnailWorker
from ..timecode import fmt_short, human_size
from .cliplist import ClipListWidget

FILE_FILTER = (
    "Video files (" + " ".join(f"*{e}" for e in SUPPORTED_EXTS) + ");;"
    "All files (*)"
)


class MergePanel(QWidget):
    """Mirror of the split flow: many clips in, one video out."""

    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker: MergeWorker | None = None
        self.thumbs: ThumbnailWorker | None = None
        self._last_dir = os.path.expanduser("~")

        self._build_ui()
        self._connect()
        self._refresh()

    # -- construction -------------------------------------------------
    def _build_ui(self) -> None:
        intro = QLabel(
            "Add the clips you want to join, then drag them into the right "
            "order. They are merged top to bottom.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)

        self.list = ClipListWidget()

        self.add_btn = QPushButton("Add Files...")
        self.add_btn.setObjectName("primary")
        self.remove_btn = QPushButton("Remove")
        self.up_btn = QPushButton("↑  Move Up")
        self.down_btn = QPushButton("↓  Move Down")
        self.sort_btn = QPushButton("Sort A→Z")
        self.reverse_btn = QPushButton("Reverse")
        self.clear_btn = QPushButton("Clear All")
        self.up_btn.setToolTip("Ctrl+Up")
        self.down_btn.setToolTip("Ctrl+Down")
        self.remove_btn.setToolTip("Delete")

        side = QVBoxLayout()
        side.setSpacing(6)
        for button in (self.add_btn, self.remove_btn, self.up_btn,
                       self.down_btn, self.sort_btn, self.reverse_btn,
                       self.clear_btn):
            button.setFocusPolicy(Qt.NoFocus)
            side.addWidget(button)
        side.addStretch(1)

        list_row = QHBoxLayout()
        list_row.setSpacing(10)
        list_row.addWidget(self.list, 1)
        list_row.addLayout(side)

        self.summary = QLabel("No clips added yet")
        self.summary.setObjectName("panelSummary")

        # -- output box ------------------------------------------------
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("Choose an output folder...")
        self.browse_btn = QPushButton("Browse...")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("merged.mp4")

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_row.addWidget(QLabel("Save to:"))
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.browse_btn)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(QLabel("Filename:"))
        name_row.addWidget(self.name_edit, 1)

        self.fast_radio = QRadioButton("Fast (join streams, lossless)")
        self.fast_radio.setChecked(True)
        self.fast_radio.setToolTip(
            "Copies the clips end to end without re-encoding.\n"
            "Only works when every clip shares the same format.")
        self.precise_radio = QRadioButton("Precise (re-encode to match)")
        self.precise_radio.setToolTip(
            "Re-encodes everything to one common format, so clips with\n"
            "different resolutions, codecs or audio can be joined.\n"
            "Slower, and slightly lossy.")

        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        mode_row.addWidget(QLabel("Merge mode:"))
        mode_row.addWidget(self.fast_radio)
        mode_row.addWidget(self.precise_radio)
        mode_row.addStretch(1)

        self.compat = QLabel()
        self.compat.setObjectName("hint")
        self.compat.setWordWrap(True)

        self.merge_btn = QPushButton("Merge Into One Video")
        self.merge_btn.setObjectName("primary")
        self.merge_btn.setMinimumHeight(38)
        self.open_btn = QPushButton("Open Output Folder")

        button_row = QHBoxLayout()
        button_row.addWidget(self.merge_btn, 2)
        button_row.addWidget(self.open_btn, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.status = QLabel("Ready")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)

        output_box = QGroupBox("Output")
        output_layout = QVBoxLayout(output_box)
        output_layout.setSpacing(8)
        output_layout.addLayout(dir_row)
        output_layout.addLayout(name_row)
        output_layout.addLayout(mode_row)
        output_layout.addWidget(self.compat)
        output_layout.addLayout(button_row)
        output_layout.addWidget(self.progress)
        output_layout.addWidget(self.status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(intro)
        layout.addLayout(list_row, 1)
        layout.addWidget(self.summary)
        layout.addWidget(output_box)

    def _connect(self) -> None:
        self.add_btn.clicked.connect(self.choose_files)
        self.remove_btn.clicked.connect(self.list.remove_selected)
        self.up_btn.clicked.connect(lambda: self.list.move_selected(-1))
        self.down_btn.clicked.connect(lambda: self.list.move_selected(1))
        self.sort_btn.clicked.connect(self.list.sort_by_name)
        self.reverse_btn.clicked.connect(self.list.reverse_order)
        self.clear_btn.clicked.connect(self.clear)

        self.list.files_dropped.connect(self.add_paths)
        self.list.order_changed.connect(self._on_order_changed)
        self.list.preview_requested.connect(self._play_externally)
        self.list.itemSelectionChanged.connect(self._refresh)

        self.browse_btn.clicked.connect(self._choose_dir)
        self.open_btn.clicked.connect(self._open_dir)
        self.merge_btn.clicked.connect(self._on_merge_clicked)
        self.fast_radio.toggled.connect(self._on_mode_changed)

    # -- clips --------------------------------------------------------
    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add clips to merge", self._last_dir, FILE_FILTER)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths) -> None:
        """Probe and append clips, reporting anything unreadable."""
        added, failed = 0, []
        for path in paths:
            path = os.path.abspath(os.path.expanduser(str(path)))
            try:
                info = probe(path)
            except (ProbeError, RuntimeError) as exc:
                failed.append(f"{os.path.basename(path)}: "
                              f"{str(exc).splitlines()[0]}")
                continue
            self.list.add_clip(info)
            self._queue_thumbnail(info)
            added += 1
            self._last_dir = os.path.dirname(path)

        self.list.renumber()
        self._on_order_changed()

        if added:
            self.status_message.emit(
                f"Added {added} clip{'s' if added != 1 else ''} to the merge list")
        if failed:
            QMessageBox.warning(
                self, "Some files could not be added",
                "These files were skipped:\n\n" + "\n".join(failed[:8]))

    def add_clip_infos(self, infos: list[MediaInfo]) -> None:
        """Append clips that have already been probed (used after a split)."""
        for info in infos:
            self.list.add_clip(info)
            self._queue_thumbnail(info)
        self.list.renumber()
        self._on_order_changed()

    def clear(self) -> None:
        self.list.clear()
        self._on_order_changed()

    def _queue_thumbnail(self, info: MediaInfo) -> None:
        if self.thumbs is None:
            self.thumbs = ThumbnailWorker(self)
            self.thumbs.ready.connect(self.list.set_thumbnail)
        self.thumbs.enqueue(info.path, info.duration)
        if not self.thumbs.isRunning():
            self.thumbs.start()

    # -- state --------------------------------------------------------
    @property
    def precise(self) -> bool:
        return self.precise_radio.isChecked()

    def clips(self) -> list[MediaInfo]:
        return self.list.clips()

    def out_path(self) -> str:
        directory = self.dir_edit.text().strip()
        name = self.name_edit.text().strip()
        if not directory or not name:
            return ""
        return os.path.join(directory, name)

    def _on_order_changed(self) -> None:
        self._suggest_output()
        self._refresh()

    def _on_mode_changed(self) -> None:
        # Keep the extension honest when the mode changes the container.
        clips = self.clips()
        name = self.name_edit.text().strip()
        if clips and name:
            wanted = merge_extension(clips, self.precise)
            base, ext = os.path.splitext(name)
            if ext.lower() != wanted:
                self.name_edit.setText(base + wanted)
        self._refresh()

    def _suggest_output(self) -> None:
        clips = self.clips()
        if not clips:
            return
        if not self.dir_edit.text().strip():
            self.dir_edit.setText(os.path.dirname(clips[0].path))
        if not self.name_edit.text().strip():
            self.name_edit.setText(suggested_output_name(clips, self.precise))

    def _refresh(self) -> None:
        clips = self.clips()
        count = len(clips)
        busy = self.worker is not None and self.worker.isRunning()

        if clips:
            total = sum(c.duration for c in clips)
            size = sum(c.size_bytes for c in clips)
            self.summary.setText(
                f"{count} clip{'s' if count != 1 else ''}  |  "
                f"total {fmt_short(total)}  |  {human_size(size)}")
        else:
            self.summary.setText("No clips added yet")

        problems = compatibility_problems(clips) if count >= 2 else []
        if count < 2:
            self.compat.setText(
                "Add at least two clips. Drag video files straight onto the "
                "list, or use Add Files.")
        elif not problems:
            self.compat.setText(
                "✓  These clips share one format, so Fast mode will join "
                "them losslessly in seconds.")
        elif self.precise:
            self.compat.setText(
                "These clips differ (" + problems[0].lower() +
                (", and more" if len(problems) > 1 else "") +
                "). Precise mode will re-encode them to match.")
        else:
            self.compat.setText(
                "⚠  Fast mode cannot join these: " + problems[0].lower() +
                (f", plus {len(problems) - 1} other difference"
                 f"{'s' if len(problems) > 2 else ''}" if len(problems) > 1
                 else "") +
                ".  Switch to Precise mode to merge them anyway.")

        has_selection = bool(self.list.selectedItems())
        self.remove_btn.setEnabled(has_selection and not busy)
        self.up_btn.setEnabled(has_selection and not busy)
        self.down_btn.setEnabled(has_selection and not busy)
        self.sort_btn.setEnabled(count > 1 and not busy)
        self.reverse_btn.setEnabled(count > 1 and not busy)
        self.clear_btn.setEnabled(count > 0 and not busy)
        self.add_btn.setEnabled(not busy)
        self.merge_btn.setEnabled(count >= 2 or busy)
        self.list.setEnabled(not busy)

    def set_busy(self, busy: bool) -> None:
        self.merge_btn.setText("Cancel Merge" if busy else "Merge Into One Video")
        for widget in (self.dir_edit, self.name_edit, self.browse_btn,
                       self.fast_radio, self.precise_radio):
            widget.setEnabled(not busy)
        self._refresh()

    # -- merging ------------------------------------------------------
    def _on_merge_clicked(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Cancelling...")
            self.worker.cancel()
            return
        self.start_merge()

    def start_merge(self) -> None:
        clips = self.clips()
        settings = MergeSettings(clips=clips, out_path=self.out_path(),
                                 precise=self.precise)
        try:
            validate(settings)
        except MergeError as exc:
            QMessageBox.warning(self, "Cannot merge", str(exc))
            return

        if os.path.exists(settings.out_path):
            answer = QMessageBox.question(
                self, "File already exists",
                f"{os.path.basename(settings.out_path)} already exists in "
                "that folder.\n\nReplace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        self.worker = MergeWorker(settings, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_merge.connect(self._on_finished)
        self.set_busy(True)
        self.progress.setValue(0)
        self.worker.start()

    def _on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(int(round(percent)))
        self.status.setText(message)

    def _on_finished(self, result) -> None:
        self.worker = None
        self.set_busy(False)

        if result.cancelled:
            self.progress.setValue(0)
            self.status.setText("Merge cancelled")
            self.status_message.emit("Merge cancelled")
            return

        if result.error:
            self.progress.setValue(0)
            self.status.setText("Merge failed")
            QMessageBox.critical(
                self, "Merge failed",
                "The clips could not be merged.\n\n" + result.error)
            return

        size = os.path.getsize(result.out_path)
        self.progress.setValue(100)
        self.status.setText(
            f"Merged into {os.path.basename(result.out_path)}  "
            f"({human_size(size)})")
        self.status_message.emit("Merge complete")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Merge complete")
        box.setText(f"{len(self.clips())} clips were merged into:\n"
                    f"{result.out_path}")
        play_button = box.addButton("Play", QMessageBox.ActionRole)
        open_button = box.addButton("Open Folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is play_button:
            self._play_externally(result.out_path)
        elif box.clickedButton() is open_button:
            self._open_dir()

    # -- helpers ------------------------------------------------------
    def _choose_dir(self) -> None:
        start = self.dir_edit.text().strip() or self._last_dir
        path = QFileDialog.getExistingDirectory(
            self, "Choose output folder", start)
        if path:
            self.dir_edit.setText(path)
            self._refresh()

    def _open_dir(self) -> None:
        path = self.dir_edit.text().strip()
        if not path or not os.path.isdir(path):
            QMessageBox.information(
                self, "No output folder",
                "Choose an existing output folder first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _play_externally(self, path: str) -> None:
        if os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def shutdown(self) -> None:
        """Stop background work before the window closes."""
        if self.thumbs is not None:
            self.thumbs.stop()
            self.thumbs.wait(2000)
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
