# Video Cutter

Removes the silent parts of a video automatically. Useful for tutorials and
screen recordings: you talk, pause, think, talk again — this tool cuts out
the pauses for you.

The output keeps the **same resolution and bit depth** as your original
video. Nothing is scaled down. With `--youtube` it can also crop the video
to a full-screen 16:9 frame (no black bars on YouTube).

## What you need

- macOS, Linux or Windows
- Python 3.8 or newer
- ffmpeg

## Step 1 — Install ffmpeg

On macOS (with [Homebrew](https://brew.sh)):

```bash
brew install ffmpeg
```

On Ubuntu/Debian:

```bash
sudo apt install ffmpeg
```

Check that it works:

```bash
ffmpeg -version
```

## Step 2 — Get the tool

```bash
git clone https://github.com/gieart87/video-cutter.git
cd video-cutter
```

## Step 3 — Preview the cuts (optional, but recommended)

Before rendering, see what will be removed. This is fast and does not create
any file:

```bash
python3 auto_trim_fast.py my-video.mov --dry-run
```

You will see something like:

```
Source: 2560x1440 @ 29.970 fps, 8-bit yuv420p, 12:30
Keeping 85 parts: 9:10 kept, 3:20 removed (27% shorter)
     1.     0:00 ->     0:14  (14.2s)
     2.     0:16 ->     0:41  (25.3s)
   ...
```

## Step 4 — Cut the video

```bash
python3 auto_trim_fast.py my-video.mov
```

This saves `my-video_trimmed.mp4` next to the original. To pick your own
output name:

```bash
python3 auto_trim_fast.py my-video.mov final.mp4
```

When it finishes, it prints the output resolution so you can confirm it
matches the source:

```
Output: 2560x1440 @ 29.970 fps, 9:10 (same resolution as source)
Done! Saved as my-video_trimmed.mp4
```

Your original file is never changed.

## Step 5 — Adjust if needed

Watch the result. If something feels wrong, change a setting and run again:

| Problem | Try this |
|---|---|
| Too much is cut (quiet words are removed) | `--noise -40` |
| Not enough is cut (background noise counts as talking) | `--noise -25` |
| Short pauses are cut too, feels rushed | `--min-silence 1.5` |
| Long pauses are still there | `--min-silence 0.6` |
| First word of a sentence is clipped | `--pad-before 0.4` |
| Last word is cut off too early | `--pad-after 0.5` |

Example:

```bash
python3 auto_trim_fast.py my-video.mov --noise -35 --min-silence 0.8
```

## Step 6 — Make it full screen on YouTube (optional)

Screen recordings from a Mac are usually not 16:9 (for example 2992x1934).
On YouTube that shows black bars on the left and right. Add `--youtube` to
crop the video to exactly 16:9 so it fills the whole player:

```bash
python3 auto_trim_fast.py my-video.mov --youtube
```

By default this also scales the video to **4K (3840x2160)**. Even if your
viewers watch in 1080p, YouTube gives 4K uploads a much higher bitrate, so
small text like code and terminal output stays sharp.

Pick a different size if you want:

| Option | Output size | Notes |
|---|---|---|
| `--youtube` or `--youtube 4k` | 3840x2160 | Sharpest on YouTube (recommended) |
| `--youtube 1440p` | 2560x1440 | Smaller file |
| `--youtube 1080p` | 1920x1080 | Smallest file, text is less sharp |
| `--youtube native` | e.g. 2976x1674 | Only crops, no scaling |

Cropping removes a strip from the top and bottom (or left and right). Choose
which part to keep:

| Option | What is kept |
|---|---|
| `--crop-anchor center` (default) | The middle. Cuts a bit of the top (menu bar) and bottom (Dock). |
| `--crop-anchor top` | The top, e.g. browser tabs. Cuts more from the bottom. |
| `--crop-anchor bottom` | The bottom. Cuts more from the top. |

Tip: check the result with `--dry-run` first. It shows the crop size before
rendering.

## Quality options

By default the video is saved as **H.264 at visually lossless quality
(CRF 12)**, which plays everywhere and is great for YouTube.

| Option | What you get | When to use it |
|---|---|---|
| *(default)* `--codec h264` | High quality MP4, plays everywhere | Uploading to YouTube, sharing |
| `--codec hevc` | Same quality, about half the file size | Saving disk space, Apple devices |
| `--codec prores` | Near-lossless MOV, very big file | You will edit it further in Final Cut / Premiere / DaVinci |

More control:

- `--crf 10` — even higher quality (bigger file). Lower number = better. Default: 12 (h264), 14 (hevc).
- `--fps 30` — choose the output frame rate. Default: same as the source.
- `--preset slower` — smaller file at the same quality, but slower to render. Default: `slow`.
- `--no-denoise` — turn off background noise reduction on the audio.

What is always kept from the original:

- Resolution (e.g. 1920x1080, 2560x1440, 3840x2160), unless you use `--youtube`
- Frame rate (e.g. 29.97, 30, 60 fps)
- 10-bit color, if the source is 10-bit
- Color settings (color space, range)

About screen recordings: macOS records with a *variable* frame rate (new
frames only when something changes on screen). The tool converts these to a
smooth, constant **60 fps**, so no frames are dropped and mouse movement and
scrolling stay smooth.

## All options

```
python3 auto_trim_fast.py INPUT [OUTPUT] [options]

  --noise DB          quieter than this counts as silence (default: -30)
  --min-silence SEC   only cut pauses longer than this (default: 1.0)
  --pad-before SEC    time kept before talking starts (default: 0.2)
  --pad-after SEC     time kept after talking ends (default: 0.3)
  --youtube [SIZE]    crop to full-screen 16:9; SIZE = 4k (default), 1440p,
                      1080p, or native
  --crop-anchor POS   part to keep when cropping: center (default), top, bottom
  --fps N             output frame rate (default: source; 60 for screen recordings)
  --codec NAME        h264 (default), hevc, or prores
  --crf N             quality for h264/hevc, lower = better
  --preset NAME       x264/x265 speed preset (default: slow)
  --no-denoise        turn off audio noise reduction
  --dry-run           only show the cuts, do not render
```

Run `python3 auto_trim_fast.py --help` to see this list anytime.

## Troubleshooting

- **"No speech found"** — your audio is quieter than the threshold. Try `--noise -40` or `--noise -50`.
- **"ffmpeg: command not found"** — ffmpeg is not installed, see Step 1.
- **Important parts are cut off with `--youtube`** — try `--crop-anchor top` or `--crop-anchor bottom`, or check with `--dry-run`.
- **Rendering is slow** — that is normal for high quality (4K takes about as long as the video itself). Use `--preset medium` or `--preset fast` to speed it up (slightly bigger file, same visual quality).
