"""
assemble_reel.py

Takes:
  - a Hindi voiceover .mp3/.wav (from generate_script_and_voice.py)
  - the script text (for caption timing)
  - a headline / keyword (to pick background footage)

Produces:
  - a 1080x1920 (9:16) .mp4 reel with background video/motion graphics,
    the voiceover, and animated burned-in Hindi captions.

Free-tool design:
  - Background footage: Pexels API (free tier, needs PEXELS_API_KEY env var)
    with a generated-gradient fallback if no key / no match / rate-limited.
  - Captions: ffmpeg 'ass' subtitle filter, burned in, synced to audio
    duration by splitting script into caption chunks.
  - All video work done via ffmpeg subprocess calls (no moviepy dependency).

Usage:
    python assemble_reel.py \
        --audio voice_1.mp3 \
        --script "script text here..." \
        --keywords "artificial intelligence chip" \
        --out reel_1.mp4
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import requests

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"

WIDTH, HEIGHT = 1080, 1920
FPS = 30


ASSETS_DIR = Path(__file__).parent / "assets"
BUNDLED_FONT = ASSETS_DIR / "NotoSansDevanagari-Bold.ttf"
ASS_FONT_FAMILY_NAME = "Noto Sans Devanagari"  # must match the font's actual internal name


def _resolve_font() -> str:
    """
    Pick a font path for the watermark (drawtext filter) that works both on
    your local Windows machine and on GitHub Actions' Ubuntu runners.

    We ALWAYS prefer the bundled font (assets/NotoSansDevanagari-Bold.ttf)
    over system fonts, because:
      1. It's guaranteed to exist identically on your PC and in CI -- no
         dependency on whether Windows Hindi language pack is installed.
      2. Arial (the old fallback) has NO Devanagari glyphs, so Hindi text
         would render as empty boxes even if the path resolved correctly.
    """
    if BUNDLED_FONT.exists():
        path = str(BUNDLED_FONT)
    else:
        # last-resort system fallbacks, Latin-only -- watermark text is
        # English ("sanaw_ai") so this is fine for the watermark specifically,
        # but captions need the bundled font, see build_ass_captions()
        for candidate in ["C:/Windows/Fonts/arialbd.ttf",
                           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
            if Path(candidate).exists():
                path = candidate
                break
        else:
            raise FileNotFoundError(
                "No font found at all. Add assets/NotoSansDevanagari-Bold.ttf "
                "to the repo (free: https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari)."
            )
    # normalize slashes and escape the drive-letter colon for ffmpeg's filter parser
    return path.replace("\\", "/").replace(":", "\\:")


FONT = _resolve_font()


# ---------------------------------------------------------------------------
# 1. Get audio duration
# ---------------------------------------------------------------------------
def get_audio_duration(audio_path: str) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ---------------------------------------------------------------------------
# 2. Background: Pexels stock video, else generated motion graphic
# ---------------------------------------------------------------------------
def fetch_pexels_background(keywords: str, duration: float, workdir: Path) -> str | None:
    if not PEXELS_API_KEY:
        return None
    try:
        resp = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": keywords, "orientation": "portrait", "per_page": 10},
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        if not videos:
            return None
        random.shuffle(videos)
        for v in videos:
            files = sorted(
                v.get("video_files", []),
                key=lambda f: f.get("height", 0), reverse=True,
            )
            portrait_files = [f for f in files if f.get("height", 0) >= f.get("width", 1)]
            candidates = portrait_files or files
            if not candidates:
                continue
            url = candidates[0]["link"]
            local_path = workdir / "bg_raw.mp4"
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return str(local_path)
    except Exception as e:
        print(f"[warn] Pexels fetch failed: {e}", file=sys.stderr)
        return None
    return None


def generate_motion_background(duration: float, workdir: Path) -> str:
    """
    Zero-dependency animated background using ffmpeg lavfi:
    a slowly shifting gradient plus soft moving particles.
    Guaranteed to work with no external API / no signup.
    """
    out_path = workdir / "bg_generated.mp4"
    c1 = random.choice(["0x0f2027", "0x1a0033", "0x001e3c", "0x1b0033"])
    c2 = random.choice(["0x2c5364", "0x6a0dad", "0x00509d", "0x8e2de2"])
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", (
            f"gradients=s={WIDTH}x{HEIGHT}:c0={c1}:c1={c2}"
            f":x0=0:y0=0:x1={WIDTH}:y1={HEIGHT}"
            f":duration={duration}:speed=0.03:rate={FPS}"
        ),
        "-vf", "noise=alls=6:allf=t+u",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(out_path)


def prep_background(bg_source: str, duration: float, workdir: Path) -> str:
    """Crop/scale any background source to 1080x1920 and loop/trim to duration."""
    out_path = workdir / "bg_final.mp4"
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", bg_source,
        "-t", str(duration),
        "-vf",
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS}",
        "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(out_path)


# ---------------------------------------------------------------------------
# 2b. Stitch multiple avatar clips (e.g. Veo-generated) end-to-end
# ---------------------------------------------------------------------------
def stitch_avatar_clips(clip_paths: list[str], workdir: Path) -> tuple[str, float]:
    """
    Concatenate several short avatar clips (from Gemini/Veo, ~8-10s each,
    downloaded manually) into one continuous background clip.

    Each clip is first normalized to the same resolution/fps/codec, since
    ffmpeg's concat demuxer requires matching streams -- Veo exports can
    vary slightly in encoding between generations.

    Returns (path_to_stitched_video, total_duration_seconds).
    """
    normalized = []
    for i, clip in enumerate(clip_paths):
        norm_path = workdir / f"avatar_norm_{i}.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", clip,
            "-vf",
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an",  # drop each clip's own audio -- your Hindi voiceover replaces it
            str(norm_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        normalized.append(norm_path)

    concat_list = workdir / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in normalized:
            # ffmpeg concat demuxer needs forward slashes and escaped quotes
            f.write(f"file '{str(p).replace(chr(92), '/')}'" + "\n")

    stitched_path = workdir / "avatar_stitched.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(stitched_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    total_duration = get_audio_duration(str(stitched_path))  # works for video too via ffprobe
    return str(stitched_path), total_duration


def match_avatar_to_voiceover(stitched_video: str, voice_duration: float, workdir: Path) -> str:
    """
    Your Veo clips (say, 4 x 9s = 36s) usually won't exactly match your
    voiceover's length (say, 34s). Trim or loop-extend to match exactly,
    so audio and video end at the same time instead of one cutting off early.
    """
    out_path = workdir / "avatar_matched.mp4"
    stitched_duration = get_audio_duration(stitched_video)

    if stitched_duration >= voice_duration:
        # trim the tail
        cmd = ["ffmpeg", "-y", "-i", stitched_video, "-t", str(voice_duration),
               "-c", "copy", str(out_path)]
    else:
        # loop the stitched clip until it covers the voiceover, then trim exactly
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", stitched_video,
               "-t", str(voice_duration), "-c:v", "libx264", "-preset", "fast",
               "-crf", "18", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(out_path)


# ---------------------------------------------------------------------------
# 3. Captions: build an .ass file with pop-in animated chunks
# ---------------------------------------------------------------------------
def build_ass_captions(script_text: str, duration: float, workdir: Path) -> str:
    words = script_text.split()
    chunk_size = 5
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    if not chunks:
        chunks = [script_text]

    per_chunk = duration / len(chunks)

    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reel,{ASS_FONT_FAMILY_NAME},72,&H00FFFFFF,&H00000000,&H80000000,-1,0,1,4,2,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    t = 0.0
    for chunk in chunks:
        start, end = t, t + per_chunk
        text = chunk.replace("\n", " ")
        line = (
            f"Dialogue: 0,{ts(start)},{ts(end)},Reel,,0,0,0,,"
            f"{{\\fad(120,80)\\t(0,200,\\fscx100\\fscy100)\\fscx70\\fscy70}}{text}"
        )
        events.append(line)
        t = end

    ass_path = workdir / "captions.ass"
    ass_path.write_text(header + "\n".join(events), encoding="utf-8")
    return str(ass_path)


# ---------------------------------------------------------------------------
# 4. Final assembly: background + audio + burned captions + branding
# ---------------------------------------------------------------------------
def assemble(audio_path: str, script_text: str, keywords: str, out_path: str,
             avatar_clips: list[str] | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        duration = get_audio_duration(audio_path)

        if avatar_clips:
            # Your Veo/Gemini avatar clips, stitched end-to-end and matched
            # to the voiceover's exact length
            stitched, _ = stitch_avatar_clips(avatar_clips, workdir)
            bg_final = match_avatar_to_voiceover(stitched, duration, workdir)
        else:
            bg_raw = fetch_pexels_background(keywords, duration, workdir)
            if bg_raw is None:
                bg_raw = generate_motion_background(duration, workdir)
            bg_final = prep_background(bg_raw, duration, workdir)

        ass_path = build_ass_captions(script_text, duration, workdir)
        ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        # fontsdir tells libass to load the font directly from our bundled
        # file, instead of relying on it being installed as a system font --
        # this is what actually makes Hindi captions render reliably both
        # locally and on GitHub Actions.
        fontsdir_escaped = str(ASSETS_DIR).replace("\\", "/").replace(":", "\\:")

        watermark = "sanaw_ai"

        # NOTE: fontfile value is wrapped in single quotes -- this was the
        # actual crash bug. Without quotes, ffmpeg's filter parser chokes
        # on the escaped colon inside the path (drive letter "C\:").
        vf = (
            f"subtitles='{ass_escaped}':fontsdir='{fontsdir_escaped}',"
            f"drawtext=fontfile='{FONT}':text='{watermark}':fontsize=40:"
            f"fontcolor=white@0.6:x=(w-text_w)/2:y=h-140"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", bg_final,
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-r", str(FPS),
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError("ffmpeg assembly failed")

    print(f"[ok] wrote {out_path} ({duration:.1f}s)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--audio", required=True)
    p.add_argument("--script", required=True)
    p.add_argument("--keywords", default="technology abstract")
    p.add_argument("--out", required=True)
    p.add_argument(
        "--avatar-clips", nargs="+", default=None,
        help="Path(s) to your Gemini/Veo avatar clips, in order. "
             "If given, these replace the Pexels/gradient background entirely."
    )
    args = p.parse_args()
    assemble(args.audio, args.script, args.keywords, args.out, avatar_clips=args.avatar_clips)


if __name__ == "__main__":
    main()
