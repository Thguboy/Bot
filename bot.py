import os
import re
import asyncio
import uuid
import subprocess
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import yt_dlp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi. .env faylini yoki muhit o'zgaruvchisini sozlang.")

# PythonAnywhere bepul rejimida Telegram API uchun proxy kerak
BOT_PROXY = os.getenv("BOT_PROXY")
if not BOT_PROXY and os.getenv("PYTHONANYWHERE_DOMAIN"):
    BOT_PROXY = "http://proxy.server:3128"

if BOT_PROXY:
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=BOT_PROXY))
else:
    bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

downloaded_files = {}

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.3gp'}
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.ogg', '.wav', '.flac', '.aac', '.opus'}

START_TEXT = (
    "👋 <b>Assalomu alaykum!</b>\n\n"
    "Men <b>universal media yuklovchi</b> botman.\n\n"
    "📌 <b>Nima qila olaman?</b>\n"
    "• Havola yuborsangiz — <b>video, rasm yoki audio</b> yuklab beraman\n"
    "• <code>/round</code> — videoni <b>dumaloq video message</b> qilib beraman\n\n"
    "🌐 <b>Qo'llab-quvvatlanadigan saytlar:</b>\n"
    "YouTube • Instagram • TikTok • Facebook • Twitter/X va 1000+ sayt\n\n"
    "📝 <b>Qanday ishlatish:</b>\n"
    "1️⃣ Media havolasini yuboring\n"
    "2️⃣ Bot yuklab, sizga yuboradi\n\n"
    "💡 <i>Masalan:</i> https://youtube.com/watch?v=..."
)

LOADING_FRAMES = [
    "⏳ <b>Yuklanmoqda</b>",
    "🔄 <b>Yuklanmoqda.</b>",
    "📥 <b>Yuklanmoqda..</b>",
    "🚀 <b>Yuklanmoqda...</b>",
]

COOKIE_SITES = ('instagram.com', 'facebook.com', 'fb.watch', 'threads.net')
VIDEO_SITES = (
    'youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com',
    'facebook.com', 'fb.watch', 'twitter.com', 'x.com', 'vk.com', 'threads.net',
)


class RoundStates(StatesGroup):
    waiting_video = State()


def get_media_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "photo"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "document"


def needs_cookies(url: str) -> bool:
    url_lower = url.lower()
    return any(site in url_lower for site in COOKIE_SITES)


def is_video_site(url: str) -> bool:
    url_lower = url.lower()
    return any(site in url_lower for site in VIDEO_SITES)


def has_ffmpeg() -> bool:
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except Exception:
        return False


def get_cookies_browser():
    from yt_dlp.cookies import extract_cookies_from_browser

    for browser in ['edge', 'firefox', 'brave', 'opera', 'chromium', 'chrome']:
        try:
            extract_cookies_from_browser(browser, None, None)
            return browser
        except Exception:
            continue
    return None


def _get_downloaded_filepath(info) -> str | None:
    requested = info.get('requested_downloads') or []
    for item in requested:
        filepath = item.get('filepath')
        if filepath and os.path.exists(filepath):
            return filepath

    for key in ('filepath', '_filename'):
        filepath = info.get(key)
        if filepath and os.path.exists(filepath):
            return filepath
    return None


def _resolve_downloaded_file(ydl, info) -> str:
    filename = ydl.prepare_filename(info)
    if os.path.exists(filename):
        return filename

    base, _ = os.path.splitext(filename)
    for ext in ['.mp4', '.mkv', '.webm', '.mov', '.m4a', '.mp3', '.opus', '.jpg', '.jpeg', '.png', '.webp', '.gif']:
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
    if info and info.get('_type') == 'playlist':
        entries = [e for e in info.get('entries', []) if e is not None]
        if entries:
            return entries[0]
    return info


def _base_ytdlp_opts(url: str, use_cookies: bool = False, cookies_browser: str = None) -> dict:
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
        'no_warnings': True,
        'quiet': True,
        'restrictfilenames': True,
        'noplaylist': True,
        'extractor_retries': 5,
        'fragment_retries': 10,
        'retries': 10,
        'socket_timeout': 60,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            ),
        },
    }

    url_lower = url.lower()
    extractor_args = {}
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        extractor_args['youtube'] = {'player_client': ['android', 'web']}
    if 'tiktok.com' in url_lower:
        extractor_args['tiktok'] = {'api_hostname': 'api22-normal-c-useast1a.tiktokv.com'}
    if extractor_args:
        opts['extractor_args'] = extractor_args

    if use_cookies and cookies_browser:
        opts['cookiesfrombrowser'] = (cookies_browser,)
    return opts


def _build_ytdlp_opts(
    url: str,
    mode: str = 'video_single',
    use_cookies: bool = False,
    cookies_browser: str = None,
) -> dict:
    opts = _base_ytdlp_opts(url, use_cookies, cookies_browser)

    if mode == 'video_single':
        opts['format'] = (
            'b[ext=mp4][height<=1080][filesize<=50M]/'
            'b[ext=mp4][height<=720][filesize<=50M]/'
            'b[ext=mp4]/'
            '22/18/'
            'b[height<=720]/'
            'b'
        )
    elif mode == 'video_merge':
        opts['format'] = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            'bestvideo+bestaudio/'
            'best'
        )
        opts['merge_output_format'] = 'mp4'
    elif mode == 'video_fallback':
        opts['format'] = 'best[ext=mp4]/best[height<=720]/best'
    elif mode == 'audio':
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif mode == 'image':
        opts['format'] = 'best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best[ext=webp]/best'
    else:
        opts['format'] = 'best'

    return opts


def _download_with_opts(url: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        info = _extract_single_entry(info)

        if info is None:
            raise Exception("Media ma'lumotlari topilmadi")

        filename = _get_downloaded_filepath(info) or _resolve_downloaded_file(ydl, info)
        if not os.path.exists(filename):
            raise yt_dlp.utils.DownloadError(f"Fayl yuklanmadi: {filename}")

        title = info.get('title') or (info.get('description') or 'Media')[:100]
        return {
            'file_path': filename,
            'title': title,
            'media_type': get_media_type(filename),
        }


def download_media(url: str) -> dict:
    attempts = []
    cookies_browser = get_cookies_browser() if needs_cookies(url) else None
    cookie_variants = [True, False] if cookies_browser and needs_cookies(url) else [False]

    video_modes = ['video_single']
    if has_ffmpeg():
        video_modes.append('video_merge')
    video_modes.append('video_fallback')

    for use_cookies in cookie_variants:
        browser = cookies_browser if use_cookies else None
        for mode in video_modes:
            attempts.append(_build_ytdlp_opts(url, mode, use_cookies, browser))

    if not is_video_site(url):
        for use_cookies in cookie_variants:
            browser = cookies_browser if use_cookies else None
            attempts.append(_build_ytdlp_opts(url, 'audio', use_cookies, browser))
            attempts.append(_build_ytdlp_opts(url, 'image', use_cookies, browser))

    last_error = None
    seen = set()
    for opts in attempts:
        key = (opts.get('format'), opts.get('cookiesfrombrowser'), str(opts.get('extractor_args')))
        if key in seen:
            continue
        seen.add(key)
        try:
            return _download_with_opts(url, opts)
        except Exception as e:
            last_error = e
            print(f"Yuklab olish urinishi muvaffaqiyatsiz: {e}")

    if last_error:
        raise last_error
    raise Exception("Media yuklab bo'lmadi")


def ensure_telegram_video(file_path: str) -> str:
    """Telegram uchun mp4/h264 formatga o'tkazish"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.mp4' or not has_ffmpeg():
        return file_path

    output_path = f"{os.path.splitext(file_path)[0]}_tg.mp4"
    subprocess.run([
        'ffmpeg', '-y', '-i', file_path,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-movflags', '+faststart',
        output_path,
    ], check=True, capture_output=True)
    return output_path


def convert_to_video_note(input_path: str, output_path: str) -> None:
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-t', '60',
        '-vf', 'scale=640:640:force_original_aspect_ratio=increase,crop=640:640',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


async def run_loading_animation(chat_id: int, message_id: int, stop_event: asyncio.Event):
    i = 0
    while not stop_event.is_set():
        try:
            await bot.edit_message_text(
                LOADING_FRAMES[i % len(LOADING_FRAMES)],
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='HTML',
            )
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.9)
            break
        except asyncio.TimeoutError:
            continue


async def run_chat_action(chat_id: int, stop_event: asyncio.Event):
    actions = [ChatAction.UPLOAD_VIDEO, ChatAction.UPLOAD_PHOTO, ChatAction.UPLOAD_DOCUMENT]
    i = 0
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id, actions[i % len(actions)])
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4)
            break
        except asyncio.TimeoutError:
            continue


def format_download_error(error: Exception) -> str:
    msg = str(error).lower()
    if any(x in msg for x in ('429', 'rate limit', 'too many requests', 'quota')):
        return "⚠️ API limiti tugagan. Biroz kutib, qayta urinib ko'ring."
    if any(x in msg for x in ('403', 'forbidden', 'blocked')):
        return "🔒 Sayt kirishni cheklagan. Boshqa havola yuboring."
    if 'unsupported url' in msg:
        return "❌ Bu sayt qo'llab-quvvatlanmaydi."
    if 'private' in msg or 'login' in msg or 'sign in' in msg:
        return "🔒 Bu media shaxsiy yoki login talab qiladi."
    if any(x in msg for x in ('unavailable', 'not available', 'removed', 'deleted')):
        return "❌ Media topilmadi yoki o'chirilgan."
    if 'empty media' in msg or 'cookies' in msg:
        return (
            "🔒 Bu sayt cookie/login talab qiladi.\n\n"
            "Instagram/Facebook uchun brauzerda tizimga kiring va qayta urinib ko'ring."
        )
    if any(x in msg for x in ('unable to extract', 'no video', 'no formats', 'requested format')):
        return "❌ Media formati topilmadi. Boshqa havola yuboring."
    if 'timed out' in msg or 'timeout' in msg:
        return "⏱️ Vaqt tugadi. Internet sekin — qayta urinib ko'ring."
    return "❌ Yuklab olishda xatolik. Havolani tekshirib qayta yuboring."


def get_media_keyboard(file_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Yuklab olish", callback_data=f"dl:{file_id}"),
            InlineKeyboardButton(text="📱 Istoriyaga", callback_data=f"st:{file_id}"),
        ]
    ])


async def send_media_to_user(message: Message, file_path: str, title: str, media_type: str, keyboard):
    caption = f"✅ <b>{title}</b>\n\n📥 Tugmalar orqali yuklab oling yoki saqlang!"
    send_path = file_path
    converted_path = None

    if media_type == "video":
        try:
            send_path = ensure_telegram_video(file_path)
            if send_path != file_path:
                converted_path = send_path
        except Exception as conv_err:
            print(f"Video konvertatsiya xatolik: {conv_err}")

    file_input = FSInputFile(send_path)
    try:
        if media_type == "photo":
            await message.answer_photo(photo=file_input, caption=caption, reply_markup=keyboard, parse_mode='HTML')
        elif media_type == "video":
            await message.answer_video(
                video=file_input, caption=caption, reply_markup=keyboard,
                supports_streaming=True, parse_mode='HTML',
            )
        elif media_type == "audio":
            await message.answer_audio(audio=file_input, caption=caption, reply_markup=keyboard, parse_mode='HTML')
        else:
            await message.answer_document(document=file_input, caption=caption, reply_markup=keyboard, parse_mode='HTML')
    except Exception as send_err:
        print(f"Media yuborishda xatolik, hujjat sifatida sinash: {send_err}")
        await message.answer_document(
            document=FSInputFile(send_path), caption=caption, reply_markup=keyboard, parse_mode='HTML',
        )
    finally:
        if converted_path and os.path.exists(converted_path):
            os.remove(converted_path)


async def process_round_video(message: Message, tg_file_id: str):
    status = await message.answer("⏳ <b>Dumaloq video tayyorlanmoqda...</b>", parse_mode='HTML')
    input_path = os.path.join(DOWNLOAD_DIR, f"round_in_{uuid.uuid4().hex[:8]}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"round_out_{uuid.uuid4().hex[:8]}.mp4")

    try:
        tg_file = await bot.get_file(tg_file_id)
        await bot.download_file(tg_file.file_path, input_path)
        convert_to_video_note(input_path, output_path)

        if not os.path.exists(output_path):
            await status.edit_text("❌ Dumaloq video yaratib bo'lmadi.")
            return

        if os.path.getsize(output_path) > 50 * 1024 * 1024:
            await status.edit_text("❌ Video juda katta. 60 soniyagacha qisqa video yuboring.")
            return

        await message.answer_video_note(FSInputFile(output_path))
        await status.edit_text("✅ <b>Dumaloq video tayyor!</b>", parse_mode='HTML')
    except FileNotFoundError:
        await status.edit_text("❌ FFmpeg topilmadi. Serverda ffmpeg o'rnatilgan bo'lishi kerak.")
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg xatolik: {e.stderr.decode(errors='ignore') if e.stderr else e}")
        await status.edit_text("❌ Videoni dumaloq qilishda xatolik. Boshqa video sinab ko'ring.")
    except Exception as e:
        print(f"Round video xatolik: {e}")
        await status.edit_text("❌ Dumaloq video yaratishda xatolik.")
    finally:
        for path in (input_path, output_path):
            if os.path.exists(path):
                os.remove(path)


@dp.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer(START_TEXT, parse_mode='HTML')


@dp.message(Command("round", "raund"))
async def round_cmd(message: Message, state: FSMContext):
    reply = message.reply_to_message
    if reply and reply.video:
        await process_round_video(message, reply.video.file_id)
        return
    if reply and reply.document and reply.document.mime_type and reply.document.mime_type.startswith('video/'):
        await process_round_video(message, reply.document.file_id)
        return

    await state.set_state(RoundStates.waiting_video)
    await message.answer(
        "⭕ <b>Dumaloq video yaratish</b>\n\n"
        "📹 Video yuboring yoki videoga javob qilib <code>/round</code> yozing.\n\n"
        "💡 Video 60 soniyagacha bo'lishi kerak.",
        parse_mode='HTML',
    )


@dp.message(RoundStates.waiting_video, F.video | F.document)
async def round_receive_video(message: Message, state: FSMContext):
    await state.clear()
    if message.video:
        await process_round_video(message, message.video.file_id)
    elif message.document and message.document.mime_type and message.document.mime_type.startswith('video/'):
        await process_round_video(message, message.document.file_id)
    else:
        await message.answer("❌ Iltimos, video fayl yuboring.")


@dp.message(F.text.regexp(r'https?://\S+'))
async def handle_links(message: Message):
    url_match = re.search(r'https?://\S+', message.text)
    if not url_match:
        await message.answer("❌ Havola topilmadi. Iltimos, to'g'ri havola yuboring.")
        return

    url = url_match.group(0).rstrip('.,)')
    status_message = await message.answer(LOADING_FRAMES[0], parse_mode='HTML')

    stop_event = asyncio.Event()
    anim_task = asyncio.create_task(run_loading_animation(message.chat.id, status_message.message_id, stop_event))
    action_task = asyncio.create_task(run_chat_action(message.chat.id, stop_event))

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, download_media, url)

        file_path = result['file_path']
        title = result['title']
        media_type = result['media_type']

        if not os.path.exists(file_path):
            await status_message.edit_text("❌ Faylni yuklab bo'lmadi. Boshqa havolani sinab ko'ring.")
            return

        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            os.remove(file_path)
            await status_message.edit_text("❌ Fayl 50MB dan katta. Telegram qabul qilmaydi.")
            return

        await status_message.edit_text("🚀 <b>Telegramga yuborilmoqda...</b>", parse_mode='HTML')

        file_id = uuid.uuid4().hex[:12]
        downloaded_files[file_id] = {
            'file_path': file_path,
            'title': title,
            'media_type': media_type,
        }

        await send_media_to_user(message, file_path, title, media_type, get_media_keyboard(file_id))
        await status_message.delete()

    except yt_dlp.utils.DownloadError as e:
        await status_message.edit_text(format_download_error(e))
        print(f"yt-dlp xatolik: {e}")
    except Exception as e:
        print(f"Umumiy xatolik: {e}")
        try:
            await status_message.edit_text(format_download_error(e))
        except Exception:
            pass
    finally:
        stop_event.set()
        anim_task.cancel()
        action_task.cancel()


@dp.callback_query(F.data.startswith("dl:"))
async def callback_download(callback: CallbackQuery):
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
        safe_title = re.sub(r'[^\w\s-]', '', title)[:50].strip() or 'media'
        await callback.message.answer_document(
            document=FSInputFile(file_path, filename=f"{safe_title}.{ext}"),
            caption=f"📥 {title}\n\nHujjat sifatida yuklandi ✅",
        )
    except Exception as e:
        print(f"Download callback xatolik: {e}")
        await callback.message.answer("❌ Faylni yuborishda xatolik.")


@dp.callback_query(F.data.startswith("st:"))
async def callback_story(callback: CallbackQuery):
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
        story_text = '📱 Shu mediani Telegram istoriyangizga qo\'ying!\n\n👆 Bosib → "Post to Stories" ni tanlang'
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
    if message.text:
        await message.answer(
            "🔗 Media yuklash uchun havola yuboring.\n\n"
            "⭕ Dumaloq video uchun: <code>/round</code>\n\n"
            "💡 Masalan: https://youtube.com/watch?v=...",
            parse_mode='HTML',
        )


async def main():
    print("✅ Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
