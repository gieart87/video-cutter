import subprocess
import sys
import re
import tempfile
import os

MIN_SILENCE = 1  # detik
NOISE_LEVEL = -30  # dB


def detect_silence(input_file):
    print("Detecting silence...")

    cmd = [
        "ffmpeg",
        "-i", input_file,
        "-af", f"silencedetect=noise={NOISE_LEVEL}dB:d={MIN_SILENCE}",
        "-f", "null",
        "-"
    ]

    result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    output = result.stderr

    silence_starts = []
    silence_ends = []

    for line in output.split("\n"):
        if "silence_start" in line:
            silence_starts.append(float(re.search(r"silence_start: (\d+\.?\d*)", line).group(1)))
        if "silence_end" in line:
            silence_ends.append(float(re.search(r"silence_end: (\d+\.?\d*)", line).group(1)))

    return silence_starts, silence_ends


def get_duration(input_file):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    return float(result.stdout.strip())


def build_segments(duration, silence_starts, silence_ends):
    segments = []
    last_end = 0

    BUFFER_BEFORE = 0.2   # sedikit sebelum bicara
    BUFFER_AFTER = 0.3    # 1 detik setelah bicara

    for start, end in zip(silence_starts, silence_ends):

        segment_end = max(start - BUFFER_BEFORE, last_end)

        if segment_end > last_end:
            segments.append((last_end, segment_end + BUFFER_AFTER))

        last_end = end

    if last_end < duration:
        segments.append((last_end, duration))

    return segments


def render_video(input_file, output_file, segments):
    print("Rendering final video...")

    filter_parts = []
    concat_inputs = ""

    for i, (start, end) in enumerate(segments):
        filter_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,afftdn=nr=15[a{i}];"
        )
        concat_inputs += f"[v{i}][a{i}]"

    filter_complex = "".join(filter_parts) + \
        f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        output_file
    ]

    subprocess.run(cmd, check=True)


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 auto_trim_fast.py input.mov output.mp4")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    silence_starts, silence_ends = detect_silence(input_file)
    duration = get_duration(input_file)
    segments = build_segments(duration, silence_starts, silence_ends)

    if not segments:
        print("No non-silent segments found.")
        sys.exit(1)

    render_video(input_file, output_file, segments)

    print("Done! Saved as", output_file)


if __name__ == "__main__":
    main()
