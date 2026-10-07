"""The split-point model: the single source of truth for segments."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

# Two split points closer together than this are treated as the same point.
MIN_SPLIT_GAP = 0.05
# Segments shorter than this are not worth exporting.
MIN_SEGMENT_LEN = 0.1


@dataclass(frozen=True)
class Segment:
    """One output clip: [start, end) in seconds."""

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class SplitModel(QObject):
    """Ordered split points over a clip of known duration.

    Split points are stored as plain seconds. Segments are derived, never
    stored, so manual splits and the Quick Split modes share one pipeline.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._duration = 0.0
        self._splits: list[float] = []
        self._undo: list[list[float]] = []
        self._redo: list[list[float]] = []

    # -- clip ---------------------------------------------------------
    @property
    def duration(self) -> float:
        return self._duration

    def set_duration(self, duration: float) -> None:
        """Load a new clip; clears all splits and history."""
        self._duration = max(0.0, float(duration))
        self._splits = []
        self._undo = []
        self._redo = []
        self.changed.emit()

    # -- split points -------------------------------------------------
    @property
    def splits(self) -> list[float]:
        return list(self._splits)

    def count(self) -> int:
        return len(self._splits)

    def _push_undo(self) -> None:
        self._undo.append(list(self._splits))
        del self._undo[:-200]
        self._redo.clear()

    def can_add(self, time_s: float) -> bool:
        """True if a split at `time_s` would create two usable segments."""
        if self._duration <= 0:
            return False
        if time_s <= MIN_SEGMENT_LEN or time_s >= self._duration - MIN_SEGMENT_LEN:
            return False
        return all(abs(time_s - s) >= MIN_SPLIT_GAP for s in self._splits)

    def add_split(self, time_s: float) -> bool:
        """Insert a split point. Returns False if it was rejected."""
        time_s = float(time_s)
        if not self.can_add(time_s):
            return False
        self._push_undo()
        bisect.insort(self._splits, time_s)
        self.changed.emit()
        return True

    def set_splits(self, times) -> int:
        """Replace all split points at once (used by Quick Split)."""
        cleaned: list[float] = []
        for value in sorted(float(t) for t in times):
            if value <= MIN_SEGMENT_LEN or value >= self._duration - MIN_SEGMENT_LEN:
                continue
            if cleaned and value - cleaned[-1] < MIN_SPLIT_GAP:
                continue
            cleaned.append(value)
        self._push_undo()
        self._splits = cleaned
        self.changed.emit()
        return len(cleaned)

    def remove_split_at_index(self, index: int) -> bool:
        if not 0 <= index < len(self._splits):
            return False
        self._push_undo()
        del self._splits[index]
        self.changed.emit()
        return True

    def remove_split_near(self, time_s: float, tolerance: float) -> bool:
        """Remove the split closest to `time_s` within `tolerance` seconds."""
        index = self.nearest_index(time_s, tolerance)
        if index is None:
            return False
        return self.remove_split_at_index(index)

    def nearest_index(self, time_s: float, tolerance: float) -> int | None:
        best_index, best_delta = None, tolerance
        for i, value in enumerate(self._splits):
            delta = abs(value - time_s)
            if delta <= best_delta:
                best_index, best_delta = i, delta
        return best_index

    def move_split(self, index: int, time_s: float,
                   push_undo: bool = True) -> bool:
        """Move an existing split point, keeping it between its neighbours."""
        if not 0 <= index < len(self._splits):
            return False
        low = self._splits[index - 1] + MIN_SPLIT_GAP if index > 0 else MIN_SEGMENT_LEN
        high = (self._splits[index + 1] - MIN_SPLIT_GAP
                if index + 1 < len(self._splits)
                else self._duration - MIN_SEGMENT_LEN)
        if high <= low:
            return False
        time_s = min(max(float(time_s), low), high)
        if abs(time_s - self._splits[index]) < 1e-9:
            return False
        if push_undo:
            self._push_undo()
        self._splits[index] = time_s
        self.changed.emit()
        return True

    def clear(self) -> bool:
        if not self._splits:
            return False
        self._push_undo()
        self._splits = []
        self.changed.emit()
        return True

    # -- history ------------------------------------------------------
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(list(self._splits))
        self._splits = self._undo.pop()
        self.changed.emit()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(list(self._splits))
        self._splits = self._redo.pop()
        self.changed.emit()
        return True

    # -- derived segments ---------------------------------------------
    def segments(self, snap_fn=None) -> list[Segment]:
        """Segments implied by the current split points.

        `snap_fn` (used in fast mode) maps a requested split time to the
        keyframe ffmpeg will actually cut on, so the segment list shows
        the real boundaries rather than the requested ones.
        """
        if self._duration <= 0:
            return []
        points = self._splits
        if snap_fn is not None:
            snapped: list[float] = []
            for value in points:
                mapped = float(snap_fn(value))
                mapped = min(max(mapped, 0.0), self._duration)
                if snapped and mapped - snapped[-1] < MIN_SEGMENT_LEN:
                    continue
                snapped.append(mapped)
            points = snapped

        bounds = [0.0, *points, self._duration]
        result: list[Segment] = []
        for i in range(len(bounds) - 1):
            start, end = bounds[i], bounds[i + 1]
            if end - start < MIN_SEGMENT_LEN:
                continue
            result.append(Segment(index=len(result) + 1, start=start, end=end))
        return result
