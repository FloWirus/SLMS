"""Cover art lookup against Tidal's public (unauthenticated) search API."""

import re
import unicodedata
from difflib import SequenceMatcher

import requests

from .i18n import tr

TIDAL_TOKEN = "CzET4vdadNUFQ5JU"
SEARCH_URL = "https://api.tidal.com/v1/search/albums"
# Tidal's search is a fuzzy text search, not an exact lookup -- for an
# ambiguous query (short artist/album names, names that are substrings of
# other artists/albums, etc.) the top hit is frequently the wrong album. Ask
# for more candidates and re-rank them ourselves against the actual query
# instead of trusting result order.
SEARCH_LIMIT = 20
# Tidal's search only returns albums actually licensed in the requested
# region's catalog -- a single hardcoded country silently drops albums
# Tidal doesn't distribute there (e.g. "US" is missing plenty of Polish
# releases). Guessing the "right" region from the artist's name isn't
# reliable either (most artist names carry no nationality signal, and
# plenty of non-English artists release under ASCII names), so instead
# every region below is queried and the results are pooled before ranking
# -- whichever region actually has the album, it gets found.
#
# Each entry costs one extra HTTP round-trip per lookup (the requests are
# sequential), and regional catalogs overlap heavily, so this is kept short
# on purpose: PL covers Polish releases, US covers the international
# mainstream. Add a code below only if albums are actually being missed.
#
# countryCode takes an ISO 3166-1 alpha-2 code; the catalogue below is
# roughly the markets Tidal operates in, grouped the way the settings dialog
# presents them. Tidal changes markets over time -- an unsupported code
# doesn't error, it just comes back with no items, so an outdated entry
# costs a wasted request rather than a failure.
COUNTRY_REGIONS: dict[str, tuple[str, ...]] = {
    "region_europe": (
        "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
        "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LT", "LU", "LV", "MT", "NL",
        "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "UA",
    ),
    "region_americas": ("AR", "BR", "CA", "CL", "CO", "DO", "JM", "MX", "PE", "PR", "US"),
    "region_apac": ("AU", "HK", "MY", "NZ", "SG", "TH"),
    "region_mea": ("AE", "IL", "NG", "TR", "UG", "ZA"),
}
KNOWN_COUNTRIES = frozenset(code for codes in COUNTRY_REGIONS.values() for code in codes)

# What gets queried when the user hasn't picked anything else: PL covers
# Polish releases, US the international mainstream, FI catches Nordic
# releases the other two miss. Kept short on purpose -- every extra region is
# one more sequential HTTP round-trip per lookup, and regional catalogues
# overlap heavily.
DEFAULT_SEARCH_COUNTRIES = ("PL", "US", "FI")

# The regions actually queried, pooled before ranking. Replaced at startup
# (and whenever the setting changes) via set_search_countries(); still a
# plain module global so a caller can override it directly.
SEARCH_COUNTRIES = DEFAULT_SEARCH_COUNTRIES


def set_search_countries(codes) -> tuple[str, ...]:
    """Set which regional catalogues cover lookups query, and return what was
    actually applied.

    Unknown codes are dropped and an empty selection falls back to the
    defaults: a settings file listing only regions Tidal has since dropped
    (or nothing at all) must not turn cover search into a feature that
    silently finds nothing."""
    global SEARCH_COUNTRIES
    selected = tuple(dict.fromkeys(code for code in codes if code in KNOWN_COUNTRIES))
    SEARCH_COUNTRIES = selected or DEFAULT_SEARCH_COUNTRIES
    return SEARCH_COUNTRIES


def country_name(code: str) -> str:
    """Readable name for an ISO code, from Qt's own locale data -- "PL" alone
    is not something anyone should have to decode in a checkbox list."""
    from PySide6.QtCore import QLocale

    territory = QLocale.codeToTerritory(code)
    name = QLocale.territoryToString(territory)
    return f"{code} — {name}" if name else code
# Once candidates are within this margin of the best text-match score,
# they're treated as an equally valid textual match (e.g. a single and the
# album it's later folded into both containing the same title word) and
# release type breaks the tie instead.
CLOSE_MATCH_MARGIN = 0.08
# Tie-break priority by release type, applied only among close text matches:
# a proper album beats an EP beats a single, since singles/EPs are usually
# re-released (under the same or a near-identical title) as part of a later
# full album.
_TYPE_PRIORITY = {"ALBUM": 2, "EP": 1, "SINGLE": 0}
# Characters unicodedata's NFKD decomposition does NOT break into a base
# letter + combining accent (they're distinct letterforms, not precomposed
# accented Latin ones) -- left alone, the ascii-encode/ignore step below
# would silently drop them instead of folding them, e.g. "młodzieży" would
# lose its "ł" entirely and become "modziezy" rather than "mlodziezy". That
# previously made this word compare as *less* similar to plain-"l" spelling
# variants (which Tidal's own catalog metadata is inconsistent about) than
# to completely unrelated titles.
# Trailing disc markers as they show up in ripped folder/tag names:
# "CD1", "cd 2", "(CD3)", "[CD 4]", " - CD2". Only a *trailing* marker is
# removed, so an album that legitimately has "CD" mid-title is untouched.
_DISC_SUFFIX_RE = re.compile(
    r"[\s\-\u2013\u2014_]*[\(\[\{]?\s*cd\s*\d+\s*[\)\]\}]?\s*$",
    re.IGNORECASE,
)
_MANUAL_FOLDS = str.maketrans(
    {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss"}
)


def _normalize(text: str) -> str:
    """Lowercase, strip accents/diacritics, and collapse everything that
    isn't alphanumeric to single spaces, so e.g. "Trębusz" and "Trebusz"
    compare equal."""
    text = text.translate(_MANUAL_FOLDS)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _token_overlap(query: str, candidate: str) -> float:
    """Fraction of the query's words that appear in the candidate -- much
    more robust than character-level similarity for short titles, where e.g.
    "Trebusz" vs. an unrelated title can score deceptively high just from
    shared letters."""
    query_tokens = set(_normalize(query).split())
    if not query_tokens:
        return 0.0
    candidate_tokens = set(_normalize(candidate).split())
    return len(query_tokens & candidate_tokens) / len(query_tokens)


def _artist_score(item: dict, artist: str) -> float:
    """Best match across *every* artist credited on the album, not just the
    main one. Tidal files collaborations and duet albums under a single
    lead artist (e.g. "Kasia i Błażej" sits under Nosowska, with Błażej
    Król only in the `artists` list) -- scoring the lead alone makes such an
    album look like a wrong-artist hit, and any unrelated album by the
    queried artist then outranks it despite a perfect title match."""
    names = [
        credited.get("name", "")
        for credited in (item.get("artists") or [])
        if credited.get("name")
    ]
    main_artist = (item.get("artist") or {}).get("name", "")
    if main_artist:
        names.append(main_artist)
    if not names:
        return 0.0
    return max(_similarity(artist, name) for name in names)


def _match_score(item: dict, artist: str, album: str) -> float:
    item_title = item.get("title", "")
    artist_score = _artist_score(item, artist)
    title_score = 0.7 * _token_overlap(album, item_title) + 0.3 * _similarity(album, item_title)
    # Weighted toward the artist: a wrong artist with a similarly-named
    # album is a worse mismatch than a slightly different album title from
    # the right artist (e.g. deluxe/remaster suffixes).
    return 0.6 * artist_score + 0.4 * title_score


def _strip_disc_suffix(album: str) -> str:
    """Drop a trailing disc marker ("... CD1", "... (CD 2)") from an album
    title. Multi-disc rips carry the disc number in the folder/tag name, but
    Tidal lists such releases under the plain album title -- leaving the
    marker in makes every disc past the first score worse (or match the
    wrong release) purely because of a token the catalog never contains."""
    stripped = _DISC_SUFFIX_RE.sub("", album).strip()
    # An album genuinely titled just "CD1" would strip to nothing -- keep
    # the original rather than searching for an empty title.
    return stripped or album


class TidalCoverError(Exception):
    """Raised when no album/cover could be found on Tidal for a query."""


class TidalUnavailableError(TidalCoverError):
    """Raised when Tidal rejects the request itself rather than not finding
    anything: the hardcoded API token above belongs to Tidal's own public web
    player and can be revoked or rotated at any time, which comes back as a
    401/403 on every query. Distinguished from "album not found" so the user
    is told the feature is unavailable instead of the app quietly concluding
    that none of their albums exist."""


# Rejections of the token/request as such, as opposed to a lookup that
# simply found nothing.
_AUTH_STATUS_CODES = (401, 403)


def _search(query: str, country: str) -> list[dict]:
    resp = requests.get(
        SEARCH_URL,
        params={"query": query, "limit": SEARCH_LIMIT, "countryCode": country},
        headers={"x-tidal-token": TIDAL_TOKEN},
        timeout=10,
    )
    if resp.status_code in _AUTH_STATUS_CODES:
        raise TidalUnavailableError(tr("error_tidal_unavailable", status=resp.status_code))
    resp.raise_for_status()
    return resp.json().get("items", [])


def find_album(artist: str, album: str) -> dict:
    # Match/score against the disc-less title too, not just the query --
    # otherwise the stripped marker would come back as a scoring penalty.
    album = _strip_disc_suffix(album)
    query = f"{artist} {album}"
    # Pooled across regions and de-duplicated by id -- the same album can
    # legitimately turn up (with the same id) under several catalogs.
    by_id: dict[object, dict] = {}
    last_error: requests.RequestException | None = None
    any_succeeded = False
    for country in SEARCH_COUNTRIES:
        try:
            items = _search(query, country)
        except requests.RequestException as exc:
            last_error = exc
            continue
        any_succeeded = True
        for item in items:
            item_id = item.get("id")
            if item_id is not None and item_id not in by_id:
                by_id[item_id] = item

    if not any_succeeded and last_error is not None:
        # Every region's request failed outright (e.g. no network) -- surface
        # that instead of a misleading "not found".
        raise last_error
    if not by_id:
        raise TidalCoverError(f"Album not found on Tidal: {artist} - {album}")
    # Only candidates with cover art are worth ranking -- a perfect text
    # match with no artwork is useless to this feature.
    candidates = [item for item in by_id.values() if item.get("cover")] or list(by_id.values())
    # Scored once and carried through the tie-break below. The tie-break used
    # to re-run _match_score (which normalises and diffs every artist name
    # credited on the album) for each close match it compared.
    scored = [(item, _match_score(item, artist, album)) for item in candidates]
    best_score = max(score for _, score in scored)
    close_matches = [(item, score) for item, score in scored if score >= best_score - CLOSE_MATCH_MARGIN]
    best, _score = max(
        close_matches,
        key=lambda pair: (_TYPE_PRIORITY.get(pair[0].get("type"), 0), pair[1]),
    )
    return best


def cover_url(cover_uuid: str, size: int = 1280) -> str:
    path = cover_uuid.replace("-", "/")
    return f"https://resources.tidal.com/images/{path}/{size}x{size}.jpg"


def download_cover_bytes(artist: str, album: str, size: int = 1280) -> bytes:
    """Look up `artist`/`album` on Tidal (across SEARCH_COUNTRIES) and return
    the raw JPEG bytes of its cover art. Raises TidalCoverError if no
    matching album (or no cover on that album) is found, or
    requests.RequestException if every region's request failed."""
    result = find_album(artist, album)
    cover_uuid = result.get("cover")
    if not cover_uuid:
        raise TidalCoverError(f"Album has no cover art on Tidal: {artist} - {album}")
    resp = requests.get(cover_url(cover_uuid, size), timeout=15)
    resp.raise_for_status()
    return resp.content
