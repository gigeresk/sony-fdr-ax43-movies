# Sony FDR-AX43 Movies
Play movies on your FDR-AX43's SD card

## Windows app (easiest)
Download `AX43-Movie-Adder.exe` from the latest GitHub Release. With the camera's SD card plugged in, **drag videos onto the .exe** (or onto its window). It finds the card, converts each video, adds it, and shows a progress bar. When it's done, click **Eject card** and do the camera steps below. ffmpeg is built in, so there's nothing to install.

Dragging more videos onto the .exe while it's open adds them to the running window's queue. Windows may show a SmartScreen warning because the exe isn't code-signed: click *More info → Run anyway*.

### Building / releasing
- **Release:** commit, then run `build/release.sh v1.0.0`. That pushes a tag, GitHub Actions builds the exe on Windows, runs a smoke test and publishes a Release with the exe, SHA256SUMS and THIRD_PARTY.txt.
- **Test build without a release:** in the Actions tab, run *Windows build*; the exe appears as a downloadable artifact.
- **Local build on Windows:** `powershell -ExecutionPolicy Bypass -File build\build_windows.ps1`. Its tools (uv, Python, PyInstaller, ffmpeg) go in `%LOCALAPPDATA%\ax43build`; nothing is installed system-wide.

## Command line
### Requirements
- Python 3.8+ and **ffmpeg/ffprobe** on PATH (Windows, Linux or WSL). No other packages.
- A card that has been formatted **in the camera**, with at least one clip recorded.

### Usage
```
python3 ax43put.py "My Movie.mkv" --card /mnt/f      # Linux / WSL
python  ax43put.py "My Movie.mkv" --card F:\         # Windows
python3 ax43put.py a.mp4 b.mkv --card /mnt/f --dry-run   # preview only
```
On WSL, mount the card first: `sudo mkdir -p /mnt/f && sudo mount -t drvfs F: /mnt/f`.

Then on the camera: **MENU → Setup → Media Settings → Repair Img. DB F. → Yes**, open playback and switch the movie format to **MP4**.

Options: `--bitrate KBPS` (default 2500, the same as the camera), `--preset fast|medium|slow`.

## What it does
- Converts the video to the camera's "Dual Video REC" format (1280×720 23.976p H.264 + AAC stereo), letterboxing as needed.
- Writes it as the next free `MP_ROOT/100ANV01/MAH#####.MP4` with a matching `.THM` thumbnail.
- **Never modifies or deletes anything on the card.** It writes each file to a temporary name, checks it with SHA-256, renames it, and never overwrites an existing file.
- Files must be under 4 GB (FAT32). At 2.5 Mbit/s that's about 3.5 hours.

## License
GNU General Public License v3.0 or later, see [LICENSE](LICENSE). The Windows build bundles FFmpeg (GPL); see `build/THIRD_PARTY.txt`.
