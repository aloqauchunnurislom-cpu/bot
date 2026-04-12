import asyncio
import os
import logging
import httpx
from google import genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 15_000
SUPADATA_BASE_URL = "https://api.supadata.ai/v1"


def _fetch_transcript_supadata(video_id: str) -> str | None:
    """Supadata API orqali YouTube subtitrini oladi."""
    api_key = os.getenv("SUPADATA_API_KEY")
    if not api_key:
        logger.error("SUPADATA_API_KEY mavjud emas!")
        return None

    url = f"{SUPADATA_BASE_URL}/transcript"
    params = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "lang": "en",
        "text": "true",
    }
    headers = {
        "x-api-key": api_key,
    }

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=30)
        if response.status_code != 200:
            logger.error(f"Supadata (summary) xato: {response.status_code}")
            return None

        data = response.json()
        content = data.get("content", "")
        return content if content and len(content) > 10 else None

    except Exception as e:
        logger.error(f"Supadata (summary) xato: {e}")
        return None


async def get_summary(video_id: str, gemini_client: genai.Client) -> str:
    """YouTube video ID si berilganda, uning mazmunini o'zbek tilida qaytaradi."""
    loop = asyncio.get_event_loop()

    full_text = await loop.run_in_executor(None, lambda: _fetch_transcript_supadata(video_id))

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
