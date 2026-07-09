import os
import re
import asyncio
import uuid
from dotenv import load_dotenv
# To'g'ri shakli:
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ChatMemberStatus
import yt_dlp

load_dotenv()

# Telegram Bot Token (.env faylida yoki muhit o'zgaruvchisida)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi. .env faylini yoki muhit o'zgaruvchisini sozlang.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Yuklab olinadigan fayllar uchun vaqtinchalik papka
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Yuklab olingan fayllarni vaqtincha saqlash (callback uchun)
downloaded_files = {}

# Rasm kengaytmalari
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
# Video kengaytmalari
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.3gp'}
# Audio kengaytmalari
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.ogg', '.wav', '.flac', '.aac', '.opus'}

# Majburiy obuna kanallari
REQUIRED_CHANNELS = [
    {"username": "programmerking", "title": "Programmer King", "url": "https://t.me/programmerking"},
    {"username": "Skromniy_ku_user", "title": "Skromniy_ku_user", "url": "https://t.me/Skromniy_ku_user"},
    {"username": "Coinsscc", "title": "Coinsscc", "url": "https://t.me/Coinsscc"},
]

SUBSCRIPTION_MESSAGE = (
    "🔔 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n"
    "Obuna bo'lgach, \"✅ Obunani tekshirish\" tugmasini bosing."
)


def get_media_type(file_path: str) -> str:
    """Fayl kengaytmasiga qarab media turini aniqlash"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "photo"
    elif ext in VIDEO_EXTENSIONS:
        return "video"
    elif ext in AUDIO_EXTENSIONS:
        return "audio"
    else:
        return "document"


# Cookie talab qiladigan saytlar (YouTube/TikTok uchun cookie KERAK EMAS)
COOKIE_SITES = ('instagram.com', 'facebook.com', 'fb.watch', 'threads.net')


def needs_cookies(url: str) -> bool:
    """Faqat Instagram/Facebook kabi saytlar uchun cookie kerak"""
    url_lower = url.lower()
    return any(site in url_lower for site in COOKIE_SITES)


def get_cookies_browser():
    """Brauzer cookie'larini haqiqatan yuklab ko'rish — Chrome ochiq bo'lsa o'tkazib yuboriladi"""
    from yt_dlp.cookies import extract_cookies_from_browser

    for browser in ['edge', 'firefox', 'brave', 'opera', 'chromium', 'chrome']:
        try:
            extract_cookies_from_browser(browser, None, None)
            return browser
        except Exception:
            continue
    return None


def _resolve_downloaded_file(ydl, info) -> str:
    """Yuklab olingan fayl yo'lini aniqlash (merge yoki nom o'zgarganda)"""
    filename = ydl.prepare_filename(info)
    if os.path.exists(filename):
        return filename

    base, _ = os.path.splitext(filename)
    for ext in ['.mp4', '.mkv', '.webm', '.mov', '.m4a', '.mp3', '.jpg', '.jpeg', '.png', '.webp']:
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate

    vid = info.get('id')
    if vid:
        for name in os.listdir(DOWNLOAD_DIR):
            if name.startswith(str(vid)):
                return os.path.join(DOWNLOAD_DIR, name)

    return filename


def _extract_single_entry(info):
    """Playlist/carousel bo'lsa birinchi elementni olish"""
    if info and info.get('_type') == 'playlist':
        entries = [e for e in info.get('entries', []) if e is not None]
        if entries:
            return entries[0]
    return info


def _build_ytdlp_opts(use_cookies: bool = False, cookies_browser: str = None, video_format: bool = True) -> dict:
    """yt-dlp sozlamalarini yaratish"""
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
        'no_warnings': True,
        'quiet': True,
        'restrictfilenames': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            ),
        },
        'max_filesize': 50 * 1024 * 1024,
    }

    if video_format:
        # Telegram uchun mp4 format (bitta fayl, merge kerak emas)
        opts['format'] = (
            'best[ext=mp4][height<=1080]/'
            'best[ext=mp4]/'
            'best[height<=1080]/'
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            'bestvideo+bestaudio/'
            'best'
        )
        opts['merge_output_format'] = 'mp4'
    else:
        opts['format'] = 'best'

    if use_cookies and cookies_browser:
        opts['cookiesfrombrowser'] = (cookies_browser,)

    return opts


def _download_with_opts(url: str, opts: dict) -> dict:
    """Berilgan sozlamalar bilan yuklab olish"""
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        info = _extract_single_entry(info)

        if info is None:
            raise Exception("Ma'lumot olishda xatolik")

        filename = _resolve_downloaded_file(ydl, info)
        if not os.path.exists(filename):
            raise yt_dlp.utils.DownloadError(f"Fayl yuklanmadi: {filename}")

        title = info.get('title') or info.get('description', 'Media')[:100]
        media_type = get_media_type(filename)

        return {
            'file_path': filename,
            'title': title,
            'media_type': media_type,
        }


def download_media(url: str) -> dict:
    """yt-dlp yordamida media yuklab olish funksiyasi. Turi va yo'lini qaytaradi."""
    attempts = []

    if needs_cookies(url):
        cookies_browser = get_cookies_browser()
        if cookies_browser:
            attempts.append(_build_ytdlp_opts(use_cookies=True, cookies_browser=cookies_browser))
        attempts.append(_build_ytdlp_opts(use_cookies=False))
    else:
        # YouTube, TikTok va boshqalar — cookiesiz
        attempts.append(_build_ytdlp_opts(use_cookies=False))

    # Oxirgi urinish — eng keng format
    attempts.append(_build_ytdlp_opts(use_cookies=False, video_format=False))

    last_error = None
    for opts in attempts:
        try:
            return _download_with_opts(url, opts)
        except Exception as e:
            last_error = e
            print(f"Yuklab olish urinishi muvaffaqiyatsiz: {e}")
            continue

    if last_error:
        raise last_error
    raise Exception("Yuklab olishda xatolik")


def get_subscription_keyboard() -> InlineKeyboardMarkup:
    """Majburiy obuna kanallari va tekshirish tugmasi"""
    buttons = [
        [InlineKeyboardButton(text=f"📢 {ch['title']}", url=ch['url'])]
        for ch in REQUIRED_CHANNELS
    ]
    buttons.append([
        InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def check_user_subscription(user_id: int) -> tuple[bool, list]:
    """Foydalanuvchi barcha kanallarga obuna ekanligini tekshirish"""
    unsubscribed = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(f"@{channel['username']}", user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                unsubscribed.append(channel)
        except Exception as e:
            print(f"Obuna tekshirishda xatolik @{channel['username']}: {e}")
            unsubscribed.append(channel)
    return len(unsubscribed) == 0, unsubscribed


async def require_subscription(message: Message) -> bool:
    """Obuna bo'lmagan bo'lsa xabar yuboradi va False qaytaradi"""
    is_subscribed, _ = await check_user_subscription(message.from_user.id)
    if not is_subscribed:
        await message.answer(SUBSCRIPTION_MESSAGE, reply_markup=get_subscription_keyboard())
        return False
    return True


def get_media_keyboard(file_id: str) -> InlineKeyboardMarkup:
    """Yuklab olish va istoriyaga qo'yish tugmalarini yaratish"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Yuklab olish", callback_data=f"dl:{file_id}"),
            InlineKeyboardButton(text="📱 Istoriyaga", callback_data=f"st:{file_id}"),
        ]
    ])
    return keyboard


@dp.callback_query(F.data == "check_sub")
async def callback_check_subscription(callback: CallbackQuery):
    """Obunani qayta tekshirish"""
    is_subscribed, _ = await check_user_subscription(callback.from_user.id)
    if is_subscribed:
        await callback.message.edit_text(
            "✅ Rahmat! Barcha kanallarga obuna bo'lgansiz.\n\n"
            "👋 Salom! Men universal media yuklovchi botman.\n\n"
            "🔗 Menga quyidagi saytlardan havola yuboring:\n"
            "• YouTube\n"
            "• Instagram\n"
            "• TikTok\n"
            "• Facebook\n"
            "• Twitter/X\n"
            "• va boshqa 1000+ saytlar\n\n"
            "📥 Men rasm, video yoki audio bo'lsa — hammasini yuklab beraman!"
        )
        await callback.answer("✅ Obuna tasdiqlandi!")
    else:
        await callback.answer(
            "❌ Hali barcha kanallarga obuna bo'lmagansiz!",
            show_alert=True,
        )


@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not await require_subscription(message):
        return

    await message.answer(
        "👋 Salom! Men universal media yuklovchi botman.\n\n"
        "🔗 Menga quyidagi saytlardan havola yuboring:\n"
        "• YouTube\n"
        "• Instagram\n"
        "• TikTok\n"
        "• Facebook\n"
        "• Twitter/X\n"
        "• va boshqa 1000+ saytlar\n\n"
        "📥 Men rasm, video yoki audio bo'lsa — hammasini yuklab beraman!"
    )


@dp.message(F.text.regexp(r'https?://\S+'))
async def handle_links(message: Message):
    """Linkdan media yuklab olish va foydalanuvchiga yuborish"""
    if not await require_subscription(message):
        return

    url_match = re.search(r'https?://\S+', message.text)
    if not url_match:
        await message.answer("❌ Havola topilmadi. Iltimos, to'g'ri havola yuboring.")
        return

    url = url_match.group(0)
    status_message = await message.answer("⏳ Havola tekshirilmoqda va yuklab olinmoqda...")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, download_media, url)

        file_path = result['file_path']
        title = result['title']
        media_type = result['media_type']

        if not os.path.exists(file_path):
            await status_message.edit_text("❌ Faylni yuklab bo'lmadi. Boshqa havolani sinab ko'ring.")
            return

        # Fayl hajmini tekshirish (Telegram 50MB gacha)
        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            os.remove(file_path)
            await status_message.edit_text(
                "❌ Fayl hajmi 50MB dan katta. Telegram bu hajmdagi fayllarni qabul qilmaydi."
            )
            return

        await status_message.edit_text("🚀 Fayl Telegramga yuklanmoqda...")

        # Unique ID yaratish (qisqa)
        file_id = uuid.uuid4().hex[:12]

        # Faylni cache'ga saqlash (callback uchun)
        downloaded_files[file_id] = {
            'file_path': file_path,
            'title': title,
            'media_type': media_type,
        }

        # Inline keyboard tugmalar
        keyboard = get_media_keyboard(file_id)
        caption = f"✅ {title}\n\n📥 Tugmalar orqali yuklab oling!"
        file_input = FSInputFile(file_path)

        try:
            if media_type == "photo":
                await message.answer_photo(
                    photo=file_input, caption=caption,
                    reply_markup=keyboard
                )
            elif media_type == "video":
                await message.answer_video(
                    video=file_input, caption=caption,
                    reply_markup=keyboard,
                    supports_streaming=True,
                )
            elif media_type == "audio":
                await message.answer_audio(
                    audio=file_input, caption=caption,
                    reply_markup=keyboard
                )
            else:
                await message.answer_document(
                    document=file_input, caption=caption,
                    reply_markup=keyboard
                )
        except Exception as send_err:
            # Agar media sifatida yuborishda xatolik bo'lsa — hujjat sifatida sinash
            print(f"Media yuborishda xatolik, hujjat sifatida sinash: {send_err}")
            file_input2 = FSInputFile(file_path)
            await message.answer_document(
                document=file_input2, caption=caption,
                reply_markup=keyboard
            )

        await status_message.delete()

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "unsupported url" in error_msg:
            await status_message.edit_text("❌ Bu sayt qo'llab-quvvatlanmaydi.")
        elif "private" in error_msg or "login" in error_msg:
            await status_message.edit_text("🔒 Bu media shaxsiy. Faqat ochiq havolalar yuklanadi.")
        elif "unavailable" in error_msg or "not available" in error_msg:
            await status_message.edit_text("❌ Bu media mavjud emas yoki o'chirilgan.")
        elif "empty media" in error_msg or "cookies" in error_msg:
            await status_message.edit_text(
                "🔒 Bu sayt kirish (login) talab qiladi.\n\n"
                "Iltimos, kompyuterda Chrome/Edge brauzerda Instagram/Facebook'ga kiring, "
                "keyin botni qayta ishga tushiring."
            )
        else:
            await status_message.edit_text("❌ Yuklab olishda xatolik. Havolani tekshirib qayta yuboring.")
        print(f"yt-dlp xatolik: {e}")

    except Exception as e:
        print(f"Umumiy xatolik: {e}")
        try:
            await status_message.edit_text("❌ Kutilmagan xatolik. Havolani tekshirib qayta yuboring.")
        except Exception:
            pass


@dp.callback_query(F.data.startswith("dl:"))
async def callback_download(callback: CallbackQuery):
    """Yuklab olish tugmasi — faylni hujjat sifatida yuborish"""
    file_id = callback.data.split(":", 1)[1]

    if file_id not in downloaded_files:
        await callback.answer("⚠️ Fayl topilmadi. Havolani qayta yuboring.", show_alert=True)
        return

    file_info = downloaded_files[file_id]
    file_path = file_info['file_path']
    title = file_info['title']

    if not os.path.exists(file_path):
        await callback.answer("⚠️ Fayl serverdan o'chirilgan.", show_alert=True)
        del downloaded_files[file_id]
        return

    await callback.answer("📥 Yuklab olinmoqda...")

    try:
        ext = file_path.split('.')[-1]
        safe_title = re.sub(r'[^\w\s-]', '', title)[:50].strip()
        file_input = FSInputFile(file_path, filename=f"{safe_title}.{ext}")
        await callback.message.answer_document(
            document=file_input,
            caption=f"📥 {title}\n\nHujjat sifatida yuklandi ✅"
        )
    except Exception as e:
        print(f"Download callback xatolik: {e}")
        await callback.message.answer("❌ Faylni yuborishda xatolik.")


@dp.callback_query(F.data.startswith("st:"))
async def callback_story(callback: CallbackQuery):
    """Istoriyaga qo'yish — media qayta yuborish"""
    file_id = callback.data.split(":", 1)[1]

    if file_id not in downloaded_files:
        await callback.answer("⚠️ Fayl topilmadi. Havolani qayta yuboring.", show_alert=True)
        return

    file_info = downloaded_files[file_id]
    file_path = file_info['file_path']
    media_type = file_info['media_type']

    if not os.path.exists(file_path):
        await callback.answer("⚠️ Fayl serverdan o'chirilgan.", show_alert=True)
        del downloaded_files[file_id]
        return

    await callback.answer("📱 Istoriya uchun tayyorlanmoqda...")

    try:
        file_input = FSInputFile(file_path)
        story_text = "📱 Shu mediani Telegram istoriyangizga qo'ying!\n\n👆 Bosib → \"Post to Stories\" ni tanlang"

        if media_type == "video":
            await callback.message.answer_video(video=file_input, caption=story_text)
        elif media_type == "photo":
            await callback.message.answer_photo(photo=file_input, caption=story_text)
        elif media_type == "audio":
            await callback.message.answer_audio(audio=file_input, caption=story_text)
        else:
            await callback.message.answer_document(document=file_input, caption=story_text)
    except Exception as e:
        print(f"Story callback xatolik: {e}")
        await callback.message.answer("❌ Yuborishda xatolik.")


@dp.message()
async def handle_other(message: Message):
    """Boshqa xabarlar uchun javob"""
    if not await require_subscription(message):
        return

    if message.text:
        await message.answer(
            "🔗 Iltimos, menga media yuklab olish uchun havola (link) yuboring.\n\n"
            "Masalan: https://www.youtube.com/watch?v=..."
        )


async def main():
    print("✅ Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
