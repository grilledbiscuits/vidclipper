"""Output filename pattern handling."""

from __future__ import annotations

import os
import re

DEFAULT_PATTERN = "{name}_part{n:02d}{ext}"

PATTERN_HELP = (
    "Placeholders:  {name} original filename  |  {n} segment number  |  "
    "{ext} output extension  |  {start} start timecode  |  "
    "{dur} duration in seconds\n"
    "Use {n:02d} for zero-padded numbers (part01, part02...)."
)

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    """Strip path separators and characters that break on common filesystems."""
    name = _ILLEGAL.sub("_", name).strip().strip(".")
    return name or "segment"


def format_name(pattern: str, *, name: str, n: int, ext: str,
                start: float = 0.0, duration: float = 0.0) -> str:
    """Render one output filename from `pattern`.

    Raises ValueError with a readable message for a malformed pattern.
    """
    from .timecode import fmt_tc

    pattern = (pattern or DEFAULT_PATTERN).strip() or DEFAULT_PATTERN
    fields = {
        "name": name,
        "n": n,
        "ext": ext,
        "start": fmt_tc(start, ms=False).replace(":", "-"),
        "dur": f"{duration:.1f}",
    }
    try:
        rendered = pattern.format(**fields)
    except KeyError as exc:
        raise ValueError(f"Unknown placeholder {exc} in the filename pattern.") from exc
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Malformed filename pattern: {exc}") from exc

    rendered = sanitize(rendered)
    if not os.path.splitext(rendered)[1]:
        rendered += ext
    return rendered


def unique_path(directory: str, filename: str) -> str:
    """Return a path in `directory` that does not collide with an existing file."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{counter}{ext}")
        counter += 1
    return candidate
