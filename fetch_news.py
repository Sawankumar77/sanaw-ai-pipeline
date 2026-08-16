"""
Step 1: Fetch latest AI/tech news headlines from free RSS feeds.
No API key needed. Runs daily via GitHub Actions cron.
"""
import feedparser

# Free RSS feeds, no key required
FEEDS = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
}

# Skip stories matching these terms -- this is an unattended pipeline with
# no human review before publishing, so anything touching child sexual
# abuse material, graphic violence, suicide/self-harm, or sexual assault
# gets filtered out automatically rather than turned into reel content.
SENSITIVE_KEYWORDS = [
    "child sexual", "csam", "child abuse", "child exploitation",
    "explicit imagery of a", "sexual abuse material",
    "suicide", "self-harm", "self harm",
    "rape", "sexual assault", "molest",
    "school shooting", "mass shooting", "massacre",
    "graphic video", "beheading",
]


def is_sensitive(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(keyword in text for keyword in SENSITIVE_KEYWORDS)


def fetch_latest_stories(max_per_feed=3):
    stories = []
    for source, url in FEEDS.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_per_feed]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")[:400]
            if is_sensitive(title, summary):
                print(f"[filtered] skipping sensitive story: {title}")
                continue
            stories.append({
                "source": source,
                "title": title,
                "summary": summary,
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
            })
    return stories


if __name__ == "__main__":
    stories = fetch_latest_stories()
    print(f"Fetched {len(stories)} stories\n")
    for s in stories[:8]:
        print(f"[{s['source']}] {s['title']}")
