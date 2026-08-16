"""
post_to_instagram.py

Publishes a local .mp4 as an Instagram Reel via the Instagram Graph API
(Instagram Login flow -- graph.instagram.com endpoints).

Instagram's API requires the video to be reachable at a PUBLIC URL before
it will publish it -- it does not accept direct file uploads. Since we're
keeping this free, we host the file on a GitHub Release in your own repo
(works free as long as the repo is public).

Env vars required:
    IG_USER_ID        - e.g. 27951192607899417
    IG_ACCESS_TOKEN   - your long-lived Instagram token
    GITHUB_TOKEN       - a repo-scoped token (GitHub Actions provides this
                          automatically as secrets.GITHUB_TOKEN)
    GITHUB_REPO        - "yourusername/sanaw-ai-pipeline"

Usage:
    python post_to_instagram.py --video final_reel.mp4 --caption "आज की AI खबर..."
"""

import argparse
import os
import sys
import time
import requests

IG_USER_ID = os.environ.get("IG_USER_ID")
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("REPO_FULL_NAME")  # e.g. "Sawankumar77/sanaw-ai-pipeline"
# (named REPO_FULL_NAME, not GITHUB_REPO, because GitHub Actions blocks any
# secret name starting with "GITHUB_" -- that prefix is reserved)

GRAPH_BASE = "https://graph.instagram.com/v21.0"


# ---------------------------------------------------------------------------
# 1. Host the video publicly via a GitHub Release asset
# ---------------------------------------------------------------------------
def upload_to_github_release(video_path: str) -> str:
    """
    Creates a new GitHub Release (tagged with a timestamp) in your repo and
    uploads the video as a release asset. Returns the public download URL.

    Requires your repo to be PUBLIC -- private repo asset URLs are not
    reachable by Instagram's fetcher.
    """
    if not (GITHUB_TOKEN and GITHUB_REPO):
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPO env vars are required to host the video publicly.")

    tag = f"reel-{int(time.time())}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    # Create the release
    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases",
        headers=headers,
        json={"tag_name": tag, "name": tag, "draft": False, "prerelease": False},
        timeout=30,
    )
    resp.raise_for_status()
    release = resp.json()
    upload_url = release["upload_url"].split("{")[0]  # strip templated part

    # Upload the video as a release asset
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            f"{upload_url}?name={filename}",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f,
            timeout=120,
        )
    upload_resp.raise_for_status()
    asset = upload_resp.json()
    return asset["browser_download_url"]


# ---------------------------------------------------------------------------
# 2. Create the media container (Instagram processes the video async)
# ---------------------------------------------------------------------------
def create_reel_container(video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def wait_for_container_ready(container_id: str, timeout_s: int = 300) -> None:
    """Instagram processes the video asynchronously -- poll until it's FINISHED."""
    start = time.time()
    while time.time() - start < timeout_s:
        resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video (container {container_id}).")
        time.sleep(10)
    raise TimeoutError(f"Container {container_id} did not finish processing within {timeout_s}s.")


# ---------------------------------------------------------------------------
# 3. Publish the container as a live Reel
# ---------------------------------------------------------------------------
def publish_container(container_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media_publish",
        data={"creation_id": container_id, "access_token": IG_ACCESS_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def post_reel(video_path: str, caption: str) -> str:
    print("[1/4] Uploading video to GitHub Release for public hosting...")
    video_url = upload_to_github_release(video_path)
    print(f"      -> {video_url}")

    print("[2/4] Creating Instagram media container...")
    container_id = create_reel_container(video_url, caption)
    print(f"      -> container_id={container_id}")

    print("[3/4] Waiting for Instagram to finish processing the video...")
    wait_for_container_ready(container_id)

    print("[4/4] Publishing...")
    media_id = publish_container(container_id)
    print(f"[ok] Published! media_id={media_id}")
    return media_id


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--caption", required=True)
    args = p.parse_args()

    if not (IG_USER_ID and IG_ACCESS_TOKEN):
        print("Missing IG_USER_ID or IG_ACCESS_TOKEN env vars.", file=sys.stderr)
        sys.exit(1)

    post_reel(args.video, args.caption)


if __name__ == "__main__":
    main()
