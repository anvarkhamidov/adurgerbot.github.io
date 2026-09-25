"""ffprobe/ffmpeg helpers that make Telegram show the video with its real proportions.

Telegram lays out the player from the width/height sent with the video and
from the thumbnail; many clients ignore the rotation flag and non-square
pixels (SAR) stored in the file. So dimensions are taken from the file itself,
and files relying on rotation or SAR are re-encoded to plain square pixels.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

log = logging.getLogger(__name__)

THUMB_SIDE = 320  # Telegram limit for thumbnails: <= 320 px, <= 200 KB JPEG


@dataclass
class VideoInfo:
    width: int | None  # as displayed: rotation and pixel aspect applied
    height: int | None
    duration: int | None
    has_audio: bool
    rotation: int  # degrees stored in the file's display matrix
    sar: Fraction  # sample (pixel) aspect ratio

    @property
    def needs_normalize(self) -> bool:
        return self.rotation % 360 != 0 or self.sar != 1


def _parse_sar(raw: str | None) -> Fraction:
    try:
        num, den = (int(x) for x in (raw or "").split(":"))
        return Fraction(num, den) if num > 0 and den > 0 else Fraction(1)
    except ValueError:
        return Fraction(1)


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(float(side["rotation"]))
    return int(float((stream.get("tags") or {}).get("rotate", 0)))


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def probe(path: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams") or []
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float((data.get("format") or {}).get("duration") or 0) or None

    if not video or not video.get("width") or not video.get("height"):
        return VideoInfo(None, None, round(duration) if duration else None, has_audio, 0, Fraction(1))

    sar = _parse_sar(video.get("sample_aspect_ratio"))
    rotation = _rotation(video)
    width, height = _even(video["width"] * sar), int(video["height"])
    if rotation % 180 != 0:
        width, height = height, width
    return VideoInfo(width, height, round(duration) if duration else None, has_audio, rotation, sar)


def normalize(path: Path) -> None:
    """Re-encode in place to square pixels with the rotation applied to the frames."""
    tmp = path.with_name(path.stem + ".normalized.mp4")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(path),  # autorotate (on by default) turns the frames upright
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(tmp),
        ],
        check=True, capture_output=True, text=True,
    )
    tmp.replace(path)


def thumbnail(path: Path, info: VideoInfo) -> Path | None:
    """JPEG preview with the video's proportions, so the chat bubble is not distorted."""
    thumb = path.with_suffix(".jpg")
    at = min(1.0, (info.duration or 0) / 2)
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", f"scale={THUMB_SIDE}:{THUMB_SIDE}:force_original_aspect_ratio=decrease,setsar=1",
                "-q:v", "5", str(thumb),
            ],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        log.exception("thumbnail failed for %s", path)
        return None
    return thumb if thumb.is_file() and thumb.stat().st_size <= 200 * 1024 else None
