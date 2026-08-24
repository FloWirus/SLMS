import pytest
import requests

from music_sync import tidal_cover


def album(id_, title, artist, type_="ALBUM", cover="uuid"):
    return {"id": id_, "title": title, "type": type_, "cover": cover,
            "artist": {"name": artist}, "artists": [{"name": artist}]}


def test_normalize_folds_polish_letters():
    assert tidal_cover._normalize("Trębusz Młodzieży") == "trebusz mlodziezy"
    assert tidal_cover._normalize("Trebusz") == tidal_cover._normalize("Trębusz")


@pytest.mark.parametrize(
    "title, expected",
    [("Album CD1", "Album"), ("Album (CD 2)", "Album"), ("Album - CD3", "Album"),
     ("Album", "Album"), ("CD1", "CD1"), ("CD Projekt Red", "CD Projekt Red")],
)
def test_strip_disc_suffix(title, expected):
    assert tidal_cover._strip_disc_suffix(title) == expected


def test_token_overlap():
    assert tidal_cover._token_overlap("hello world", "world of hello") == 1.0
    assert tidal_cover._token_overlap("hello world", "hello") == 0.5
    assert tidal_cover._token_overlap("", "anything") == 0.0


def test_find_album_prefers_the_right_artist(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL",))
    monkeypatch.setattr(tidal_cover, "_search", lambda q, c: [
        album(1, "Greatest Hits", "Someone Else"),
        album(2, "Greatest Hits", "Wanted Artist"),
    ])
    assert tidal_cover.find_album("Wanted Artist", "Greatest Hits")["id"] == 2


def test_find_album_prefers_an_album_over_a_single_on_a_tie(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL",))
    monkeypatch.setattr(tidal_cover, "_search", lambda q, c: [
        album(1, "Song", "Artist", type_="SINGLE"),
        album(2, "Song", "Artist", type_="ALBUM"),
    ])
    assert tidal_cover.find_album("Artist", "Song")["id"] == 2


def test_find_album_pools_regions_and_deduplicates(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL", "US"))
    monkeypatch.setattr(tidal_cover, "_search",
                        lambda q, c: [album(7, "Only Here", "Artist")] if c == "US" else [])
    assert tidal_cover.find_album("Artist", "Only Here")["id"] == 7


def test_find_album_reports_nothing_found(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL",))
    monkeypatch.setattr(tidal_cover, "_search", lambda q, c: [])
    with pytest.raises(tidal_cover.TidalCoverError):
        tidal_cover.find_album("Artist", "Album")


def test_find_album_surfaces_a_network_failure_rather_than_not_found(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL",))

    def boom(query, country):
        raise requests.ConnectionError("no network")

    monkeypatch.setattr(tidal_cover, "_search", boom)
    with pytest.raises(requests.RequestException):
        tidal_cover.find_album("Artist", "Album")


def test_a_rejected_token_is_reported_as_unavailable(monkeypatch):
    class Response:
        status_code = 403

    monkeypatch.setattr(tidal_cover.requests, "get", lambda *a, **k: Response())
    with pytest.raises(tidal_cover.TidalUnavailableError):
        tidal_cover._search("query", "PL")


def test_cover_url():
    assert tidal_cover.cover_url("aaaa-bbbb-cccc", 640).endswith("/aaaa/bbbb/cccc/640x640.jpg")
