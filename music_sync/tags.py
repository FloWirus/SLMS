from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from mutagen.easymp4 import EasyMP4, EasyMP4Tags
from mutagen.wave import WAVE

COMMON_FIELDS = ("artist", "album", "title", "track_number", "track_total", "disc_number", "year", "genre")


@dataclass(frozen=True)
class ExtendedField:
    key: str
    label_i18n_key: str
    scope: str  # "track" (per-recording) or "album" (shared by the whole release)


# Beyond the handful of fields the basic tag/album editors show, these are
# the extended tags worth exposing -- drawn from EasyID3's real key registry
# (the closest thing to a "commonly useful, actually settable" tag list;
# excludes machine-generated/opaque things like MusicBrainz IDs, ReplayGain,
# and per-role performer credits, which aren't something someone hand-types).
EXTENDED_FIELDS: tuple[ExtendedField, ...] = (
    # Album scope: describes the release as a whole, not one recording.
    ExtendedField("albumartist", "field_ext_albumartist", "album"),
    ExtendedField("compilation", "field_ext_compilation", "album"),
    ExtendedField("organization", "field_ext_organization", "album"),
    ExtendedField("copyright", "field_ext_copyright", "album"),
    ExtendedField("catalognumber", "field_ext_catalognumber", "album"),
    ExtendedField("barcode", "field_ext_barcode", "album"),
    ExtendedField("media", "field_ext_media", "album"),
    ExtendedField("releasecountry", "field_ext_releasecountry", "album"),
    ExtendedField("originaldate", "field_ext_originaldate", "album"),
    ExtendedField("language", "field_ext_language", "album"),
    ExtendedField("asin", "field_ext_asin", "album"),
    # Track scope: specific to one recording.
    ExtendedField("composer", "field_ext_composer", "track"),
    ExtendedField("conductor", "field_ext_conductor", "track"),
    ExtendedField("lyricist", "field_ext_lyricist", "track"),
    ExtendedField("arranger", "field_ext_arranger", "track"),
    ExtendedField("performer", "field_ext_performer", "track"),
    ExtendedField("comment", "field_ext_comment", "track"),
    ExtendedField("mood", "field_ext_mood", "track"),
    ExtendedField("bpm", "field_ext_bpm", "track"),
    ExtendedField("grouping", "field_ext_grouping", "track"),
    ExtendedField("discsubtitle", "field_ext_discsubtitle", "track"),
    ExtendedField("isrc", "field_ext_isrc", "track"),
    ExtendedField("encodedby", "field_ext_encodedby", "track"),
    ExtendedField("version", "field_ext_version", "track"),
    ExtendedField("website", "field_ext_website", "track"),
    ExtendedField("author", "field_ext_author", "track"),
)

# EasyID3.valid_keys also contains role-suffixed patterns ("performer:*",
# "replaygain_*_gain", ...) that aren't plain settable keys -- only the
# literal ones are real field names.
_EASY_ID3_EXTENDED_KEYS = frozenset(k for k in EasyID3.valid_keys if "*" not in k)
_EASY_MP4_EXTENDED_KEYS = frozenset(EasyMP4Tags.Get.keys())


def editable_extended_fields(fmt: str, scope: str) -> list[ExtendedField]:
    """Extended fields (see EXTENDED_FIELDS) actually editable for the given
    audio format ('mp3', 'flac', 'm4a', ...) and scope ('track' or 'album').
    A field mutagen can't write for that format is left out entirely,
    instead of being offered and failing at save time."""
    fmt = fmt.lower().lstrip(".")
    if fmt in ("flac", "ogg"):
        supported = None  # Vorbis Comment: freeform, every key is valid.
    elif fmt in ("mp3", "wav"):
        supported = _EASY_ID3_EXTENDED_KEYS
    elif fmt in ("m4a", "aac"):
        supported = _EASY_MP4_EXTENDED_KEYS
    else:
        return []
    return [f for f in EXTENDED_FIELDS if f.scope == scope and (supported is None or f.key in supported)]


def _easy_wave(path: Path) -> EasyID3:
    """Bind an EasyID3-style plain string key/value view onto a .wav file's
    ID3 chunk. mutagen doesn't wire WAVE into its automatic "easy" tag
    dispatch (MutagenFile(path, easy=True)) -- its ID3 chunk is a distinct
    type (_WaveID3, RIFF-aware) rather than a plain ID3, so easy=True leaves
    audio.tags as raw ID3 frames (keyed "TIT2", not "title"). This does by
    hand what that dispatch does automatically for MP3/FLAC/OggVorbis/MP4:
    point a fresh EasyID3's internal tag store at the WAVE file's real one,
    so the familiar "artist"/"title"/... keys read and write the actual
    RIFF-embedded data instead of silently doing nothing."""
    wav = WAVE(str(path))
    if wav.tags is None:
        wav.add_tags()
    easy = EasyID3()
    easy._EasyID3__id3 = wav.tags  # name-mangled: EasyID3 stores it as self.__id3
    return easy


def _open_easy(path: Path):
    """The mutagen "easy" (plain string key/value) tag view for `path`, or
    None if it can't be opened. Used by both read_tags() and
    read_extended_tags() so .wav's special-casing (see _easy_wave) only
    lives in one place."""
    try:
        if path.suffix.lower() == ".wav":
            return _easy_wave(path)
        audio = MutagenFile(str(path), easy=True)
    except Exception:
        return None
    return audio.tags if audio is not None else None


def read_tags(path: Path) -> dict:
    result = {f: "" for f in COMMON_FIELDS}
    tags = _open_easy(path)

    if tags is not None:
        result["artist"] = _first(tags.get("artist"))
        result["album"] = _first(tags.get("album"))
        result["title"] = _first(tags.get("title"))
        result["track_number"], result["track_total"] = _split_track_number(_first(tags.get("tracknumber")))
        result["disc_number"], _ = _split_track_number(_first(tags.get("discnumber")))
        result["year"] = _first(tags.get("date")) or _first(tags.get("year"))
        result["genre"] = _first(tags.get("genre"))

    if not result["title"]:
        result["title"] = path.stem

    return result


def read_extended_tags(path: Path, keys: list[str]) -> dict[str, str]:
    """Current values for the given extended field keys (see
    EXTENDED_FIELDS), for prefilling the extended tag editor. Missing or
    unreadable values come back as "" rather than being left out, so callers
    don't need to guard for missing keys."""
    result = {key: "" for key in keys}
    tags = _open_easy(path)
    if tags is not None:
        for key in keys:
            result[key] = _first(tags.get(key))
    return result


MEDIA_INFO_FIELDS = (
    "codec",
    "sample_rate",
    "bit_depth",
    "channels",
    "duration",
    "bitrate",
    "file_size",
)


def read_media_info(path: Path) -> dict:
    """Returns technical info about the audio stream (codec, sample rate, bit depth,
    channels, duration in seconds, bitrate in bps, file size in bytes). Any value
    that couldn't be determined is left as None.
    """
    result = {f: None for f in MEDIA_INFO_FIELDS}
    try:
        result["file_size"] = path.stat().st_size
    except OSError:
        pass

    try:
        audio = MutagenFile(str(path))
    except Exception:
        audio = None

    if audio is None or audio.info is None:
        return result

    info = audio.info
    result["sample_rate"] = getattr(info, "sample_rate", None)
    result["bit_depth"] = getattr(info, "bits_per_sample", None)
    result["channels"] = getattr(info, "channels", None)
    result["duration"] = getattr(info, "length", None)
    result["codec"] = (
        getattr(info, "codec_description", None) or getattr(info, "codec", None) or path.suffix.lstrip(".").upper()
    )

    bitrate = getattr(info, "bitrate", None)
    if bitrate:
        result["bitrate"] = bitrate
    elif result["duration"] and result["file_size"]:
        result["bitrate"] = int(result["file_size"] * 8 / result["duration"])

    return result


def _split_track_number(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    if "/" in value:
        number, _, total = value.partition("/")
        return number.strip(), total.strip()
    return value.strip(), ""


def _combine_track_number(number: str, total: str) -> str:
    number = (number or "").strip()
    total = (total or "").strip()
    if number and total:
        return f"{number}/{total}"
    return number


def _first(value) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def write_tags(path: Path, fields: dict) -> None:
    ext = path.suffix.lower()
    if ext == ".mp3":
        _write_id3_easy(path, fields)
    elif ext == ".flac":
        audio = FLAC(str(path))
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext == ".ogg":
        audio = OggVorbis(str(path))
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext in (".m4a", ".aac"):
        audio = EasyMP4(str(path))
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext == ".wav":
        audio = _easy_wave(path)
        _apply_easy_fields(audio, fields)
        audio.save(str(path))
    else:
        raise ValueError(f"Nieobsługiwany format do edycji tagów: {ext}")


def _write_id3_easy(path: Path, fields: dict) -> None:
    try:
        audio = EasyID3(str(path))
    except ID3NoHeaderError:
        audio = MP3(str(path), ID3=ID3)
        audio.add_tags()
        audio.save()
        audio = EasyID3(str(path))
    _apply_easy_fields(audio, fields)
    audio.save()


# field name -> easy/Vorbis-comment key, for the fields with a name mismatch.
# Everything else (all of EXTENDED_FIELDS) maps to itself.
_BASIC_TEXT_FIELD_MAP = {
    "artist": "artist",
    "album": "album",
    "title": "title",
    "year": "date",
    "genre": "genre",
}


def _apply_easy_fields(audio, fields: dict) -> None:
    """Applies `fields` to any mutagen "easy" (plain string key/value) tag
    object -- EasyID3, EasyMP4, or a FLAC/OggVorbis file's native Vorbis
    Comment dict, all of which share the same dict-like interface. This one
    function backs every format write_tags() supports."""
    field_map = {**_BASIC_TEXT_FIELD_MAP, **{f.key: f.key for f in EXTENDED_FIELDS}}
    for field_name, tag_key in field_map.items():
        if field_name in fields and fields[field_name] is not None:
            value = str(fields[field_name])
            if value == "":
                if tag_key in audio:
                    del audio[tag_key]
            else:
                audio[tag_key] = value

    if "track_number" in fields or "track_total" in fields:
        combined = _combine_track_number(fields.get("track_number", ""), fields.get("track_total", ""))
        if combined == "":
            if "tracknumber" in audio:
                del audio["tracknumber"]
        else:
            audio["tracknumber"] = combined

    if "disc_number" in fields:
        disc_value = str(fields["disc_number"] or "").strip()
        if disc_value == "":
            if "discnumber" in audio:
                del audio["discnumber"]
        else:
            audio["discnumber"] = disc_value


def fix_track_number(value: str) -> str:
    value = (value or "").strip()
    if value.isdigit() and len(value) == 1:
        return value.zfill(2)
    return value


def sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    return "image/jpeg"


def read_cover_art(path: Path) -> bytes | None:
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            audio = ID3(str(path))
            for tag in audio.values():
                if isinstance(tag, APIC):
                    return tag.data
        elif ext == ".flac":
            audio = FLAC(str(path))
            if audio.pictures:
                return audio.pictures[0].data
        elif ext in (".m4a", ".aac"):
            audio = MP4(str(path))
            covers = audio.get("covr")
            if covers:
                return bytes(covers[0])
    except Exception:
        return None
    return None


def write_cover_art(path: Path, image_bytes: bytes, mime: str = "image/jpeg") -> None:
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            audio = ID3(str(path))
        except ID3NoHeaderError:
            audio = ID3()
        audio.delall("APIC")
        audio.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
        audio.save(str(path))
    elif ext == ".flac":
        audio = FLAC(str(path))
        audio.clear_pictures()
        pic = Picture()
        pic.data = image_bytes
        pic.type = 3
        pic.mime = mime
        audio.add_picture(pic)
        audio.save()
    elif ext in (".m4a", ".aac"):
        audio = MP4(str(path))
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
        audio.save()
    else:
        raise ValueError(f"Nieobsługiwana edycja okładki dla formatu: {ext}")
