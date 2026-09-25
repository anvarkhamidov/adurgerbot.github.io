import asyncio
import hashlib
import logging
import re
import shutil
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message

from config import Config
from downloader import DownloadFailed, NeedsCookies, Result, TooLarge, download

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PROGRESS_INTERVAL = 4.0  # seconds between status message edits
SEND_ATTEMPTS = 4
UPLOAD_TIMEOUT = 3600

cfg = Config.from_env()
router = Router()
semaphore = asyncio.Semaphore(cfg.max_concurrent)
url_locks: dict[str, asyncio.Lock] = {}  # one download per URL at a time


def extract_url(message: Message) -> str | None:
    text = message.text or message.caption or ""
    for entity in message.entities or message.caption_entities or []:
        if entity.type == "text_link" and entity.url:
            return entity.url
        if entity.type == "url":
            return entity.extract_from(text)
    match = URL_RE.search(text)
    return match.group(0) if match else None


def job_dir_for(url: str) -> Path:
    # Stable per-URL directory: a repeated request resumes the partial download.
    return cfg.download_dir / hashlib.sha256(url.encode()).hexdigest()[:16]


class StatusMessage:
    """Throttled edits of a single status message, callable from any thread."""

    def __init__(self, message: Message, loop: asyncio.AbstractEventLoop):
        self.message = message
        self.loop = loop
        self.last_text = ""
        self.last_edit = 0.0

    async def set(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        if text == self.last_text or (not force and now - self.last_edit < PROGRESS_INTERVAL):
            return
        self.last_text, self.last_edit = text, now
        try:
            await self.message.edit_text(text)
        except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            pass

    def from_thread(self, text: str) -> None:
        asyncio.run_coroutine_threadsafe(self.set(text), self.loop)

    async def delete(self) -> None:
        try:
            await self.message.delete()
        except TelegramBadRequest:
            pass


async def send_result(message: Message, result: Result, status: StatusMessage) -> None:
    caption = result.title[:1000]
    if not result.has_audio:
        caption += "\n\n(у источника нет звуковой дорожки)"

    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            await status.set(f"📤 Отправляю в Telegram… (попытка {attempt})", force=True)
            await message.answer_video(
                FSInputFile(result.path, filename=f"{result.path.stem}.mp4"),
                caption=caption,
                duration=result.duration,
                width=result.width,
                height=result.height,
                thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
                supports_streaming=True,
                request_timeout=UPLOAD_TIMEOUT,
            )
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            # Telegram sometimes refuses a file as video; fall back to a document.
            log.warning("send_video rejected (%s), sending as document", e)
            await message.answer_document(
                FSInputFile(result.path, filename=f"{result.path.stem}.mp4"),
                caption=caption,
                thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
                request_timeout=UPLOAD_TIMEOUT,
            )
            return
        except TelegramNetworkError as e:
            if attempt == SEND_ATTEMPTS:
                raise
            log.warning("upload attempt %d failed: %s", attempt, e)
            await asyncio.sleep(5 * attempt)


async def process(message: Message, url: str) -> None:
    status = StatusMessage(await message.reply("⏳ В очереди…"), asyncio.get_running_loop())
    lock = url_locks.setdefault(url, asyncio.Lock())
    job_dir = job_dir_for(url)

    async with semaphore, lock:
        try:
            result = await asyncio.to_thread(
                download,
                url,
                job_dir,
                max_bytes=cfg.max_file_bytes,
                attempts=cfg.download_attempts,
                cookies=cfg.cookies_file,
                pot_provider_url=cfg.pot_provider_url,
                progress=status.from_thread,
            )
            await send_result(message, result, status)
        except TooLarge as e:
            shutil.rmtree(job_dir, ignore_errors=True)
            limit_mb = cfg.max_file_bytes // (1024 * 1024)
            await status.set(f"❌ Видео больше лимита Telegram ({limit_mb} МБ): {e}", force=True)
        except NeedsCookies:
            shutil.rmtree(job_dir, ignore_errors=True)
            await status.set(
                "🔒 Сайт требует вход или считает сервер ботом. "
                "Положите cookies.txt с этого сайта в /data/cookies.txt (см. README).",
                force=True,
            )
        except DownloadFailed as e:
            # Partial files are kept so the next request for this URL resumes.
            await status.set(f"❌ Не удалось скачать: {str(e)[:500]}", force=True)
        except Exception as e:
            log.exception("failed to process %s", url)
            await status.set(f"❌ Ошибка: {str(e)[:500]}", force=True)
        else:
            shutil.rmtree(job_dir, ignore_errors=True)
            await status.delete()


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(
        "Пришлите ссылку на видео — я скачаю его в лучшем качестве "
        "и отправлю сюда как MP4 со звуком."
    )


@router.message(F.text | F.caption)
async def on_message(message: Message) -> None:
    if cfg.allowed_users and (not message.from_user or message.from_user.id not in cfg.allowed_users):
        await message.reply("⛔ Доступ запрещён.")
        return
    url = extract_url(message)
    if not url:
        await message.reply("Пришлите ссылку на видео (http/https).")
        return
    # Updates are handled as separate tasks, so a long download does not block others.
    await process(message, url)


async def cleanup_stale() -> None:
    """Remove leftovers of failed/abandoned downloads older than STALE_HOURS."""
    while True:
        cutoff = time.time() - cfg.stale_hours * 3600
        for path in cfg.download_dir.glob("*"):
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
                    log.info("removed stale %s", path)
            except FileNotFoundError:
                pass
        await asyncio.sleep(3600)


async def main() -> None:
    cfg.download_dir.mkdir(parents=True, exist_ok=True)

    session = None
    if cfg.bot_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.bot_api_url, is_local=True))
    bot = Bot(cfg.bot_token, session=session)

    # The local Bot API server may still be starting up.
    for attempt in range(30):
        try:
            me = await bot.get_me()
            break
        except TelegramNetworkError as e:
            log.warning("Bot API not reachable yet (%s), retrying…", e)
            await asyncio.sleep(min(30, 2 + attempt))
    else:
        raise SystemExit("Bot API is unreachable")

    log.info("started as @%s, api=%s, upload limit=%d MB, allowed users=%s",
             me.username, cfg.bot_api_url or "api.telegram.org",
             cfg.max_file_bytes // (1024 * 1024), sorted(cfg.allowed_users) or "all")

    dp = Dispatcher()
    dp.include_router(router)
    cleaner = asyncio.create_task(cleanup_stale())
    try:
        await dp.start_polling(bot, handle_as_tasks=True)
    finally:
        cleaner.cancel()


if __name__ == "__main__":
    asyncio.run(main())
