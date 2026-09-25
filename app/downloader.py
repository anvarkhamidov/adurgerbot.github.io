"""yt-dlp wrapper: best video + best audio merged by FFmpeg into MP4.

Runs synchronously (call it from a worker thread). Partial downloads are kept
in the job directory, so a retry - in this call or a later request for the same
URL - resumes from the existing .part files instead of starting over.
"""

import copy
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadError as YtDlpDownloadError

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

# Prefer the highest resolution/fps; at equal quality prefer codecs that play
# everywhere in Telegram (H.264 + AAC).
FORMAT_SORT = ["res", "fps", "vcodec:h264", "acodec:aac"]
BEST_FORMAT = "bv*+ba/b"
FALLBACK_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)

# Errors that will not go away by retrying.
PERMANENT_ERRORS = (
    "unsupported url",
    "private video",
    "video unavailable",
    "is not available",
    "has been removed",
    "copyright",
    "sign in to confirm your age",
    "members-only",
    "requested format is not available",
    "no video formats found",
    "http error 404",
    "http error 403: forbidden",
)

# The site refuses anonymous access from this server's IP - cookies help.
AUTH_ERRORS = ("sign in to confirm", "logged-in", "login required", "use --cookies")


class DownloadFailed(Exception):
    pass


class TooLarge(DownloadFailed):
    pass


class NeedsCookies(DownloadFailed):
    pass


@dataclass
class Result:
    path: Path
    title: str
    duration: int | None
    width: int | None
    height: int | None
    has_audio: bool


def _base_opts(job_dir: Path, cookies: Path | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": str(job_dir / "%(id).60s.%(ext)s"),
        "paths": {"home": str(job_dir), "temp": str(job_dir)},
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format_sort": FORMAT_SORT,
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        # yt-dlp's merger runs "-c copy"; these output args re-encode only the
        # audio to AAC (Opus in MP4 is silent in some Telegram clients) and move
        # the index to the front so the video streams before fully loading.
        "postprocessor_args": {
            "merger+ffmpeg_o": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
            "videoremuxer+ffmpeg_o": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
        },
        # Resilience: resume .part files, retry HTTP/fragments with backoff.
        "continuedl": True,
        "retries": 15,
        "fragment_retries": 15,
        "file_access_retries": 5,
        "extractor_retries": 3,
        "retry_sleep_functions": {
            "http": lambda n: min(30, 2**n),
            "fragment": lambda n: min(30, 2**n),
            "file_access": lambda n: min(10, n + 1),
            "extractor": lambda n: min(30, 2**n),
        },
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "http_chunk_size": 10 * 1024 * 1024,
        "overwrites": False,
    }
    if cookies:
        opts["cookiefile"] = str(cookies)
    return opts


def _estimated_size(info: dict[str, Any]) -> int | None:
    formats = info.get("requested_formats") or [info]
    total = 0
    for fmt in formats:
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not size and fmt.get("tbr") and info.get("duration"):
            size = fmt["tbr"] * 1000 / 8 * info["duration"]  # tbr is in kbit/s
        if not size:
            return None
        total += size
    return int(total)


def _pick_format(
    raw: dict[str, Any], opts: dict[str, Any], limit: int, below_height: int | None
) -> str:
    """Return the best format selector whose estimated size fits the limit.

    below_height excludes qualities already found to be too large.
    """
    heights = [h for h in FALLBACK_HEIGHTS if below_height is None or h < below_height]
    candidates = ([BEST_FORMAT] if below_height is None else []) + [
        f"bv*[height<={h}]+ba/b[height<={h}]" for h in heights
    ]
    for selector in candidates:
        with yt_dlp.YoutubeDL({**opts, "format": selector, "simulate": True}) as ydl:
            try:
                info = ydl.process_ie_result(copy.deepcopy(raw), download=False)
            except YtDlpDownloadError as e:
                if "requested format is not available" in str(e).lower():
                    continue
                raise
        if info.get("entries"):
            info = next(e for e in info["entries"] if e)
        size = _estimated_size(info)
        if size is None or size <= limit:
            return selector
    raise TooLarge("even the lowest quality exceeds the Telegram upload limit")


def _has_audio(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return any(s.get("codec_type") == "audio" for s in json.loads(out)["streams"])
    except Exception:
        log.exception("ffprobe failed for %s", path)
        return True


def _is_permanent(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in PERMANENT_ERRORS + AUTH_ERRORS)


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def download(
    url: str,
    job_dir: Path,
    *,
    max_bytes: int,
    attempts: int,
    cookies: Path | None,
    progress: ProgressCallback,
) -> Result:
    job_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(job_dir, cookies)

    def on_progress(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading":
            return
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        kind = "видео" if (d.get("info_dict") or {}).get("vcodec") not in (None, "none") else "аудио"
        text = f"⬇️ Скачиваю {kind}: {_fmt_bytes(done)}"
        if total:
            text += f" / {_fmt_bytes(total)} ({done * 100 / total:.0f}%)"
        if d.get("speed"):
            text += f", {_fmt_bytes(d['speed'])}/s"
        progress(text)

    def on_postprocess(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            progress("🎞 Объединяю видео и аудио в MP4…")

    opts["progress_hooks"] = [on_progress]
    opts["postprocessor_hooks"] = [on_postprocess]

    last_error: Exception | None = None
    below_height: int | None = None
    attempt = 0
    while attempt < attempts:
        try:
            if attempt == 0:
                progress("🔎 Получаю информацию о видео…")
            # Extract once, then reuse the result for format selection and download.
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=False, process=False)
            opts["format"] = _pick_format(raw, opts, max_bytes, below_height)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.process_ie_result(copy.deepcopy(raw), download=True)
            if info.get("entries"):
                info = next(e for e in info["entries"] if e)

            downloads = info.get("requested_downloads") or []
            path = Path(downloads[-1]["filepath"]) if downloads else None
            if not path or not path.is_file():
                raise DownloadFailed("yt-dlp finished without producing a file")

            size = path.stat().st_size
            if size > max_bytes:
                # The site gave no usable size estimate: step down in quality.
                height = info.get("height")
                path.unlink(missing_ok=True)
                if not height or height <= FALLBACK_HEIGHTS[-1]:
                    raise TooLarge(f"file is {_fmt_bytes(size)}")
                below_height = height
                progress(f"📉 {height}p весит {_fmt_bytes(size)} — больше лимита, беру качество ниже…")
                continue

            return Result(
                path=path,
                title=info.get("title") or path.stem,
                duration=int(info["duration"]) if info.get("duration") else None,
                width=info.get("width"),
                height=info.get("height"),
                has_audio=_has_audio(path),
            )
        except TooLarge:
            raise
        except Exception as e:  # network errors, extractor hiccups, ffmpeg...
            attempt += 1
            last_error = e
            if _is_permanent(e) or attempt >= attempts:
                break
            delay = min(60, 5 * 2 ** (attempt - 1))
            log.warning("attempt %d/%d for %s failed: %s; retry in %ds",
                        attempt, attempts, url, e, delay)
            progress(f"⚠️ Ошибка, повтор {attempt + 1}/{attempts} через {delay} с…")
            time.sleep(delay)

    if any(marker in str(last_error).lower() for marker in AUTH_ERRORS):
        raise NeedsCookies(str(last_error)) from last_error
    raise DownloadFailed(str(last_error)) from last_error
