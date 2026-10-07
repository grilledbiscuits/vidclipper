"""Quick Split panel - bulk split-point generators.

These are conveniences layered on top of manual splitting: they only
produce split points, which then flow through the same segment list and
export pipeline as clicking "Add Split".
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout,
                               QGroupBox, QLabel, QPushButton, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

MODE_EQUAL = 0
MODE_INTERVAL = 1
MODE_SIZE = 2


class QuickSplitPanel(QGroupBox):
    """Choose a bulk strategy and apply it to the current clip."""

    apply_requested = Signal(int, float)   # mode, value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Quick Split (optional)", parent)
        self.mode = QComboBox()
        self.mode.setMinimumWidth(190)
        self.mode.addItems([
            "Split into N equal parts",
            "Split every X seconds",
            "Split by max file size",
        ])

        self.parts = QSpinBox()
        self.parts.setRange(2, 500)
        self.parts.setValue(3)
        self.parts.setSuffix("  parts")

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.5, 100000.0)
        self.interval.setDecimals(1)
        self.interval.setValue(60.0)
        self.interval.setSuffix("  seconds")

        self.size_mb = QDoubleSpinBox()
        self.size_mb.setRange(0.5, 100000.0)
        self.size_mb.setDecimals(1)
        self.size_mb.setValue(25.0)
        self.size_mb.setSuffix("  MB per segment")

        self.stack = QStackedWidget()
        for widget in (self.parts, self.interval, self.size_mb):
            self.stack.addWidget(widget)
        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.mode.currentIndexChanged.connect(self._update_hint)

        self.apply_btn = QPushButton("Apply Quick Split")
        self.apply_btn.setToolTip(
            "Replaces the current split points with generated ones")
        self.apply_btn.clicked.connect(self._emit_apply)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        grid.addWidget(self.mode, 0, 0)
        grid.addWidget(self.stack, 0, 1)
        grid.addWidget(self.apply_btn, 0, 2)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addLayout(grid)
        layout.addWidget(self.hint)
        self._update_hint(0)

    def _update_hint(self, index: int) -> None:
        hints = {
            MODE_EQUAL: "Divides the clip into equal-length parts.",
            MODE_INTERVAL: "Cuts every X seconds; the last part keeps the "
                           "remainder.",
            MODE_SIZE: "Estimated from the average bitrate, so sizes vary "
                       "around the target.",
        }
        self.hint.setText(hints.get(index, ""))

    def _emit_apply(self) -> None:
        index = self.mode.currentIndex()
        value = (float(self.parts.value()) if index == MODE_EQUAL else
                 self.interval.value() if index == MODE_INTERVAL else
                 self.size_mb.value())
        self.apply_requested.emit(index, value)
