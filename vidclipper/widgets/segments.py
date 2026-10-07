"""Segment list panel: start / end / duration for every resulting clip."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QMenu,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..model import Segment
from ..timecode import fmt_tc, fmt_short, human_size
from .timeline import SEGMENT_COLORS

HEADERS = ("#", "Start", "End", "Duration", "Approx. size")


class SegmentsPanel(QWidget):
    """Read-only view of the derived segments, with seek-on-click."""

    seek_requested = Signal(float)
    segment_selected = Signal(int)
    remove_split_before = Signal(int)   # split index to remove

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self._bytes_per_second = 0.0

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.cellDoubleClicked.connect(self._on_double_click)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in range(1, len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.Stretch)

        self.summary = QLabel("No video loaded")
        self.summary.setObjectName("panelSummary")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)

    # -- data ---------------------------------------------------------
    def set_rate(self, bytes_per_second: float) -> None:
        self._bytes_per_second = max(0.0, bytes_per_second)

    def set_segments(self, segments: list[Segment], note: str = "") -> None:
        self._segments = segments
        blocked = self.table.blockSignals(True)
        self.table.setRowCount(len(segments))
        for row, seg in enumerate(segments):
            size = (f"~{human_size(seg.duration * self._bytes_per_second)}"
                    if self._bytes_per_second else "-")
            values = (str(seg.index), fmt_tc(seg.start), fmt_tc(seg.end),
                      fmt_short(seg.duration), size)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    color = SEGMENT_COLORS[(seg.index - 1) % len(SEGMENT_COLORS)]
                    item.setForeground(QColor(color))
                    item.setTextAlignment(Qt.AlignCenter)
                elif col > 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.blockSignals(blocked)

        total = sum(seg.duration for seg in segments)
        if segments:
            text = (f"{len(segments)} segment"
                    f"{'s' if len(segments) != 1 else ''}  |  "
                    f"total {fmt_short(total)}")
            if note:
                text += f"  |  {note}"
        else:
            text = "No video loaded"
        self.summary.setText(text)

    def select_index(self, index: int) -> None:
        for row in range(self.table.rowCount()):
            if self._segments[row].index == index:
                self.table.selectRow(row)
                return

    # -- interaction --------------------------------------------------
    def _on_selection(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._segments):
            self.segment_selected.emit(self._segments[row].index)

    def _on_double_click(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._segments):
            self.seek_requested.emit(self._segments[row].start)

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if not 0 <= row < len(self._segments):
            return
        menu = QMenu(self)
        seg = self._segments[row]

        go = QAction("Jump to start of this segment", self)
        go.triggered.connect(lambda: self.seek_requested.emit(seg.start))
        menu.addAction(go)

        if row > 0:
            merge = QAction("Merge with previous (remove split before)", self)
            merge.triggered.connect(
                lambda: self.remove_split_before.emit(row - 1))
            menu.addAction(merge)
        menu.exec(self.table.viewport().mapToGlobal(pos))
