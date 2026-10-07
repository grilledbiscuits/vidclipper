"""Quick Split strategies.

Each function returns a list of split *points* (seconds). They feed the
exact same SplitModel as manual clicking, so every mode shares one
segment/export pipeline.
"""

from __future__ import annotations

from .model import MIN_SEGMENT_LEN


def equal_parts(duration: float, parts: int) -> list[float]:
    """Split into `parts` equal-length pieces."""
    if duration <= 0 or parts < 2:
        return []
    step = duration / parts
    if step < MIN_SEGMENT_LEN:
        return []
    return [step * i for i in range(1, parts)]


def every_seconds(duration: float, interval: float) -> list[float]:
    """Split every `interval` seconds; the last piece keeps the remainder."""
    if duration <= 0 or interval < MIN_SEGMENT_LEN:
        return []
    points: list[float] = []
    value = interval
    while value < duration - MIN_SEGMENT_LEN:
        points.append(value)
        value += interval
    return points


def by_target_size(duration: float, total_bytes: float,
                   target_bytes: float) -> list[float]:
    """Split so each segment is roughly `target_bytes` or smaller.

    This uses the file's average bitrate, so it is an estimate: variable
    bitrate content will vary around the target.
    """
    if duration <= 0 or total_bytes <= 0 or target_bytes <= 0:
        return []
    if target_bytes >= total_bytes:
        return []
    bytes_per_second = total_bytes / duration
    interval = target_bytes / bytes_per_second
    if interval < MIN_SEGMENT_LEN:
        return []
    return every_seconds(duration, interval)


def estimated_part_count(duration: float, points: list[float]) -> int:
    """How many segments a set of split points produces."""
    if duration <= 0:
        return 0
    return len(points) + 1
