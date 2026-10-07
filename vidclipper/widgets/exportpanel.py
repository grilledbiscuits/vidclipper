"""Export panel: destination, filename pattern, split mode and progress."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

from ..naming import DEFAULT_PATTERN, PATTERN_HELP

PATTERNS = (
    DEFAULT_PATTERN,
    "{name}_{n:02d}{ext}",
    "{name}_segment{n}{ext}",
    "{name}_{start}{ext}",
    "part{n:03d}{ext}",
)


class ExportPanel(QGroupBox):
    """Everything needed to turn the segment list into files."""

    export_requested = Signal()
    cancel_requested = Signal()
    mode_changed = Signal(bool)          # True = precise

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Export", parent)
        self._busy = False

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("Choose an output folder...")
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._choose_dir)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.browse_btn)

        self.pattern_edit = QComboBox()
        self.pattern_edit.setEditable(True)
        self.pattern_edit.addItems(PATTERNS)
        self.pattern_edit.setCurrentText(DEFAULT_PATTERN)
        self.pattern_edit.setToolTip(PATTERN_HELP)

        self.fast_radio = QRadioButton("Fast (stream copy, lossless)")
        self.fast_radio.setChecked(True)
        self.fast_radio.setToolTip(
            "Copies the existing streams - near-instant and lossless.\n"
            "Cuts snap to the nearest keyframe, so they can land a\n"
            "fraction of a second away from the marker.")
        self.precise_radio = QRadioButton("Precise (re-encode, exact frames)")
        self.precise_radio.setToolTip(
            "Re-encodes to H.264/AAC so every cut lands on the exact\n"
            "frame you marked. Much slower and slightly lossy.")
        self.fast_radio.toggled.connect(
            lambda checked: self.mode_changed.emit(not checked))

        self.overwrite = QCheckBox("Overwrite existing files")
        self.overwrite.setToolTip(
            "Off: a number is appended instead of replacing a file.")

        self.export_btn = QPushButton("Export All Segments")
        self.export_btn.setObjectName("primary")
        self.export_btn.setMinimumHeight(38)
        self.export_btn.clicked.connect(self._on_export_clicked)

        self.open_btn = QPushButton("Open Output Folder")
        self.open_btn.clicked.connect(self._open_dir)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")

        self.status = QLabel("Ready")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("Output folder:", dir_row)
        form.addRow("Filename pattern:", self.pattern_edit)

        mode_box = QVBoxLayout()
        mode_box.setSpacing(2)
        mode_box.addWidget(self.fast_radio)
        mode_box.addWidget(self.precise_radio)
        form.addRow("Split mode:", mode_box)
        form.addRow("", self.overwrite)

        buttons = QHBoxLayout()
        buttons.addWidget(self.export_btn, 2)
        buttons.addWidget(self.open_btn, 1)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

    # -- state --------------------------------------------------------
    @property
    def precise(self) -> bool:
        return self.precise_radio.isChecked()

    @property
    def out_dir(self) -> str:
        return self.dir_edit.text().strip()

    def set_out_dir(self, path: str) -> None:
        self.dir_edit.setText(path)

    @property
    def pattern(self) -> str:
        return self.pattern_edit.currentText().strip()

    @property
    def overwrite_enabled(self) -> bool:
        return self.overwrite.isChecked()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.export_btn.setText("Cancel Export" if busy else "Export All Segments")
        for widget in (self.dir_edit, self.browse_btn, self.pattern_edit,
                       self.fast_radio, self.precise_radio, self.overwrite):
            widget.setEnabled(not busy)

    def set_progress(self, percent: float, message: str = "") -> None:
        self.progress.setValue(int(round(percent)))
        if message:
            self.status.setText(message)

    def reset_progress(self) -> None:
        self.progress.setValue(0)
        self.status.setText("Ready")

    # -- actions ------------------------------------------------------
    def _on_export_clicked(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        else:
            self.export_requested.emit()

    def _choose_dir(self) -> None:
        start = self.out_dir or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "Choose output folder", start)
        if path:
            self.dir_edit.setText(path)

    def _open_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        path = self.out_dir
        if not path or not os.path.isdir(path):
            QMessageBox.information(
                self, "No output folder",
                "Choose an existing output folder first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
