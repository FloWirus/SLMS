from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TRCK, TDRC, TCON, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

COMMON_FIELDS = ("artist", "album", "title", "track_number", "track_total", "disc_number", "year", "genre")


def read_tags(path: Path) -> dict:
    result = {f: "" for f in COMMON_FIELDS}
    try:
        audio = MutagenFile(str(path), easy=True)
    except Exception:
        audio = None

    if audio is not None and audio.tags is not None:
        tags = audio.tags
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
        audio = MP4(str(path))
        _apply_mp4_fields(audio, fields)
        audio.save()
    elif ext == ".wav":
        audio = WAVE(str(path))
        _apply_easy_fields(audio, fields)
        audio.save()
    else:
        raise ValueError(f"Nieobsługiwany format do edycji tagów: {ext}")


def _write_id3_easy(path: Path, fields: dict) -> None:
    from mutagen.easyid3 import EasyID3

    try:
        audio = EasyID3(str(path))
    except ID3NoHeaderError:
        audio = MP3(str(path), ID3=ID3)
        audio.add_tags()
        audio.save()
        audio = EasyID3(str(path))
    _apply_easy_fields(audio, fields)
    audio.save()


def _apply_easy_fields(audio, fields: dict) -> None:
    mapping = {
        "artist": "artist",
        "album": "album",
        "title": "title",
        "year": "date",
        "genre": "genre",
    }
    for field_name, tag_key in mapping.items():
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


def _apply_mp4_fields(audio: MP4, fields: dict) -> None:
    mapping = {
        "artist": "\xa9ART",
        "album": "\xa9alb",
        "title": "\xa9nam",
        "year": "\xa9day",
        "genre": "\xa9gen",
    }
    for field_name, tag_key in mapping.items():
        if field_name in fields and fields[field_name] is not None:
            value = str(fields[field_name])
            if value == "":
                audio.pop(tag_key, None)
            else:
                audio[tag_key] = [value]
    if "track_number" in fields or "track_total" in fields:
        try:
            number = int(fields.get("track_number") or 0)
            total = int(fields.get("track_total") or 0)
        except ValueError:
            number, total = 0, 0
        if number or total:
            audio["trkn"] = [(number, total)]
        else:
            audio.pop("trkn", None)

    if "disc_number" in fields:
        try:
            disc = int(fields.get("disc_number") or 0)
        except ValueError:
            disc = 0
        if disc:
            audio["disk"] = [(disc, 0)]
        else:
            audio.pop("disk", None)


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
