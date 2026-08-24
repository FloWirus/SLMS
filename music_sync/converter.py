import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile

from .db import Track

LOSSLESS_FORMATS = {"flac", "wav"}


@dataclass(frozen=True)
class ConversionSpec:
    key: str
    codec: str  # "flac" or "mp3"
    sample_rate: int
    bit_depth: int | None  # None for mp3
    extension: str
    bitrate: str | None = None  # e.g. "320k", only for mp3


CONVERSION_TARGETS: dict[str, ConversionSpec] = {
    "flac16_44": ConversionSpec("flac16_44", "flac", 44100, 16, "flac"),
    "flac24_44": ConversionSpec("flac24_44", "flac", 44100, 24, "flac"),
    "flac24_48": ConversionSpec("flac24_48", "flac", 48000, 24, "flac"),
    "flac24_96": ConversionSpec("flac24_96", "flac", 96000, 24, "flac"),
    "mp3_320": ConversionSpec("mp3_320", "mp3", 48000, None, "mp3", bitrate="320k"),
}

CONVERSION_TARGET_ORDER = ["flac16_44", "flac24_44", "flac24_48", "flac24_96", "mp3_320"]


@dataclass(frozen=True)
class ConversionSettings:
    target_key: str
    use_libsoxr: bool


@dataclass(frozen=True)
class CoverResizeSettings:
    max_size: int
    dpi: int


class ConversionFailed(subprocess.CalledProcessError):
    """A failed ffmpeg run, carrying ffmpeg's own diagnosis.

    Subclasses CalledProcessError so every existing `except
    subprocess.CalledProcessError` still catches it -- the difference is
    str(): the base class reports only "returned non-zero exit status 1",
    which is what the sync result dialog used to show for every failed
    conversion, with the one line that says *why* thrown away."""

    def __str__(self) -> str:
        detail = [line for line in (self.stderr or "").strip().splitlines() if line.strip()]
        # ffmpeg puts the actual reason last, after the banner and the stream
        # dump; a couple of lines is enough context without pasting a screen
        # of build flags into a message box.
        return f"{super().__str__()}: {' / '.join(detail[-2:])}" if detail else super().__str__()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def libsoxr_available() -> bool:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return "enable-libsoxr" in result.stdout.lower()


def probe_lossless_info(path: Path) -> tuple[int, int]:
    """Returns (sample_rate, bits_per_sample) for a FLAC/WAV file. Defaults to 44100/16 if unknown."""
    try:
        audio = MutagenFile(str(path))
    except Exception:
        return 44100, 16
    if audio is None or audio.info is None:
        return 44100, 16
    sample_rate = getattr(audio.info, "sample_rate", 44100) or 44100
    bits_per_sample = getattr(audio.info, "bits_per_sample", 16) or 16
    return sample_rate, bits_per_sample


def decide_conversion(track: Track, source_path: Path, target_key: str) -> ConversionSpec | None:
    """Decides whether a track should be transcoded before being copied to a device.

    Returns None when the file should be copied unchanged: lossy sources are
    never re-encoded, and lossless sources already at or below the target's
    quality are left untouched (never upsampled).
    """
    if track.format not in LOSSLESS_FORMATS:
        return None

    spec = CONVERSION_TARGETS[target_key]
    if spec.codec == "mp3":
        return spec

    source_rate, source_bits = probe_lossless_info(source_path)
    if source_rate <= spec.sample_rate and source_bits <= spec.bit_depth:
        return None
    return spec


def convert_file(source_path: Path, target_path: Path, spec: ConversionSpec, use_libsoxr: bool) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(source_path), "-map", "0", "-map_metadata", "0", "-c:v", "copy"]

    if spec.codec == "flac":
        if use_libsoxr:
            cmd += ["-af", f"aresample=resampler=soxr:out_sample_rate={spec.sample_rate}"]
        else:
            cmd += ["-ar", str(spec.sample_rate)]
        if spec.bit_depth == 24:
            cmd += ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
        else:
            cmd += ["-sample_fmt", "s16"]
        cmd += ["-c:a", "flac"]
    else:  # mp3
        source_rate, _ = probe_lossless_info(source_path)
        if source_rate > spec.sample_rate:
            if use_libsoxr:
                cmd += ["-af", f"aresample=resampler=soxr:out_sample_rate={spec.sample_rate}"]
            else:
                cmd += ["-ar", str(spec.sample_rate)]
        cmd += ["-c:a", "libmp3lame", "-b:a", spec.bitrate, "-write_xing", "0", "-id3v2_version", "3"]

    cmd.append(str(target_path))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ConversionFailed(result.returncode, cmd, result.stdout, result.stderr)
