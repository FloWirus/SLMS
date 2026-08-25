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


def test_set_search_countries_applies_the_selection(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", tidal_cover.DEFAULT_SEARCH_COUNTRIES)
    assert tidal_cover.set_search_countries(["DE", "PL"]) == ("DE", "PL")
    assert tidal_cover.SEARCH_COUNTRIES == ("DE", "PL")


def test_set_search_countries_drops_unknown_codes_and_duplicates(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", tidal_cover.DEFAULT_SEARCH_COUNTRIES)
    assert tidal_cover.set_search_countries(["PL", "ZZ", "PL", "US"]) == ("PL", "US")


def test_an_empty_selection_falls_back_to_the_defaults(monkeypatch):
    """Otherwise a stale settings file (or a catalogue Tidal dropped) would
    turn cover search into a feature that quietly finds nothing."""
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", ("PL",))
    assert tidal_cover.set_search_countries([]) == tidal_cover.DEFAULT_SEARCH_COUNTRIES
    assert tidal_cover.set_search_countries(["ZZ"]) == tidal_cover.DEFAULT_SEARCH_COUNTRIES


def test_lookups_query_exactly_the_configured_regions(monkeypatch):
    monkeypatch.setattr(tidal_cover, "SEARCH_COUNTRIES", tidal_cover.DEFAULT_SEARCH_COUNTRIES)
    tidal_cover.set_search_countries(["PL", "DE", "BR"])
    asked = []

    def search(query, country):
        asked.append(country)
        return [album(1, "Album", "Artist")]

    monkeypatch.setattr(tidal_cover, "_search", search)
    tidal_cover.find_album("Artist", "Album")
    assert asked == ["PL", "DE", "BR"]


def test_every_region_lists_only_known_two_letter_codes():
    codes = [code for group in tidal_cover.COUNTRY_REGIONS.values() for code in group]
    assert len(codes) == len(set(codes)), "a country is listed in two regions"
    assert all(len(code) == 2 and code.isupper() for code in codes)
    assert set(tidal_cover.DEFAULT_SEARCH_COUNTRIES) <= tidal_cover.KNOWN_COUNTRIES


def test_country_name_is_readable():
    assert tidal_cover.country_name("PL").startswith("PL")
    assert "Poland" in tidal_cover.country_name("PL")
