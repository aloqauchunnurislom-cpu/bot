import os
import re
import asyncio
import logging
import time
import json
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from google import genai
from summary import get_summary
import cache

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ BOT_TOKEN yoki GEMINI_API_KEY topilmadi!")

if not SUPADATA_API_KEY:
    logger_init = logging.getLogger(__name__)
    logger_init.warning("⚠️ SUPADATA_API_KEY topilmadi! Subtitr olish ishlamasligi mumkin.")

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

SUPADATA_BASE_URL = "https://api.supadata.ai/v1"


def fetch_transcript_supadata(video_id: str) -> str | None:
    """Supadata API orqali YouTube subtitrini oladi."""
    if not SUPADATA_API_KEY:
        logger.error("SUPADATA_API_KEY mavjud emas!")
        return None

    url = f"{SUPADATA_BASE_URL}/transcript"
    params = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "lang": "en",
        "text": "true",
    }
    headers = {
        "x-api-key": SUPADATA_API_KEY,
    }

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=30)
        logger.info(f"Supadata status: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"Supadata xato: {response.status_code} - {response.text[:300]}")
            return None

        data = response.json()
        content = data.get("content", "")

        if not content or len(content) < 10:
            logger.warning(f"Supadata: Subtitr juda qisqa yoki yo'q [{video_id}]")
            return None

        logger.info(f"Supadata: Subtitr olindi [{video_id}]: {len(content)} belgi")
        return content

    except Exception as e:
        logger.error(f"Supadata xato: {e}")
        return None


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
        # 1. Keshdan tekshirish
        cached = cache.get(video_id, "translation")
        if cached:
            await callback.message.delete()
            await callback.message.answer("⚡ <i>Keshdan olindi</i>", parse_mode="HTML")
            await send_long_message(callback.message, cached)
            return

        loop = asyncio.get_event_loop()

        full_text = await loop.run_in_executor(None, lambda: fetch_transcript_supadata(video_id))

        if not full_text or len(full_text) < 10:
            await callback.message.edit_text("❌ Bu videoda inglizcha subtitr topilmadi.")
            return

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

        # 2. Keshga saqlash
        cache.set(video_id, "translation", translated_text)

        await callback.message.delete()

        if truncated:
            await callback.message.answer(
                f"⚠️ Video juda uzun. Faqat birinchi {MAX_TRANSCRIPT_CHARS:,} belgisi tarjima qilindi."
            )

        await send_long_message(callback.message, translated_text)

    except Exception as e:
        logger.error(f"Tarjima xato [{video_id}]: {e}", exc_info=True)
        await callback.message.edit_text(f"❌ Xatolik yuz berdi. Keyinroq urinib ko'ring.")


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

    # Keshdan tekshirish
    cached = cache.get(video_id, "summary")
    if cached:
        await callback.message.edit_text(f"📋 <b>Video mazmuni:</b>\n\n{cached} ⚡ <i>(kesh)</i>")
        return

    summary = await get_summary(video_id, gemini_client)

    # Muvaffaqiyatli bo'lsa keshga saqlash
    if not summary.startswith("❌"):
        cache.set(video_id, "summary", summary)

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

# ─── Self-ping (Render uxlamasligi uchun) ────────────────────────────
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")

async def self_ping_forever():
    """Har 10 daqiqada o'z-o'ziga ping — Render uxlamaydi."""
    if not RENDER_URL:
        logger.info("ℹ️ RENDER_EXTERNAL_URL topilmadi — self-ping o'chirilgan")
        return

    logger.info("🏓 Self-ping ishga tushdi: har 10 daqiqada → %s", RENDER_URL)
    while True:
        try:
            await asyncio.sleep(600)  # 10 daqiqa
            async with httpx.AsyncClient() as client:
                resp = await client.get(RENDER_URL, timeout=10)
                logger.info("🏓 Self-ping: %s (status=%d)", RENDER_URL, resp.status_code)
        except Exception as e:
            logger.warning("🏓 Self-ping xatolik: %s", e)

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
    asyncio.create_task(self_ping_forever())  # Self-ping ni fonda ishga tushirish
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
