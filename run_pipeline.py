"""
run_pipeline.py

The orchestrator: ties the whole pipeline into ONE command.
  1. Fetch latest AI/tech news (RSS, free)
  2. Pick a headline not already posted today (dedupe via posted_headlines.json)
  3. Generate a Hindi script + voiceover (Gemini free tier + edge-tts, free)
  4. Assemble the reel (Pexels stock footage or gradient fallback, free)
  5. Post it to Instagram (Graph API, free)

Run manually:
    python run_pipeline.py

Run in GitHub Actions: this is what the workflow calls, once per scheduled
trigger (see .github/workflows/daily_reels.yml).
"""
import json
import os
import sys
from pathlib import Path

from fetch_news import fetch_latest_stories
from generate_script_and_voice import process_story
from assemble_reel import assemble
from post_to_instagram import post_reel

DEDUPE_FILE = Path("posted_headlines.json")
OUTPUT_DIR = Path("output")


def load_posted_titles() -> set:
    if DEDUPE_FILE.exists():
        return set(json.loads(DEDUPE_FILE.read_text(encoding="utf-8")))
    return set()


def save_posted_title(title: str):
    posted = load_posted_titles()
    posted.add(title)
    # keep only the last 200 to stop the file growing forever
    posted = set(list(posted)[-200:])
    DEDUPE_FILE.write_text(json.dumps(list(posted), ensure_ascii=False, indent=2), encoding="utf-8")


def pick_unposted_story(stories: list) -> dict | None:
    posted = load_posted_titles()
    for story in stories:
        if story["title"] not in posted:
            return story
    return None


def build_caption(script_text: str) -> str:
    # Instagram caption: script text + a few hashtags. Keep it short.
    hashtags = "#AI #TechNews #ArtificialIntelligence #TechIndia #sanaw_ai"
    return f"{script_text}\n\n{hashtags}"


def run():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("[1/5] Fetching latest AI/tech news...")
    stories = fetch_latest_stories(max_per_feed=3)
    if not stories:
        print("No stories fetched -- check RSS feed connectivity.", file=sys.stderr)
        sys.exit(1)
    print(f"      Fetched {len(stories)} stories")

    print("[2/5] Picking an un-posted story...")
    story = pick_unposted_story(stories)
    if story is None:
        print("All fetched stories were already posted recently. Nothing to do.")
        sys.exit(0)
    print(f"      -> {story['title']}")

    print("[3/5] Generating Hindi script + voiceover...")
    index = int(os.environ.get("RUN_INDEX", "0"))  # 0-3, set by the workflow per daily slot
    result = process_story(story, index=index)
    script_text = result["script"]
    audio_path = result["audio_path"]
    print(f"      Script: {script_text[:80]}...")

    print("[4/5] Assembling the reel...")
    video_path = str(OUTPUT_DIR / f"reel_{index}.mp4")
    keywords = " ".join(story["title"].split()[:5])
    assemble(audio_path, script_text, keywords, video_path)
    print(f"      -> {video_path}")

    print("[5/5] Posting to Instagram...")
    caption = build_caption(script_text)
    post_reel(video_path, caption)

    save_posted_title(story["title"])
    print("[done] Pipeline run complete.")


if __name__ == "__main__":
    run()
