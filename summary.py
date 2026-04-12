import asyncio
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript
from google import genai


async def get_summary(video_id: str, gemini_client: genai.Client) -> str:
    """
    YouTube video ID si berilganda, uning mazmunini o'zbek tilida qaytaradi.
    """
    loop = asyncio.get_event_loop()
    ytta = YouTubeTranscriptApi()

    try:
        # 1. Subtitrni olish
        transcript = await loop.run_in_executor(
            None,
            lambda: ytta.fetch(video_id, languages=['en', 'en-US', 'en-GB'])
        )

        full_text = " ".join([entry.text for entry in transcript])

        if len(full_text) < 10:
            return "❌ Subtitr juda qisqa, mazmun chiqarib bo'lmadi."

        # Mazmun uchun 15,000 belgi yetarli (tarjimaga qaraganda kamroq)
        if len(full_text) > 15_000:
            full_text = full_text[:15_000]

        # 2. Gemini orqali mazmun chiqarish
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

        response = await loop.run_in_executor(
            None,
            lambda: gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
        )

        summary = response.text.strip()

        if not summary:
            return "❌ Mazmun chiqarib bo'lmadi."

        return summary

    except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript):
        return "❌ Bu videoda inglizcha subtitr topilmadi."
    except Exception as e:
        return f"❌ Xatolik: {type(e).__name__}"
