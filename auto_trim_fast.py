#!/usr/bin/env python3
"""Cut silent parts out of a video while keeping the original resolution,
frame rate, bit depth and color metadata."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction

CODEC_DEFAULTS = {
    "h264": {"crf": 16, "ext": ".mp4"},
    "hevc": {"crf": 18, "ext": ".mp4"},
    "prores": {"crf": None, "ext": ".mov"},
}


def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, **kwargs)


def probe(input_file):
    result = run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", input_file],
        stdout=subprocess.PIPE, check=True,
    )
    data = json.loads(result.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        sys.exit("Error: input has no video stream.")
    if audio is None:
        sys.exit("Error: input has no audio stream (silence detection needs audio).")

    fps = Fraction(video.get("avg_frame_rate", "0/0") if video.get("avg_frame_rate") != "0/0"
                   else video["r_frame_rate"])
    pix_fmt = video.get("pix_fmt", "yuv420p")
    bit_depth = int(video.get("bits_per_raw_sample") or 0) or (10 if "10" in pix_fmt else 8)

    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps.limit_denominator(1001),
        "pix_fmt": pix_fmt,
        "bit_depth": bit_depth,
        "color_primaries": video.get("color_primaries"),
        "color_trc": video.get("color_transfer"),
        "colorspace": video.get("color_space"),
        "color_range": video.get("color_range"),
        "sample_rate": int(audio["sample_rate"]),
        "audio_bitrate": int(audio.get("bit_rate") or 0),
    }


def detect_silence(input_file, noise_db, min_silence):
    print(f"Detecting silence (below {noise_db} dB for at least {min_silence}s)...")
    result = run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", input_file, "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?\d+\.?\d*)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (\d+\.?\d*)", result.stderr)]
    return starts, ends


def build_segments(duration, silence_starts, silence_ends, pad_before, pad_after, fps):
    """Return the parts to KEEP as (start, end) pairs, padded and snapped to frames."""
    # Silence that runs until the end of the file has no silence_end.
    if len(silence_ends) < len(silence_starts):
        silence_ends.append(duration)

    # Speech = everything between silences.
    speech = []
    cursor = 0.0
    for start, end in zip(silence_starts, silence_ends):
        if start > cursor:
            speech.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        speech.append((cursor, duration))

    # Pad each speech part so words are not clipped, then merge overlaps.
    frame = 1 / float(fps)
    segments = []
    for start, end in speech:
        start = max(0.0, start - pad_before)
        end = min(duration, end + pad_after)
        # Snap to the frame grid so audio and video cut at the same moment.
        start = round(start / frame) * frame
        end = round(end / frame) * frame
        if end - start < frame:
            continue
        if segments and start <= segments[-1][1]:
            segments[-1] = (segments[-1][0], max(segments[-1][1], end))
        else:
            segments.append((start, end))
    return segments


def build_filter(segments, info, denoise):
    """Build the ffmpeg filter that keeps only the given segments.

    Video: select the kept frames and shift their timestamps to close the
    gaps. Timestamps come from the source, so there is no drift.
    Audio: split the stream at every cut point (sample-accurate) and join
    the kept pieces. (aselect is not used: it does not drop frames in
    ffmpeg 8.0.)
    """
    select_terms = []
    offset_terms = []
    removed = 0.0
    last_end = 0.0
    for start, end in segments:
        removed += start - last_end
        last_end = end
        select_terms.append(f"gte(t,{start:.6f})*lt(t,{end:.6f})")
        if removed:
            offset_terms.append(f"gte(T,{start:.6f})*lt(T,{end:.6f})*{removed:.6f}")

    fps = info["fps"]
    # fps would otherwise pad up to the source's end time.
    kept = sum(end - start for start, end in segments)
    video = (
        f"[0:v]select='{'+'.join(select_terms)}',"
        f"setpts='PTS-({'+'.join(offset_terms) or '0'})/TB',"
        f"fps={fps.numerator}/{fps.denominator},trim=end={kept:.6f}[outv]"
    )

    # Every cut point, in order. Pieces alternate between keep and drop.
    cuts = []
    pieces = []
    if segments[0][0] > 0:
        cuts.append(segments[0][0])
        pieces.append("drop")
    for i, (start, end) in enumerate(segments):
        if i > 0:
            cuts.append(start)
            pieces.append("drop")
        pieces.append("keep")
        if i < len(segments) - 1 or end < info["duration"]:
            cuts.append(end)
    if len(pieces) < len(cuts) + 1:
        pieces.append("drop")

    labels = [f"[a{i}]" for i in range(len(pieces))]
    # concat expects every piece to start at timestamp 0.
    keep = [f"{l}asetpts=PTS-STARTPTS[k{i}]" for i, (l, kind) in enumerate(zip(labels, pieces))
            if kind == "keep"]
    drop = [f"{l}anullsink" for l, kind in zip(labels, pieces) if kind == "drop"]
    keep_labels = "".join(k[k.rindex("["):] for k in keep)
    audio_pre = "afftdn=nr=15," if denoise else ""
    if cuts:
        stamps = "|".join(f"{c:.6f}" for c in cuts)
        audio = [f"[0:a]{audio_pre}asegment=timestamps='{stamps}'{''.join(labels)}",
                 *keep, *drop, f"{keep_labels}concat=n={len(keep)}:v=0:a=1[outa]"]
    else:
        audio = [f"[0:a]{audio_pre}anull[outa]"]

    return ";\n".join([video, *audio])


def encoder_args(codec, crf, preset, info):
    color = []
    for flag, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_trc"),
                      ("-colorspace", "colorspace"), ("-color_range", "color_range")):
        if info[key] and info[key] != "unknown":
            color += [flag, info[key]]

    high_bit = info["bit_depth"] > 8
    # Audio: match the source bitrate, at least 320k (512k is AAC's useful max).
    aac_bitrate = f"{min(512_000, max(320_000, info['audio_bitrate'])) // 1000}k"

    if codec == "prores":
        video = ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
                 "-pix_fmt", "yuv422p10le"]
        audio = ["-c:a", "pcm_s24le"]
    elif codec == "hevc":
        video = ["-c:v", "libx265", "-preset", preset, "-crf", str(crf), "-tag:v", "hvc1",
                 "-pix_fmt", "yuv420p10le" if high_bit else "yuv420p"]
        audio = ["-c:a", "aac", "-b:a", aac_bitrate]
    else:
        video = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                 "-pix_fmt", "yuv420p10le" if high_bit else "yuv420p"]
        audio = ["-c:a", "aac", "-b:a", aac_bitrate]

    return video + color + audio


def render(input_file, output_file, filter_text, codec, crf, preset, info):
    print("Rendering final video (this can take a while)...")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(filter_text)
        filter_path = f.name

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_file,
        "-/filter_complex", filter_path,
        "-map", "[outv]", "-map", "[outa]",
        *encoder_args(codec, crf, preset, info),
    ]
    if output_file.lower().endswith((".mp4", ".m4v", ".mov")):
        cmd += ["-movflags", "+faststart"]
    cmd.append(output_file)

    try:
        if run(cmd).returncode != 0:
            sys.exit("Error: ffmpeg failed while rendering (see the messages above).")
    finally:
        os.unlink(filter_path)


def fmt_time(seconds):
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_args():
    p = argparse.ArgumentParser(
        description="Remove silent parts from a video, keeping the original quality.")
    p.add_argument("input", help="input video (e.g. recording.mov)")
    p.add_argument("output", nargs="?", help="output file (default: <input>_trimmed.mp4)")
    p.add_argument("--noise", type=float, default=-30,
                   help="anything quieter than this (dB) counts as silence (default: -30)")
    p.add_argument("--min-silence", type=float, default=1.0,
                   help="only cut silences longer than this, in seconds (default: 1.0)")
    p.add_argument("--pad-before", type=float, default=0.2,
                   help="seconds kept before speech starts (default: 0.2)")
    p.add_argument("--pad-after", type=float, default=0.3,
                   help="seconds kept after speech ends (default: 0.3)")
    p.add_argument("--codec", choices=CODEC_DEFAULTS, default="h264",
                   help="h264 (default, plays everywhere), hevc (smaller file), "
                        "prores (near-lossless, huge file, for further editing)")
    p.add_argument("--crf", type=int,
                   help="quality for h264/hevc: lower = better (default: 16 h264, 18 hevc)")
    p.add_argument("--preset", default="slow",
                   help="x264/x265 preset: slower = better compression (default: slow)")
    p.add_argument("--no-denoise", action="store_true", help="turn off audio noise reduction")
    p.add_argument("--dry-run", action="store_true",
                   help="only show what would be cut, do not render")
    return p.parse_args()


def main():
    args = parse_args()
    if not os.path.isfile(args.input):
        sys.exit(f"Error: file not found: {args.input}")

    ext = CODEC_DEFAULTS[args.codec]["ext"]
    output = args.output or os.path.splitext(args.input)[0] + "_trimmed" + ext
    if not os.path.splitext(output)[1]:
        output += ext
    crf = args.crf if args.crf is not None else CODEC_DEFAULTS[args.codec]["crf"]

    info = probe(args.input)
    print(f"Source: {info['width']}x{info['height']} @ {float(info['fps']):.3f} fps, "
          f"{info['bit_depth']}-bit {info['pix_fmt']}, {fmt_time(info['duration'])}")

    starts, ends = detect_silence(args.input, args.noise, args.min_silence)
    segments = build_segments(info["duration"], starts, ends,
                              args.pad_before, args.pad_after, info["fps"])
    if not segments:
        sys.exit("No speech found. Try a lower --noise value (e.g. -40).")

    kept = sum(e - s for s, e in segments)
    removed = info["duration"] - kept
    print(f"Keeping {len(segments)} parts: {fmt_time(kept)} kept, "
          f"{fmt_time(removed)} removed ({removed / info['duration']:.0%} shorter)")

    if args.dry_run:
        for i, (s, e) in enumerate(segments, 1):
            print(f"  {i:4d}. {fmt_time(s):>8} -> {fmt_time(e):>8}  ({e - s:.1f}s)")
        return

    filter_text = build_filter(segments, info, denoise=not args.no_denoise)
    render(args.input, output, filter_text, args.codec, crf, args.preset, info)

    out = probe(output)
    same = (out["width"], out["height"]) == (info["width"], info["height"])
    print(f"Output: {out['width']}x{out['height']} @ {float(out['fps']):.3f} fps, "
          f"{fmt_time(out['duration'])} "
          f"({'same resolution as source' if same else 'WARNING: resolution changed!'})")
    print("Done! Saved as", output)


if __name__ == "__main__":
    main()
