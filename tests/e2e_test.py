#!/usr/bin/env python3
"""End-to-end check: load a video, add splits, export, verify the output.

Drives the real MainWindow, so it exercises the same code path as a user
clicking through the app. Run it with:

    ./run.sh --self-test          (see scripts/self_test.sh)
    .venv/bin/python tests/e2e_test.py [sample.mp4] [work-dir]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from vidclipper.app import MainWindow  # noqa: E402
from vidclipper.model import SplitModel  # noqa: E402
from vidclipper import quicksplit  # noqa: E402

FAILURES: list[str] = []
CHECKS: list[str] = []


def check(condition: bool, label: str) -> bool:
    (CHECKS if condition else FAILURES).append(label)
    print(("  PASS  " if condition else "  FAIL  ") + label)
    return bool(condition)


def duration_of(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def decodes_cleanly(path: str) -> bool:
    """Fully decode the file; any error means the output is broken."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
        capture_output=True, text=True)
    return proc.returncode == 0 and not proc.stderr.strip()


def make_sample(path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", "30", "-c:v", "libx264", "-preset", "veryfast", "-g", "60",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", path],
        check=True)


class Harness:
    """Runs the scripted steps once the event loop is up."""

    def __init__(self, window: MainWindow, sample: str, work: str) -> None:
        self.win = window
        self.sample = sample
        self.work = work
        self.steps = [
            self.step_load, self.step_wait_scan, self.step_add_splits,
            self.step_export_fast, self.step_verify_fast,
            self.step_export_precise, self.step_verify_precise,
            self.step_containers, self.step_pure_units, self.step_errors,
            self.step_quick_split_export, self.step_verify_quick_split,
            self.step_cancel, self.step_verify_cancel,
            self.step_merge_setup, self.step_merge_fast,
            self.step_verify_merge_fast, self.step_merge_mixed,
            self.step_verify_merge_mixed, self.step_merge_drag_drop,
            self.finish,
        ]
        self.export_done = False
        self.result = None
        self.fast_dir = os.path.join(work, "out_fast")
        self.precise_dir = os.path.join(work, "out_precise")
        os.makedirs(self.fast_dir, exist_ok=True)
        os.makedirs(self.precise_dir, exist_ok=True)
        window.worker_finished_hook = None

        # Dismiss any modal dialog the app opens so the test never blocks.
        self.dismisser = QTimer()
        self.dismisser.timeout.connect(self._dismiss)
        self.dismisser.start(150)

    def _dismiss(self) -> None:
        widget = QApplication.activeModalWidget()
        if isinstance(widget, QMessageBox):
            print(f"  (dismissed dialog: {widget.windowTitle()!r} - "
                  f"{widget.text().splitlines()[0][:70]!r})")
            widget.done(QMessageBox.Ok)
        elif isinstance(widget, QDialog):
            widget.reject()

    def run_next(self) -> None:
        if not self.steps:
            return
        step = self.steps.pop(0)
        step()

    # -- steps --------------------------------------------------------
    def step_load(self) -> None:
        print("\n[1] Loading the sample video")
        self.win.load_file(self.sample)
        info = self.win.info
        check(info is not None, "video probed successfully")
        check(abs(info.duration - 30.0) < 0.2,
              f"duration read as {info.duration:.2f}s (expected ~30s)")
        check(info.width == 640 and info.height == 360,
              f"resolution read as {info.width}x{info.height}")
        check(info.has_audio, "audio stream detected")
        QTimer.singleShot(2500, self.run_next)

    def step_wait_scan(self) -> None:
        print("\n[2] Keyframe scan")
        keyframes = self.win._keyframes
        check(len(keyframes) > 5,
              f"{len(keyframes)} keyframes found in the packet index")
        QTimer.singleShot(50, self.run_next)

    def step_add_splits(self) -> None:
        print("\n[3] Adding split points at 8s and 18s (the 'S' key path)")
        self.win.player.set_position(8.0)
        self.win.add_split()
        self.win.player.set_position(18.0)
        self.win.add_split()
        check(self.win.model.count() == 2,
              f"model holds {self.win.model.count()} split points")

        segments = self.win.model.segments(self.win._snap_fn())
        check(len(segments) == 3, f"{len(segments)} segments derived")
        check(self.win.segments_panel.table.rowCount() == 3,
              "segment list panel shows 3 rows")
        total = sum(s.duration for s in segments)
        check(abs(total - self.win.model.duration) < 0.01,
              f"segments tile the timeline ({total:.3f}s)")

        # Duplicate and out-of-range splits must be rejected.
        check(not self.win.model.add_split(8.0), "duplicate split rejected")
        check(not self.win.model.add_split(0.0), "split at 0s rejected")
        check(not self.win.model.add_split(30.0), "split at the end rejected")

        # Undo/remove round-trip, then put the marker back.
        self.win.model.add_split(24.0)
        check(self.win.model.count() == 3, "third split added")
        self.win.undo()
        check(self.win.model.count() == 2, "undo removed the third split")

        self.win.grab().save(os.path.join(self.work, "screenshot_splits.png"))
        QTimer.singleShot(50, self.run_next)

    def _start_export(self, out_dir: str, precise: bool) -> None:
        panel = self.win.export_panel
        panel.set_out_dir(out_dir)
        panel.pattern_edit.setCurrentText("{name}_part{n:02d}{ext}")
        panel.precise_radio.setChecked(precise)
        panel.fast_radio.setChecked(not precise)
        self.export_done = False
        self.win.worker = None
        self.win.start_export()
        worker = self.win.worker
        check(worker is not None, "export worker started")
        if worker is not None:
            worker.finished_all.connect(self._on_done)

    def _on_done(self, result) -> None:
        self.result = result
        self.export_done = True

    def _wait_for_export(self, then, elapsed: float = 0.0) -> None:
        if self.export_done:
            QTimer.singleShot(200, then)
            return
        if elapsed > 180:
            check(False, "export finished within 180s")
            QTimer.singleShot(0, then)
            return
        QTimer.singleShot(200, lambda: self._wait_for_export(then, elapsed + 0.2))

    def step_export_fast(self) -> None:
        print("\n[4] Exporting with Fast mode (stream copy)")
        import time
        self._t0 = time.time()
        self._start_export(self.fast_dir, precise=False)
        self._wait_for_export(self.run_next)

    def step_verify_fast(self) -> None:
        import time
        print("\n[5] Verifying the Fast-mode output")
        elapsed = time.time() - self._t0
        files = sorted(os.listdir(self.fast_dir))
        check(len(files) == 3, f"3 files written: {files}")
        check(self.result is not None and not self.result.errors,
              f"no export errors ({getattr(self.result, 'errors', None)})")
        check(elapsed < 15, f"stream copy finished fast ({elapsed:.1f}s)")

        total = 0.0
        for name in files:
            path = os.path.join(self.fast_dir, name)
            seconds = duration_of(path)
            total += seconds
            size = os.path.getsize(path)
            check(seconds > 0.5 and size > 1000,
                  f"{name}: {seconds:.2f}s, {size / 1024:.0f} KB")
            check(decodes_cleanly(path), f"{name} decodes without errors")
        check(abs(total - 30.0) < 1.0,
              f"segment durations add up to {total:.2f}s (original 30s)")
        self.fast_files = [os.path.join(self.fast_dir, f) for f in files]
        QTimer.singleShot(50, self.run_next)

    def step_export_precise(self) -> None:
        print("\n[6] Exporting with Precise mode (re-encode)")
        self._start_export(self.precise_dir, precise=True)
        self._wait_for_export(self.run_next)

    def step_verify_precise(self) -> None:
        print("\n[7] Verifying the Precise-mode output")
        files = sorted(os.listdir(self.precise_dir))
        check(len(files) == 3, f"3 files written: {files}")
        check(self.result is not None and not self.result.errors,
              f"no export errors ({getattr(self.result, 'errors', None)})")
        durations = [duration_of(os.path.join(self.precise_dir, f))
                     for f in files]
        for name, seconds in zip(files, durations):
            check(decodes_cleanly(os.path.join(self.precise_dir, name)),
                  f"{name} decodes without errors ({seconds:.2f}s)")
        # Precise mode must hit the requested marks, not the keyframes.
        check(abs(durations[0] - 8.0) < 0.15,
              f"first precise segment is {durations[0]:.3f}s (asked for 8.000s)")
        check(abs(durations[1] - 10.0) < 0.15,
              f"second precise segment is {durations[1]:.3f}s (asked for 10.000s)")
        check(abs(sum(durations) - 30.0) < 0.3,
              f"precise durations add up to {sum(durations):.2f}s")
        QTimer.singleShot(50, self.run_next)

    def step_containers(self) -> None:
        """Every advertised container must load, split and export."""
        print("\n[8] Other containers (mkv / webm / avi / mov)")
        from vidclipper.exporter import (ExportSettings, ExportWorker,
                                         plan_outputs)
        from vidclipper.media import output_extension, probe
        from vidclipper.model import SplitModel

        recipes = {
            ".mkv": ["-c:v", "libx264", "-c:a", "aac"],
            ".webm": ["-c:v", "libvpx-vp9", "-b:v", "300k", "-c:a", "libopus"],
            ".avi": ["-c:v", "mpeg4", "-c:a", "libmp3lame"],
            ".mov": ["-c:v", "libx264", "-c:a", "aac"],
        }
        for ext, encoders in recipes.items():
            source = os.path.join(self.work, f"clip{ext}")
            build = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", self.sample, "-t", "12",
                 "-g", "60", *encoders, source],
                capture_output=True, text=True)
            if build.returncode != 0:
                check(False, f"{ext}: could not build a test clip "
                             f"({build.stderr.strip()[:80]})")
                continue

            try:
                info = probe(source)
            except Exception as exc:
                check(False, f"{ext}: probe failed ({exc})")
                continue
            check(info.duration > 10, f"{ext}: probed ({info.duration:.1f}s, "
                                      f"{info.video_codec})")

            # The app snaps fast-mode cuts to keyframes; do the same here,
            # otherwise ffmpeg seeks back to the previous keyframe and the
            # segments overlap.
            from vidclipper.media import scan_keyframes, snap_to_keyframe
            keyframes = scan_keyframes(source)
            check(len(keyframes) >= 1, f"{ext}: {len(keyframes)} keyframes found")

            model = SplitModel()
            model.set_duration(info.duration)
            model.add_split(info.duration / 2)
            segments = model.segments(
                lambda t: snap_to_keyframe(t, keyframes))

            out_dir = os.path.join(self.work, "containers", ext.lstrip("."))
            os.makedirs(out_dir, exist_ok=True)
            settings = ExportSettings(
                source_path=source, out_dir=out_dir,
                pattern="{name}_part{n:02d}{ext}", precise=False,
                source_ext=ext, source_bytes=info.size_bytes,
                source_duration=info.duration, has_audio=info.has_audio)
            plan = plan_outputs(segments, settings)
            # Drive the real worker (synchronously) so this exercises the
            # same code path the Export button uses.
            worker = ExportWorker(plan, settings)
            worker.run()
            ok = all(os.path.exists(p) for _, p in plan)
            if not ok:
                check(False, f"{ext}: fast export did not produce both files")
            if ok:
                total = sum(duration_of(p) for _, p in plan)
                check(abs(total - info.duration) < 1.0,
                      f"{ext}: fast split output totals {total:.1f}s "
                      f"of {info.duration:.1f}s")
                check(all(decodes_cleanly(p) for _, p in plan),
                      f"{ext}: fast segments decode cleanly")

        check(output_extension(".webm", precise=True) == ".mp4",
              "precise mode moves .webm output to .mp4")
        check(output_extension(".avi", precise=True) == ".mp4",
              "precise mode moves .avi output to .mp4")
        check(output_extension(".mkv", precise=True) == ".mkv",
              "precise mode keeps .mkv")
        check(output_extension(".mkv", precise=False) == ".mkv",
              "fast mode always keeps the source container")
        QTimer.singleShot(50, self.run_next)

    def step_pure_units(self) -> None:
        print("\n[9] Quick Split maths and model rules")
        model = SplitModel()
        model.set_duration(100.0)
        model.set_splits(quicksplit.equal_parts(100.0, 4))
        check([round(s, 3) for s in model.splits] == [25.0, 50.0, 75.0],
              "equal parts -> 25/50/75")
        check(len(model.segments()) == 4, "equal parts -> 4 segments")

        model.set_splits(quicksplit.every_seconds(100.0, 30.0))
        check([round(s, 3) for s in model.splits] == [30.0, 60.0, 90.0],
              "every 30s -> 30/60/90")

        points = quicksplit.by_target_size(100.0, 100 * 1024 * 1024,
                                           25 * 1024 * 1024)
        check(len(points) == 3, f"25 MB of a 100 MB/100 s file -> {points}")
        check(quicksplit.equal_parts(100.0, 1) == [], "N=1 produces no splits")

        # Drag-and-drop filtering (the handler only accepts local video files).
        from PySide6.QtCore import QMimeData, QUrl

        class FakeEvent:
            def __init__(self, mime):
                self._mime = mime

            def mimeData(self):
                return self._mime

        video_mime = QMimeData()
        video_mime.setUrls([QUrl.fromLocalFile(self.sample)])
        check(MainWindow._dropped_path(FakeEvent(video_mime)) == self.sample,
              "drag-and-drop accepts a local video file")

        text_mime = QMimeData()
        text_mime.setUrls([QUrl.fromLocalFile("/tmp/notes.txt")])
        check(MainWindow._dropped_path(FakeEvent(text_mime)) is None,
              "drag-and-drop ignores non-video files")

        remote_mime = QMimeData()
        remote_mime.setUrls([QUrl("https://example.com/clip.mp4")])
        check(MainWindow._dropped_path(FakeEvent(remote_mime)) is None,
              "drag-and-drop ignores remote URLs")

        # Quick Split through the window, then cleared again.
        self.win.apply_quick_split(0, 5)
        check(self.win.model.count() == 4, "Quick Split applied to the window")
        self.win.clear_splits()
        check(self.win.model.count() == 0, "Remove All cleared the markers")
        QTimer.singleShot(50, self.run_next)

    def step_errors(self) -> None:
        print("\n[10] Error handling")
        from vidclipper.media import ProbeError, probe
        from vidclipper.exporter import ExportError, ExportSettings, plan_outputs
        from vidclipper.model import Segment

        bad = os.path.join(self.work, "corrupt.mp4")
        with open(bad, "wb") as handle:
            handle.write(os.urandom(4096))
        try:
            probe(bad)
            check(False, "corrupt file raises ProbeError")
        except ProbeError as exc:
            check("could not be read" in str(exc).lower()
                  or "duration" in str(exc).lower(),
                  f"corrupt file rejected: {str(exc).splitlines()[0]}")

        try:
            probe(os.path.join(self.work, "does_not_exist.mp4"))
            check(False, "missing file raises ProbeError")
        except ProbeError:
            check(True, "missing file rejected with a clear message")

        settings = ExportSettings(source_path=self.sample, out_dir=self.work,
                                  pattern="{name}{ext}")
        try:
            plan_outputs([], settings)
            check(False, "empty segment list raises ExportError")
        except ExportError:
            check(True, "exporting zero segments is blocked")

        segments = [Segment(1, 0, 5), Segment(2, 5, 10)]
        empty_dir = os.path.join(self.work, "empty_out")
        os.makedirs(empty_dir, exist_ok=True)
        settings.out_dir = empty_dir
        try:
            plan_outputs(segments, settings)
            check(False, "colliding filenames raise ExportError")
        except ExportError:
            check(True, "pattern without {n} is rejected")

        # Exporting on top of the source file must never be allowed.
        settings.out_dir = os.path.dirname(self.sample)
        settings.pattern = os.path.basename(self.sample)
        settings.overwrite = True
        try:
            plan_outputs(segments, settings)
            check(False, "writing over the source file raises ExportError")
        except ExportError:
            check(True, "refuses to overwrite the source video")

        # A colliding name with overwrite off gets a unique name instead.
        settings.out_dir = empty_dir
        settings.overwrite = False
        settings.pattern = "{name}_part{n:02d}{ext}"
        open(os.path.join(empty_dir, "sample_part01.mp4"), "wb").close()
        plan = plan_outputs(segments, settings)
        check(os.path.basename(plan[0][1]) == "sample_part01_2.mp4",
              f"existing file preserved, wrote {os.path.basename(plan[0][1])}")

        settings.out_dir = "/nonexistent-folder-xyz"
        settings.pattern = "{name}_part{n:02d}{ext}"
        try:
            plan_outputs(segments, settings)
            check(False, "missing output folder raises ExportError")
        except ExportError:
            check(True, "missing output folder is rejected")

        # The corrupt file must also be refused by the GUI path (dialog is
        # auto-dismissed by the harness) without changing the loaded video.
        loaded = self.win.info.path
        self.win.load_file(bad)
        check(self.win.info.path == loaded,
              "a failed load leaves the current video in place")
        QTimer.singleShot(300, self.run_next)

    def step_quick_split_export(self) -> None:
        """Quick Split must feed the same export pipeline as manual splits."""
        print("\n[11] Quick Split -> export (8 equal parts, one pass)")
        self.win.clear_splits()
        self.win.apply_quick_split(0, 8)   # 8 equal parts
        check(self.win.model.count() == 7, "7 split points generated")
        self.quick_dir = os.path.join(self.work, "out_quick")
        os.makedirs(self.quick_dir, exist_ok=True)
        self._start_export(self.quick_dir, precise=False)
        self._wait_for_export(self.run_next)

    def step_verify_quick_split(self) -> None:
        files = sorted(os.listdir(self.quick_dir))
        check(len(files) == 8, f"8 files written: {len(files)}")
        total = sum(duration_of(os.path.join(self.quick_dir, f))
                    for f in files)
        check(abs(total - 30.0) < 0.6,
              f"8 quick-split segments total {total:.2f}s")
        check(all(decodes_cleanly(os.path.join(self.quick_dir, f))
                  for f in files), "all 8 segments decode cleanly")
        leftovers = [f for f in files if f.startswith(".vidclipper-")]
        check(not leftovers, f"no temporary files left behind ({leftovers})")
        QTimer.singleShot(50, self.run_next)

    def step_cancel(self) -> None:
        """Cancelling must stop the run and clean up the partial file."""
        print("\n[12] Cancelling an export")
        self.win.clear_splits()
        self.win.model.add_split(10.0)
        self.win.model.add_split(20.0)
        self.cancel_dir = os.path.join(self.work, "out_cancel")
        os.makedirs(self.cancel_dir, exist_ok=True)
        self._start_export(self.cancel_dir, precise=True)
        QTimer.singleShot(700, self.win.cancel_export)
        self._wait_for_export(self.run_next)

    def step_verify_cancel(self) -> None:
        check(self.result is not None and self.result.cancelled,
              "export reported as cancelled")
        files = sorted(os.listdir(self.cancel_dir))
        written = [os.path.basename(p) for p in (self.result.written or [])]
        check(all(f in written for f in files),
              f"only completed files kept: on disk {files}, reported {written}")
        check(self.win.worker is None, "worker cleared after cancelling")
        check(self.win.export_panel.export_btn.text() == "Export All Segments",
              "export button returned to its normal state")
        QTimer.singleShot(50, self.run_next)

    def step_merge_setup(self) -> None:
        """The Merge tab: add clips, reorder them, check compatibility."""
        print("\n[13] Merge tab - adding and reordering clips")
        panel = self.win.merge_panel
        panel.clear()
        parts = sorted(os.path.join(self.fast_dir, f)
                       for f in os.listdir(self.fast_dir))
        panel.add_paths(parts)
        check(len(panel.clips()) == 3, f"{len(panel.clips())} clips in the list")
        check([os.path.basename(c.path) for c in panel.clips()]
              == [os.path.basename(p) for p in parts],
              "clips are listed in the order they were added")
        check(panel.list.item(0).text().startswith("1."),
              "rows are numbered for the merge order")

        # Reverse, then sort back - the same operations the buttons run.
        panel.list.reverse_order()
        check([os.path.basename(c.path) for c in panel.clips()]
              == [os.path.basename(p) for p in reversed(parts)],
              "Reverse flipped the merge order")

        panel.list.setCurrentRow(0)
        panel.list.move_selected(1)
        check(os.path.basename(panel.clips()[1].path)
              == os.path.basename(parts[2]),
              "Move Down shifted the selected clip one place")

        panel.list.sort_by_name()
        check([os.path.basename(c.path) for c in panel.clips()]
              == [os.path.basename(p) for p in parts],
              "Sort A-Z restored the original order")

        panel.list.setCurrentRow(1)
        removed = panel.list.remove_selected()
        check(removed == 1 and len(panel.clips()) == 2,
              "Remove took out the selected clip")
        panel.clear()
        panel.add_paths(parts)
        check(len(panel.clips()) == 3, "clips re-added after Clear All")

        from vidclipper.merger import compatibility_problems
        check(not compatibility_problems(panel.clips()),
              "segments from one source are compatible with Fast merge")
        check("Fast mode will join them losslessly" in panel.compat.text(),
              "panel reports that a lossless merge is possible")
        QTimer.singleShot(50, self.run_next)

    def step_merge_fast(self) -> None:
        print("\n[14] Merging losslessly (the split, undone)")
        panel = self.win.merge_panel
        self.merge_dir = os.path.join(self.work, "out_merge")
        os.makedirs(self.merge_dir, exist_ok=True)
        panel.dir_edit.setText(self.merge_dir)
        panel.name_edit.setText("rejoined.mp4")
        panel.fast_radio.setChecked(True)
        self.merge_result = None
        panel.merge_btn.click()
        worker = panel.worker
        check(worker is not None, "merge worker started")
        if worker is not None:
            worker.finished_merge.connect(self._on_merge_done)
        self._wait_for_merge(self.run_next)

    def _on_merge_done(self, result) -> None:
        self.merge_result = result

    def _wait_for_merge(self, then, elapsed: float = 0.0) -> None:
        if self.merge_result is not None:
            QTimer.singleShot(200, then)
            return
        if elapsed > 180:
            check(False, "merge finished within 180s")
            QTimer.singleShot(0, then)
            return
        QTimer.singleShot(200, lambda: self._wait_for_merge(then, elapsed + 0.2))

    def step_verify_merge_fast(self) -> None:
        print("\n[15] Verifying the merged file")
        result = self.merge_result
        check(result is not None and result.ok,
              f"merge reported success ({getattr(result, 'error', '')})")
        out = os.path.join(self.merge_dir, "rejoined.mp4")
        check(os.path.exists(out), "rejoined.mp4 written")
        merged = duration_of(out)
        check(abs(merged - 30.0) < 0.5,
              f"merged duration {merged:.2f}s matches the 30s original")
        check(decodes_cleanly(out), "merged file decodes without errors")
        check(self.win.merge_panel.worker is None,
              "merge worker cleared afterwards")
        QTimer.singleShot(50, self.run_next)

    def step_merge_mixed(self) -> None:
        """Clips from different sources: Fast must refuse, Precise must work."""
        print("\n[16] Merging clips that do not match")
        from vidclipper.merger import (MergeError, MergeSettings,
                                       compatibility_problems, target_geometry,
                                       validate)

        odd_dir = os.path.join(self.work, "odd")
        os.makedirs(odd_dir, exist_ok=True)
        small = os.path.join(odd_dir, "small_silent.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=25", "-t", "4",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", small], check=True)

        panel = self.win.merge_panel
        panel.clear()
        panel.add_paths([os.path.join(self.fast_dir, f)
                         for f in sorted(os.listdir(self.fast_dir))[:1]])
        panel.add_paths([small])
        clips = panel.clips()
        check(len(clips) == 2, "one 640x360 clip plus one 320x240 silent clip")

        problems = compatibility_problems(clips)
        check(bool(problems), f"mismatch detected: {problems[0]}")
        check("Switch to Precise" in panel.compat.text(),
              "panel tells the user to switch to Precise mode")

        settings = MergeSettings(clips=clips,
                                 out_path=os.path.join(odd_dir, "x.mp4"),
                                 precise=False)
        try:
            validate(settings)
            check(False, "fast merge of mismatched clips is refused")
        except MergeError as exc:
            check("cannot be joined losslessly" in str(exc),
                  "fast merge refused with an explanation")

        width, height, _fps = target_geometry(clips)
        check((width, height) == (640, 360),
              f"precise merge targets the largest frame ({width}x{height})")

        # Guard: never write the merged file over one of its own inputs.
        settings.out_path = clips[0].path
        settings.precise = True
        try:
            validate(settings)
            check(False, "merging onto an input file is refused")
        except MergeError:
            check(True, "refuses to overwrite a clip being merged")

        panel.precise_radio.setChecked(True)
        panel.dir_edit.setText(odd_dir)
        panel.name_edit.setText("mixed.mp4")
        self.merge_result = None
        panel.merge_btn.click()
        check(panel.worker is not None, "precise merge started")
        if panel.worker is not None:
            panel.worker.finished_merge.connect(self._on_merge_done)
        self._wait_for_merge(self.run_next)

    def step_verify_merge_mixed(self) -> None:
        print("\n[17] Verifying the re-encoded merge")
        result = self.merge_result
        check(result is not None and result.ok,
              f"precise merge succeeded ({getattr(result, 'error', '')})")
        out = result.out_path if result else ""
        if out and os.path.exists(out):
            merged = duration_of(out)
            check(abs(merged - 12.02) < 0.6,
                  f"merged duration {merged:.2f}s = 8.02s + 4.00s")
            check(decodes_cleanly(out), "re-encoded merge decodes cleanly")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", out], capture_output=True, text=True)
            check(probe.stdout.strip().startswith("640,360"),
                  f"output normalised to 640x360 ({probe.stdout.strip()})")
            has_audio = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", out],
                capture_output=True, text=True).stdout.strip()
            check(has_audio == "aac",
                  f"silence was generated for the mute clip (audio={has_audio})")
        else:
            check(False, "precise merge produced a file")
        QTimer.singleShot(50, self.run_next)

    def step_merge_drag_drop(self) -> None:
        """The list must accept dropped files and reject junk."""
        print("\n[18] Merge list drag-and-drop filtering")
        from PySide6.QtCore import QMimeData, QUrl
        from vidclipper.widgets.cliplist import ClipListWidget

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.sample),
                      QUrl.fromLocalFile("/tmp/readme.txt"),
                      QUrl("https://example.com/remote.mp4")])
        accepted = ClipListWidget._video_paths(mime)
        check(accepted == [self.sample],
              f"only the local video was accepted ({accepted})")

        panel = self.win.merge_panel
        panel.clear()
        panel.list.files_dropped.emit([self.sample, self.sample])
        check(len(panel.clips()) == 2,
              "the same clip can be added twice (repeat a section)")
        check(self.win._dropped_paths(
            type("E", (), {"mimeData": lambda s: mime})()) == [self.sample],
            "window-level drop filter agrees")
        panel.clear()
        QTimer.singleShot(50, self.run_next)

    def finish(self) -> None:
        # Close through the real quit path first, so background threads
        # are stopped the way they are when a user quits the app.
        print("\n[19] Shutting down")
        self.dismisser.stop()
        self.win.close()
        check(self.win.merge_panel.thumbs is None
              or not self.win.merge_panel.thumbs.isRunning(),
              "thumbnail thread stopped on close")
        check(self.win.worker is None, "no export thread left running")

        print("\n" + "=" * 62)
        print(f"{len(CHECKS)} checks passed, {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  FAILED: {failure}")
        print("=" * 62)
        QApplication.instance().exit(1 if FAILURES else 0)


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is required for the self-test")
        return 2

    work = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(
        prefix="vidclipper-test-")
    os.makedirs(work, exist_ok=True)
    sample = sys.argv[1] if len(sys.argv) > 1 else os.path.join(work, "sample.mp4")
    if not os.path.exists(sample):
        print(f"Generating a sample video at {sample}")
        make_sample(sample)

    print(f"Sample:    {sample}")
    print(f"Work dir:  {work}")

    app = QApplication(sys.argv[:1])
    window = MainWindow()
    window.show()
    harness = Harness(window, sample, work)
    QTimer.singleShot(600, harness.run_next)
    code = app.exec()
    print("Outputs left in:", work)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
