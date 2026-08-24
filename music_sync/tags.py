import base64
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.asf import ASF
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from mutagen.easymp4 import EasyMP4, EasyMP4Tags
from mutagen.wave import WAVE

from .i18n import tr

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

# ASF/WMA has no "easy" wrapper in mutagen, so this is the mapping the
# _EasyASF adapter below uses to make a .wma file behave like every other
# format here. Keys are the same plain names EasyID3/EasyMP4/Vorbis use;
# values are the ASF attribute names Windows Media and every WMA-capable
# player actually read.
_ASF_KEY_MAP = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "date": "WM/Year",
    "genre": "WM/Genre",
    "tracknumber": "WM/TrackNumber",
    "discnumber": "WM/PartOfSet",
    "albumartist": "WM/AlbumArtist",
    "composer": "WM/Composer",
    "conductor": "WM/Conductor",
    "lyricist": "WM/Writer",
    "publisher": "WM/Publisher",
    "organization": "WM/Publisher",
    "copyright": "Copyright",
    "comment": "Description",
    "mood": "WM/Mood",
    "bpm": "WM/BeatsPerMinute",
    "isrc": "WM/ISRC",
    "encodedby": "WM/EncodedBy",
    "language": "WM/Language",
    "barcode": "WM/Barcode",
    "media": "WM/Media",
    "grouping": "WM/ContentGroupDescription",
}
_ASF_EXTENDED_KEYS = frozenset(_ASF_KEY_MAP)


class _EasyASF:
    """Plain string key/value view over a .wma file's ASF attributes.

    mutagen ships "easy" wrappers for MP3/MP4/Vorbis but not for ASF, so a
    .wma file used to read as untagged (its keys are "Author", "WM/Year", ...
    not "artist"/"date") and refuse to save at all -- despite .wma being in
    AUDIO_EXTENSIONS and its files being scanned, listed and synced like any
    other. This adapter maps the handful of names that matter (see
    _ASF_KEY_MAP) so read_tags(), read_extended_tags() and
    _apply_easy_fields() all work on WMA unchanged. Keys with no ASF
    equivalent are ignored rather than raising: writing a tag the container
    can't hold is not an error worth failing a save over."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._asf = ASF(str(self._path))

    def _attr(self, key: str) -> str | None:
        return _ASF_KEY_MAP.get(key)

    def get(self, key, default=None):
        attr = self._attr(key)
        if attr is None:
            return default
        values = self._asf.get(attr)
        return [str(value) for value in values] if values else default

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key) -> bool:
        attr = self._attr(key)
        return bool(attr and self._asf.get(attr))

    def __setitem__(self, key, value) -> None:
        attr = self._attr(key)
        if attr is None:
            return
        self._asf[attr] = [value] if isinstance(value, str) else list(value)

    def __delitem__(self, key) -> None:
        attr = self._attr(key)
        if attr is not None and attr in self._asf:
            del self._asf[attr]

    def save(self, *args) -> None:
        self._asf.save()


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
    elif fmt == "m4a":
        supported = _EASY_MP4_EXTENDED_KEYS
    elif fmt == "wma":
        supported = _ASF_EXTENDED_KEYS
    else:
        # Includes .aac: raw ADTS streams have nowhere to put tags at all
        # (mutagen: "doesn't support tags"), so offering fields for them
        # would only produce failures at save time.
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
    suffix = path.suffix.lower()
    try:
        if suffix == ".wav":
            return _easy_wave(path)
        if suffix == ".wma":
            return _EasyASF(path)
        if suffix == ".aac":
            # ".aac" is usually a raw ADTS stream (no tag container at all),
            # but the extension is also used for plain MP4/AAC files, which
            # tag fine -- so try that and fall through to "no tags" instead
            # of guessing from the extension.
            audio = MutagenFile(str(path), easy=True)
            return audio.tags if audio is not None else None
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
    elif ext == ".m4a":
        audio = EasyMP4(str(path))
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext == ".aac":
        # Only MP4-in-.aac can hold tags; a raw ADTS stream can not (see
        # _open_easy), and mutagen says so by refusing to open it as MP4.
        try:
            audio = EasyMP4(str(path))
        except Exception as exc:
            raise ValueError(tr("error_unsupported_tag_format", ext=ext)) from exc
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext == ".wma":
        audio = _EasyASF(path)
        _apply_easy_fields(audio, fields)
        audio.save()
    elif ext == ".wav":
        audio = _easy_wave(path)
        _apply_easy_fields(audio, fields)
        audio.save(str(path))
    else:
        raise ValueError(tr("error_unsupported_tag_format", ext=ext))


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


# Vorbis Comment key holding a base64-encoded FLAC picture block. This is
# how Ogg carries embedded artwork -- it has no picture block of its own the
# way FLAC does, so the block is base64'd into an ordinary comment field.
OGG_PICTURE_KEY = "metadata_block_picture"


def _first_id3_cover(tags) -> bytes | None:
    for tag in (tags or {}).values():
        if isinstance(tag, APIC):
            return tag.data
    return None


def read_cover_art(path: Path) -> bytes | None:
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            return _first_id3_cover(ID3(str(path)))
        if ext == ".flac":
            audio = FLAC(str(path))
            if audio.pictures:
                return audio.pictures[0].data
        elif ext == ".ogg":
            audio = OggVorbis(str(path))
            encoded = audio.get(OGG_PICTURE_KEY)
            if encoded:
                return Picture(base64.b64decode(encoded[0])).data
        elif ext == ".wav":
            return _first_id3_cover(WAVE(str(path)).tags)
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
    elif ext == ".ogg":
        audio = OggVorbis(str(path))
        picture = Picture()
        picture.data = image_bytes
        picture.type = 3
        picture.mime = mime
        audio[OGG_PICTURE_KEY] = [base64.b64encode(picture.write()).decode("ascii")]
        audio.save()
    elif ext == ".wav":
        audio = WAVE(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
        audio.save()
    elif ext in (".m4a", ".aac"):
        try:
            audio = MP4(str(path))
        except Exception as exc:
            raise ValueError(tr("error_unsupported_cover_format", ext=ext)) from exc
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
        audio.save()
    else:
        # .wma is the remaining case: ASF stores artwork in a packed binary
        # WM/Picture attribute that mutagen exposes only as raw bytes, so it
        # is reported as unsupported rather than written half-correctly.
        raise ValueError(tr("error_unsupported_cover_format", ext=ext))
