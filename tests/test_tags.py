import pytest

from music_sync import tags as tagsmod


@pytest.mark.parametrize(
    "raw, expected",
    [("3/12", ("3", "12")), (" 5 / 9 ", ("5", "9")), ("7", ("7", "")), ("", ("", ""))],
)
def test_split_track_number(raw, expected):
    assert tagsmod._split_track_number(raw) == expected


@pytest.mark.parametrize(
    "number, total, expected",
    [("3", "12", "3/12"), ("3", "", "3"), ("", "12", ""), ("", "", "")],
)
def test_combine_track_number(number, total, expected):
    assert tagsmod._combine_track_number(number, total) == expected


@pytest.mark.parametrize("raw, expected", [("1", "01"), ("01", "01"), ("12", "12"), ("A", "A"), ("", "")])
def test_fix_track_number(raw, expected):
    assert tagsmod.fix_track_number(raw) == expected


def test_sniff_image_mime():
    assert tagsmod.sniff_image_mime(b"\x89PNG\r\n") == "image/png"
    assert tagsmod.sniff_image_mime(b"\xff\xd8\xff") == "image/jpeg"


def test_editable_extended_fields_per_format():
    # Vorbis Comment takes any key; ID3/MP4/ASF only the ones they define;
    # raw AAC has nowhere to put tags at all.
    assert len(tagsmod.editable_extended_fields("flac", "track")) > len(
        tagsmod.editable_extended_fields("m4a", "track")
    )
    assert tagsmod.editable_extended_fields("aac", "track") == []
    assert tagsmod.editable_extended_fields("wma", "track"), "WMA must offer its mapped fields"


ROUNDTRIP_FIELDS = {
    "artist": "Wykonawca",
    "album": "Album",
    "title": "Tytuł",
    "track_number": "3",
    "track_total": "12",
    "disc_number": "2",
    "year": "1999",
    "genre": "Rock",
}


def test_wav_tag_roundtrip(wav_factory):
    """Guards tags._easy_wave, which binds an EasyID3 view onto a WAVE file
    through mutagen's private __id3 attribute -- if a mutagen upgrade ever
    breaks that, tags silently stop being written, and only this notices."""
    path = wav_factory("tagged.wav")
    tagsmod.write_tags(path, ROUNDTRIP_FIELDS)
    assert tagsmod.read_tags(path) == ROUNDTRIP_FIELDS


def test_wav_cover_roundtrip(wav_factory):
    path = wav_factory("cover.wav")
    image = b"\xff\xd8\xff" + b"jpeg-bytes" * 10
    tagsmod.write_cover_art(path, image, "image/jpeg")
    assert tagsmod.read_cover_art(path) == image


def test_extended_tags_roundtrip(wav_factory):
    path = wav_factory("extended.wav")
    tagsmod.write_tags(path, {**ROUNDTRIP_FIELDS, "composer": "Kompozytor"})
    assert tagsmod.read_extended_tags(path, ["composer", "conductor"]) == {
        "composer": "Kompozytor",
        "conductor": "",
    }


def test_writing_a_blank_value_removes_the_tag(wav_factory):
    path = wav_factory("cleared.wav")
    tagsmod.write_tags(path, ROUNDTRIP_FIELDS)
    tagsmod.write_tags(path, {**ROUNDTRIP_FIELDS, "genre": ""})
    assert tagsmod.read_tags(path)["genre"] == ""


def test_unsupported_format_reports_itself(tmp_path):
    path = tmp_path / "track.xyz"
    path.write_bytes(b"not audio")
    with pytest.raises(ValueError):
        tagsmod.write_tags(path, ROUNDTRIP_FIELDS)


def test_read_tags_falls_back_to_the_filename(wav_factory):
    path = wav_factory("Some Title.wav")
    assert tagsmod.read_tags(path)["title"] == "Some Title"
