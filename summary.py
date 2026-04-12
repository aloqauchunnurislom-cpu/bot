import asyncio
import os
import json
import subprocess
import glob
import logging
from google import genai

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 15_000


def _fetch_transcript_ytdlp(video_id: str) -> str | None:
    """yt-dlp yordamida YouTube subtitrini oladi."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    ytdlp_paths = ["yt-dlp", "/Users/macbookpro/Library/Python/3.9/bin/yt-dlp"]
    ytdlp_cmd = "yt-dlp"
    for path in ytdlp_paths:
        try:
            r = subprocess.run([path, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                ytdlp_cmd = path
                break
        except Exception:
            continue

    cmd = [
        ytdlp_cmd,
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en",
        "--skip-download",
        "--sub-format", "json3",
        "-o", f"/tmp/yt_sum_{video_id}",
    ]

    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(cookie_path):
        cmd += ["--cookies", cookie_path]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"yt-dlp (summary) xato: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"yt-dlp subprocess xato: {e}")
        return None

    pattern = f"/tmp/yt_sum_{video_id}*.json3"
    files = glob.glob(pattern)
    if not files:
        return None

    try:
        with open(files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        texts = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t and t != "\n":
                    texts.append(t)

        for fpath in files:
            try:
                os.remove(fpath)
            except Exception:
                pass

        full_text = " ".join(texts)
        return full_text if len(full_text) > 10 else None

    except Exception as e:
        logger.error(f"Subtitr (summary) faylini o'qishda xato: {e}")
        return None


async def get_summary(video_id: str, gemini_client: genai.Client) -> str:
    """YouTube video ID si berilganda, uning mazmunini o'zbek tilida qaytaradi."""
    loop = asyncio.get_event_loop()

    full_text = await loop.run_in_executor(None, lambda: _fetch_transcript_ytdlp(video_id))

    if not full_text:
        return "❌ Bu videoda inglizcha subtitr topilmadi."

    if len(full_text) > MAX_SUMMARY_CHARS:
        full_text = full_text[:MAX_SUMMARY_CHARS]

    prompt = (
        "Read the following English YouTube video transcript carefully.\n"
        "Then write a clear and concise summary in Uzbek language.\n\n"
        "Requirements:\n"
        "- Write 5-8 sentences maximum\n"
        "- Cover the main topic and key points\n"
        "- Use natural, fluent Uzbek\n"
        "- Start with: 'Bu videoda...'\n"
        "- Return ONLY the summary, nothing else\n\n"
        f"Transcript:\n{full_text}"
    )

    try:
        response = await loop.run_in_executor(
            None,
            lambda: gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
        )

        summary = response.text.strip()
        return summary if summary else "❌ Mazmun chiqarib bo'lmadi."

    except Exception as e:
        logger.error(f"Gemini summary xato: {e}")
        return f"❌ Xatolik: {type(e).__name__}"
