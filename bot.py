import os
import re
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript
from google import genai
from summary import get_summary

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ BOT_TOKEN yoki GEMINI_API_KEY topilmadi!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

MAX_TRANSCRIPT_CHARS = 30_000
RATE_LIMIT_SECONDS = 30
user_last_request: dict[int, float] = {}

# Kanal username (@ siz)
CHANNEL_USERNAME = "sharofiddinov_n"

YOUTUBE_REGEX = (
    r'(?:https?://)?(?:www\.|m\.)?'
    r'(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)'
    r'([0-9A-Za-z_-]{11})'
)

logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> str | None:
    match = re.search(YOUTUBE_REGEX, url)
    return match.group(1) if match else None


async def is_subscribed(user_id: int) -> bool:
    """Foydalanuvchi kanalga obuna bo'lganmi yoki yo'qmi tekshiradi."""
    try:
        member = await bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}",
            user_id=user_id
        )
        return member.status not in ("left", "kicked", "restricted")
    except Exception:
        return False


def subscription_keyboard() -> types.InlineKeyboardMarkup:
    """Obuna bo'lish tugmasi."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📢 Kanalga obuna bo'lish",
        url=f"https://t.me/{CHANNEL_USERNAME}"
    )
    builder.button(
        text="✅ Obuna bo'ldim",
        callback_data="check_subscription"
    )
    builder.adjust(1)
    return builder.as_markup()


def action_keyboard(video_id: str) -> types.InlineKeyboardMarkup:
    """Tarjima va Mazmun tugmalari."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔤 Tarjima",
        callback_data=f"translate:{video_id}"
    )
    builder.button(
        text="📋 Mazmun",
        callback_data=f"summary:{video_id}"
    )
    builder.adjust(2)
    return builder.as_markup()


async def send_long_message(message: types.Message, text: str, chunk_size: int = 4000):
    for i in range(0, len(text), chunk_size):
        await message.answer(text[i:i + chunk_size])


# ================== HANDLERLAR ==================

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Assalomu alaykum!\n\n"
        "YouTube video havolasini yuboring.\n"
        "Tarjima yoki mazmun tugmasini bosing."
    )


@dp.message(F.text)
async def youtube_handler(message: types.Message):
    user_id = message.from_user.id

    # 1. Obuna tekshirish
    if not await is_subscribed(user_id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun avval kanalga obuna bo'ling!",
            reply_markup=subscription_keyboard()
        )
        return

    url = message.text.strip()
    video_id = extract_video_id(url)

    if not video_id:
        await message.answer("❌ Bu to'g'ri YouTube havolasi emas.")
        return

    # 2. Tugmalarni chiqarish
    await message.answer(
        "✅ Havola qabul qilindi. Nima qilishni tanlang:",
        reply_markup=action_keyboard(video_id)
    )


# ================== CALLBACK HANDLERLAR ==================

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    """Obuna bo'ldim tugmasi."""
    if await is_subscribed(callback.from_user.id):
        await callback.message.edit_text(
            "✅ Rahmat! Endi YouTube havolasini yuboring."
        )
    else:
        await callback.answer(
            "❌ Siz hali obuna bo'lmagansiz!",
            show_alert=True
        )


@dp.callback_query(F.data.startswith("translate:"))
async def translate_callback(callback: types.CallbackQuery):
    """Tarjima tugmasi bosilganda."""
    user_id = callback.from_user.id

    # Obuna tekshirish
    if not await is_subscribed(user_id):
        await callback.answer("❌ Avval kanalga obuna bo'ling!", show_alert=True)
        return

    # Rate limiting
    now = time.time()
    if now - user_last_request.get(user_id, 0) < RATE_LIMIT_SECONDS:
        remaining = int(RATE_LIMIT_SECONDS - (now - user_last_request[user_id]))
        await callback.answer(f"⏳ {remaining} soniya kuting.", show_alert=True)
        return

    video_id = callback.data.split(":", 1)[1]
    user_last_request[user_id] = now

    await callback.message.edit_text("⏳ Tarjima qilyapman, biroz kuting...")

    try:
        loop = asyncio.get_event_loop()
        
        # Cookie fayli orqali YouTube blokini aylanib o'tish
        import requests
        from http.cookiejar import MozillaCookieJar
        
        http_client = requests.Session()
        if os.path.exists("cookies.txt"):
            try:
                cj = MozillaCookieJar("cookies.txt")
                cj.load(ignore_discard=True, ignore_expires=True)
                http_client.cookies.update(cj)
                logger.info("Cookies yuklandi!")
            except Exception as ce:
                logger.error(f"Cookie yuklashda xato: {ce}")
                
        ytta = YouTubeTranscriptApi(http_client=http_client)

        transcript = await loop.run_in_executor(
            None,
            lambda: ytta.fetch(video_id, languages=['en', 'en-US', 'en-GB'])
        )

        full_text = " ".join([entry.text for entry in transcript])

        if len(full_text) < 10:
            raise ValueError("Matn juda qisqa")

        truncated = False
        if len(full_text) > MAX_TRANSCRIPT_CHARS:
            full_text = full_text[:MAX_TRANSCRIPT_CHARS]
            truncated = True

        prompt = (
            "Translate the following English YouTube transcript into natural, fluent Uzbek. "
            "Return ONLY the translated text with no extra commentary.\n\n"
            f"Transcript:\n{full_text}"
        )

        response = await loop.run_in_executor(
            None,
            lambda: gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
        )

        translated_text = response.text.strip()

        if not translated_text:
            raise ValueError("Tarjima bo'sh qaytdi")

        await callback.message.delete()

        if truncated:
            await callback.message.answer(
                f"⚠️ Video juda uzun. Faqat birinchi {MAX_TRANSCRIPT_CHARS:,} belgisi tarjima qilindi."
            )

        await send_long_message(callback.message, translated_text)

    except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript):
        await callback.message.edit_text("❌ Bu videoda inglizcha subtitr topilmadi.")

    except Exception as e:
        logger.error(f"Tarjima xato [{video_id}]: {e}", exc_info=True)
        await callback.message.edit_text(f"❌ Xatolik: {type(e).__name__}")


@dp.callback_query(F.data.startswith("summary:"))
async def summary_callback(callback: types.CallbackQuery):
    """Mazmun tugmasi bosilganda."""
    user_id = callback.from_user.id

    # Obuna tekshirish
    if not await is_subscribed(user_id):
        await callback.answer("❌ Avval kanalga obuna bo'ling!", show_alert=True)
        return

    # Rate limiting
    now = time.time()
    if now - user_last_request.get(user_id, 0) < RATE_LIMIT_SECONDS:
        remaining = int(RATE_LIMIT_SECONDS - (now - user_last_request[user_id]))
        await callback.answer(f"⏳ {remaining} soniya kuting.", show_alert=True)
        return

    video_id = callback.data.split(":", 1)[1]
    user_last_request[user_id] = now

    await callback.message.edit_text("⏳ Mazmun tayyorlanmoqda...")

    summary = await get_summary(video_id, gemini_client)

    await callback.message.edit_text(f"📋 <b>Video mazmuni:</b>\n\n{summary}")


# ================== ISHGA TUSHIRISH ==================

from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    logger.info(f"Dummy web server {port}-portda ishga tushdi...")
    await site.start()

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ]
    )
    logger.info("Bot ishga tushmoqda...")
    await start_dummy_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
