import os
import re
import asyncio
import logging
import time
import json
import subprocess
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
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


def fetch_transcript_ytdlp(video_id: str) -> str | None:
    """yt-dlp yordamida YouTube subtitrini oladi (cookies.txt bilan)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # yt-dlp path: Railway'da pip install bilan o'rnatiladi
    ytdlp_paths = ["yt-dlp", "/Users/macbookpro/Library/Python/3.9/bin/yt-dlp"]
    ytdlp_cmd = "yt-dlp"
    for path in ytdlp_paths:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
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
        "-o", f"/tmp/yt_sub_{video_id}",
        "--proxy", "socks5://127.0.0.1:9050",
    ]

    # Cookie fayl mavjud bo'lsa qo'shamiz
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(cookie_path):
        cmd += ["--cookies", cookie_path]
        logger.info("cookies.txt topildi va ishlatilmoqda")
    else:
        logger.warning("cookies.txt topilmadi! Tor orqali urinib ko'riladi.")

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        logger.info(f"yt-dlp stdout: {result.stdout[:300]}")
        if result.returncode != 0:
            logger.error(f"yt-dlp xato: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timeout!")
        return None
    except Exception as e:
        logger.error(f"yt-dlp ishlatishda xato: {e}")
        return None

    # JSON subtitr faylini o'qish
    import glob
    pattern = f"/tmp/yt_sub_{video_id}*.json3"
    files = glob.glob(pattern)
    if not files:
        logger.error(f"Subtitr fayl topilmadi: {pattern}")
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
        
        # Tozalash
        for fpath in files:
            try:
                os.remove(fpath)
            except Exception:
                pass
        
        full_text = " ".join(texts)
        logger.info(f"Subtitr olindi: {len(full_text)} belgi")
        return full_text if len(full_text) > 10 else None

    except Exception as e:
        logger.error(f"Subtitr faylini o'qishda xato: {e}")
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
        loop = asyncio.get_event_loop()

        full_text = await loop.run_in_executor(None, lambda: fetch_transcript_ytdlp(video_id))

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
