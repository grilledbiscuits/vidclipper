"""ffmpeg/ffprobe discovery, probing and keyframe scanning.

Everything here shells out to the *system* ffmpeg/ffprobe binaries; no
network access and no third-party media libraries are involved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

# Containers we accept in the file picker / drag-and-drop.
SUPPORTED_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")

# Containers that can hold the H.264 + AAC streams produced by precise mode.
PRECISE_SAFE_EXTS = (".mp4", ".mov", ".mkv", ".m4v")

# Hide console windows / banners on every call.
_QUIET = ("-hide_banner", "-loglevel", "error")


class FFmpegMissingError(RuntimeError):
    """Raised when ffmpeg and/or ffprobe cannot be found on PATH."""


class ProbeError(RuntimeError):
    """Raised when a file cannot be read as a playable video."""


def _no_window_kwargs() -> dict:
    kwargs: dict = {}
    if os.name == "nt":  # pragma: no cover - Linux is the target platform
        kwargs["creationflags"] = 0x08000000
    return kwargs


def find_binaries() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) paths, raising FFmpegMissingError if absent."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
    if missing:
        raise FFmpegMissingError(
            "Missing required program(s): " + ", ".join(missing)
        )
    return ffmpeg, ffprobe


def ffmpeg_version() -> str:
    """Short version string for the About box; '' if unavailable."""
    try:
        ffmpeg, _ = find_binaries()
    except FFmpegMissingError:
        return ""
    try:
        out = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            **_no_window_kwargs(),
        ).stdout
        return out.splitlines()[0] if out else ""
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class MediaInfo:
    """Everything the UI needs to know about the loaded file."""

    path: str
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 25.0
    video_codec: str = ""
    pix_fmt: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    container: str = ""
    size_bytes: int = 0
    bit_rate: float = 0.0
    has_audio: bool = False
    keyframes: list[float] = field(default_factory=list)

    @property
    def basename(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower() or ".mp4"

    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps if self.fps > 0 else 1.0 / 25.0

    @property
    def stream_signature(self) -> tuple:
        """What must match for two clips to concatenate losslessly."""
        return (self.video_codec, self.width, self.height, self.pix_fmt,
                self.audio_codec, self.sample_rate, self.channels)

    def summary(self) -> str:
        bits = [f"{self.width}x{self.height}", f"{self.fps:.3g} fps"]
        if self.video_codec:
            bits.append(self.video_codec)
        bits.append(self.audio_codec if self.has_audio else "no audio")
        return "  |  ".join(bits)


def _parse_fraction(value: str) -> float:
    """Parse ffprobe rationals such as '30000/1001'."""
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str) -> MediaInfo:
    """Read stream/format metadata for `path`.

    Raises ProbeError with a human-readable message for unreadable,
    corrupt or audio-only files.
    """
    if not os.path.isfile(path):
        raise ProbeError(f"File not found:\n{path}")
    if os.path.getsize(path) == 0:
        raise ProbeError(f"File is empty:\n{path}")

    _, ffprobe = find_binaries()
    cmd = [
        ffprobe, *_QUIET,
        "-show_streams", "-show_format",
        "-print_format", "json",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, **_no_window_kwargs()
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("ffprobe timed out while reading this file.") from exc
    except OSError as exc:
        raise ProbeError(f"Could not run ffprobe: {exc}") from exc

    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip().splitlines()
        detail_text = detail[-1] if detail else "unknown error"
        raise ProbeError(
            "This file could not be read as a video.\n\n"
            f"ffprobe said: {detail_text}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned malformed output.") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise ProbeError(
            "No video stream found in this file.\n"
            "VidClipper splits video files; audio-only files are not supported."
        )
    # Cover art in an audio file shows up as a video stream with no frames.
    if (video.get("disposition") or {}).get("attached_pic"):
        raise ProbeError(
            "This file only contains a cover image, not a video stream."
        )

    duration = _parse_fraction(str(fmt.get("duration") or ""))
    if duration <= 0:
        duration = _parse_fraction(str(video.get("duration") or ""))
    if duration <= 0:
        # Some containers (notably raw/streamed MKV) omit the duration.
        duration = _duration_by_decode(path)
    if duration <= 0:
        raise ProbeError(
            "Could not determine the duration of this file.\n"
            "It may be truncated or corrupt."
        )

    fps = _parse_fraction(str(video.get("avg_frame_rate") or ""))
    if fps <= 0:
        fps = _parse_fraction(str(video.get("r_frame_rate") or ""))
    if fps <= 0:
        fps = 25.0

    try:
        size_bytes = int(fmt.get("size") or os.path.getsize(path))
    except (TypeError, ValueError):
        size_bytes = os.path.getsize(path)

    bit_rate = _parse_fraction(str(fmt.get("bit_rate") or ""))
    if bit_rate <= 0 and duration > 0:
        bit_rate = size_bytes * 8.0 / duration

    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        video_codec=str(video.get("codec_name") or ""),
        pix_fmt=str(video.get("pix_fmt") or ""),
        audio_codec=str(audio.get("codec_name") or "") if audio else "",
        sample_rate=int(audio.get("sample_rate") or 0) if audio else 0,
        channels=int(audio.get("channels") or 0) if audio else 0,
        container=str(fmt.get("format_name") or ""),
        size_bytes=size_bytes,
        bit_rate=bit_rate,
        has_audio=audio is not None,
    )


def _duration_by_decode(path: str) -> float:
    """Fallback duration: decode the video and read the last packet time."""
    try:
        _, ffprobe = find_binaries()
    except FFmpegMissingError:
        return 0.0
    cmd = [
        ffprobe, *_QUIET,
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time",
        "-print_format", "csv=p=0",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, **_no_window_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    last = 0.0
    for line in proc.stdout.splitlines():
        value = _parse_fraction(line.strip().rstrip(","))
        if value > last:
            last = value
    return last


def scan_keyframes(path: str, cancel=None) -> list[float]:
    """Return sorted keyframe timestamps (seconds) for the first video stream.

    Reads the packet index only - no decoding - so this is fast even for
    long files. `cancel` is an optional callable returning True to abort.
    """
    _, ffprobe = find_binaries()
    cmd = [
        ffprobe, *_QUIET,
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags",
        "-print_format", "csv=p=0",
        path,
    ]
    times: list[float] = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, **_no_window_kwargs()
        )
    except OSError:
        return []

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel():
                proc.terminate()
                return sorted(times)
            line = line.strip()
            if not line or "K" not in line:
                continue
            pts = line.split(",")[0]
            if pts in ("N/A", ""):
                continue
            try:
                times.append(float(pts))
            except ValueError:
                continue
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

    times.sort()
    return times


def snap_to_keyframe(time_s: float, keyframes: list[float]) -> float:
    """Snap `time_s` to the nearest keyframe; identity if none are known."""
    if not keyframes:
        return time_s
    import bisect

    idx = bisect.bisect_left(keyframes, time_s)
    if idx == 0:
        return keyframes[0]
    if idx >= len(keyframes):
        return keyframes[-1]
    before, after = keyframes[idx - 1], keyframes[idx]
    return before if (time_s - before) <= (after - time_s) else after


def output_extension(source_ext: str, precise: bool) -> str:
    """Pick a container for the output files.

    Fast mode copies the streams, so the source container always works.
    Precise mode re-encodes to H.264/AAC, which WebM and AVI cannot
    reliably hold - those fall back to .mp4.
    """
    source_ext = (source_ext or ".mp4").lower()
    if not precise:
        return source_ext
    return source_ext if source_ext in PRECISE_SAFE_EXTS else ".mp4"
