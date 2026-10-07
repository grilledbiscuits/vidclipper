# VidClipper

**Split and merge video on your desktop, without uploading it anywhere.** VidClipper is a Linux desktop app built with PySide6 and FFmpeg. Mark cuts on a visual timeline, export segments, or arrange clips into a single video.

<p align="center"><img src="assets/vidclipper.png" width="128" alt="VidClipper icon"></p>

## What it does

- **Split visually:** preview video, scrub the timeline, drag cut markers, and undo or redo changes.
- **Split in bulk:** divide into equal parts, cut at fixed intervals, or target an approximate file size.
- **Merge clips:** reorder files by dragging and join them into one output.
- **Choose speed or accuracy:** Fast mode copies streams without re-encoding; Precise mode re-encodes for frame-accurate cuts or mixed-format merges.
- **Work locally:** your video stays on your computer. Internet access is needed only to install the Python dependency on first launch.

## Install and run

Requires **Linux**, **Python 3.11+**, **FFmpeg/FFprobe**, and Python's `venv` module. On Ubuntu or Linux Mint:

```bash
sudo apt install ffmpeg python3 python3-venv
git clone https://github.com/grilledbiscuits/vidclipper.git
cd vidclipper
./run.sh
```

The first launch creates `.venv/` and installs PySide6. Later launches use that environment. Open a file directly with `./run.sh /path/to/video.mp4`.

To add VidClipper to the application menu and desktop:

```bash
./scripts/install-launcher.sh
```

## Split a video

1. Open a video with **Open Video**, `Ctrl+O`, or drag and drop.
2. Move the playhead and press `S` to add each split point. Drag a marker to adjust it; `Ctrl+Z` undoes a change.
3. Choose an output folder and **Fast** or **Precise** mode, then select **Export All Segments**.

Fast mode cuts at nearby keyframes, so the result can differ slightly from the marked time. Use Precise mode when a cut must land on the chosen frame. The segment list shows the snapped times for Fast mode.

The default filename pattern is `{name}_part{n:02d}{ext}`. For example, `holiday.mp4` becomes `holiday_part01.mp4`, `holiday_part02.mp4`, and so on. Existing files are preserved unless you explicitly enable overwrite.

## Merge videos

Open the **Merge** tab, add clips, drag them into order, choose an output, and select **Merge**. Fast mode joins compatible clips without re-encoding. Use Precise mode for clips with different codecs, resolutions, or audio formats; it re-encodes to H.264/AAC and letterboxes smaller frames.

## Useful shortcuts

| Shortcut | Action |
| --- | --- |
| `Space` | Play or pause |
| `S` | Add split point |
| `←` / `→` | Step one frame |
| `Shift+←` / `Shift+→` | Jump one second |
| `Delete` | Remove the nearest split point |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+E` | Export segments |
| `Ctrl+1` / `Ctrl+2` | Switch between Split and Merge |

Press `F1` in the app for the full shortcut list.

## Verify the installation

```bash
./scripts/self-test.sh
```

This runs the end-to-end checks offscreen, creating sample videos and checking split and merge outputs. It needs the same FFmpeg and Python dependencies as the app.

If startup fails, run `./run.sh` in a terminal or read `~/.cache/vidclipper/launch.log`. If preview is black but export works, the Qt player may lack that video's decoder; export uses FFmpeg separately.

## License

MIT. See [LICENSE](LICENSE).
