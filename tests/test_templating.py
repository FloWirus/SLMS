import pytest

from music_sync.db import Track
from music_sync.templating import (
    build_relative_target_path,
    render_template,
    sanitize_component,
    validate_template,
)


def make_track(**overrides) -> Track:
    values = dict(
        id=None, path="a.flac", filename="a.flac", hash="h",
        artist="Artist", album="Album", title="Title",
        track_number="3", track_total="12", disc_number="1",
        year="1999", genre="Rock", format="flac",
    )
    values.update(overrides)
    return Track(**values)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Normal Name", "Normal Name"),
        ("with/slash", "withslash"),
        ("a:b?c*d", "abcd"),
        ("  spaced   out  ", "spaced out"),
        ("...", "_"),
        ("", "_"),
        # "/" stripped, then leading dots trimmed -- nothing left to walk up with.
        ("../evil", "evil"),
    ],
)
def test_sanitize_component(raw, expected):
    assert sanitize_component(raw) == expected


def test_render_template_pads_numbers_and_fills_blanks():
    track = make_track(track_number="3", artist="", year="")
    rendered = render_template("{track} {artist} {year}", track)
    assert rendered == "03 Unknown Artist 0000"


def test_render_template_rejects_unknown_placeholder():
    with pytest.raises(ValueError):
        render_template("{nosuchfield}", make_track())


def test_build_relative_target_path():
    track = make_track()
    assert build_relative_target_path("{artist}/{album}", "{track}. {title}", track) == (
        "Artist/Album/03. Title.flac"
    )


def test_build_relative_target_path_without_directory_template():
    assert build_relative_target_path("", "{title}", make_track()) == "Title.flac"


@pytest.mark.parametrize("template", ["/music/{artist}", "../{artist}", "C:/music/{artist}", "{artist}/../.."])
def test_validate_template_rejects_escaping_templates(template):
    assert validate_template(template) is not None


@pytest.mark.parametrize("template", ["{artist}/{album}", "{artist} - {album}", ""])
def test_validate_template_accepts_relative_templates(template):
    assert validate_template(template) is None


@pytest.mark.parametrize("dir_template", ["/absolute/{album}", "../{album}", "./{album}"])
def test_rendered_path_never_escapes_the_target(dir_template):
    """Second line of defence: settings saved by an older version are not
    re-validated, so rendering itself has to stay inside the target."""
    rendered = build_relative_target_path(dir_template, "{title}", make_track())
    assert not rendered.startswith("/")
    assert ".." not in rendered.split("/")
