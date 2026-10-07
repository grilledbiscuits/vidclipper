"""Timecode formatting and parsing helpers."""

from __future__ import annotations


def fmt_tc(seconds: float, ms: bool = True) -> str:
    """Format seconds as HH:MM:SS.mmm (or HH:MM:SS)."""
    if seconds is None:
        return "--:--:--"
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if ms:
        return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def fmt_short(seconds: float) -> str:
    """Compact duration, e.g. 1:03.5 or 12:04:07."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}"
    return f"{int(minutes)}:{secs:04.1f}"


def parse_tc(text: str) -> float | None:
    """Parse HH:MM:SS.mmm / MM:SS / SS into seconds. None if unparseable."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60.0 + part
    return total


def human_size(num_bytes: float) -> str:
    """Format a byte count for display."""
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < step or unit == "TB":
            if unit == "B":
                return f"{int(num_bytes)} B"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} TB"
