"""
Step 2 & 3: Turn a news story into a punchy 30-45s Hindi reel script,
then generate a free Hindi voiceover.

Script generation: uses Google's Gemini API (free tier - generous daily quota,
no cost at this volume: https://ai.google.dev/pricing). This is a legitimate
AI provider API, not a scraping/ToS workaround.

Voiceover: edge-tts (Microsoft Edge's free TTS engine, no API key, no cost).
"""
import asyncio
import edge_tts
import requests
import os
import json

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # free key from https://aistudio.google.com/apikey
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"

HINDI_VOICES = ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]  # male / female, free

SCRIPT_PROMPT = """Tum ek Hindi tech/AI Instagram Reels script writer ho.
Neeche diye gaye news story se ek punchy, informative 30-40 second Hindi reel script banao.

Rules:
- Pehla line ek strong hook ho (curiosity ya shock value)
- 2-3 key points, simple Hindi mein, Gen-Z friendly tone
- Last line mein ek short CTA (jaise "follow karo AI updates ke liye")
- Sirf spoken script do, koi stage direction nahi
- Total 70-90 words, taaki 30-40 second mein bola ja sake

News story:
Title: {title}
Summary: {summary}

Hindi script:"""


def generate_hindi_script(title, summary):
    prompt = SCRIPT_PROMPT.format(title=title, summary=summary)
    resp = requests.post(
        GEMINI_URL,
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def text_to_speech(text, output_path, voice="hi-IN-MadhurNeural"):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def process_story(story, index, voice=None):
    voice = voice or HINDI_VOICES[index % 2]  # alternate male/female
    script = generate_hindi_script(story["title"], story["summary"])
    audio_path = f"output/reel_{index}_voice.mp3"
    os.makedirs("output", exist_ok=True)
    asyncio.run(text_to_speech(script, audio_path, voice))
    return {"script": script, "audio_path": audio_path, "voice": voice, "source": story}


if __name__ == "__main__":
    # Mock story for local testing without live news fetch
    mock_story = {
        "title": "OpenAI announces new reasoning model",
        "summary": "OpenAI has released a new model with improved step-by-step reasoning capabilities for complex tasks.",
    }
    result = process_story(mock_story, index=0)
    print(json.dumps({k: v for k, v in result.items() if k != "source"}, indent=2, ensure_ascii=False))
