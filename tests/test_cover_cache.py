"""The resized-cover cache, in both of its layouts."""

from music_sync.cover_cache import (
    ALBUM_COVERS_DIRNAME,
    CoverCache,
    hash_cover,
    library_covers_db_path,
    open_cover_cache,
)


def app_cache(tmp_path) -> CoverCache:
    root = tmp_path / "appdata" / "covers"
    return CoverCache(root, root / "covers.db")


def library(tmp_path):
    album = tmp_path / "library" / "Artist" / "Album"
    album.mkdir(parents=True)
    return tmp_path / "library", album


def test_put_then_get_roundtrip(tmp_path):
    cache = app_cache(tmp_path)
    key = hash_cover(b"original artwork")
    cache.put(key, 500, 72, "image/jpeg", b"resized")
    assert cache.get(key, 500, 72) == (b"resized", "image/jpeg")
    cache.close()


def test_different_resize_settings_are_different_entries(tmp_path):
    cache = app_cache(tmp_path)
    key = hash_cover(b"artwork")
    cache.put(key, 500, 72, "image/jpeg", b"small")
    assert cache.get(key, 1000, 72) is None
    cache.close()


def test_a_deleted_cache_file_is_a_miss_not_an_error(tmp_path):
    cache = app_cache(tmp_path)
    key = hash_cover(b"artwork")
    cache.put(key, 500, 72, "image/jpeg", b"resized")
    for path in (tmp_path / "appdata").rglob("*.jpg"):
        path.unlink()
    assert cache.get(key, 500, 72) is None
    cache.close()


def test_app_data_layout_writes_nothing_into_the_library(tmp_path):
    root, album = library(tmp_path)
    cache = open_cover_cache(root, in_library=False)
    cache.put(hash_cover(b"artwork"), 500, 72, "image/png", b"resized", album)
    assert list(album.iterdir()) == []
    cache.close()


def test_library_layout_writes_the_cover_next_to_the_album(tmp_path):
    root, album = library(tmp_path)
    cache = CoverCache(root, library_covers_db_path(root), per_album=True)
    key = hash_cover(b"artwork")
    written = cache.put(key, 500, 72, "image/jpeg", b"resized", album)

    assert written.parent == album / ALBUM_COVERS_DIRNAME
    assert written.name == f"{key[:16]}_500_72.jpg"
    assert library_covers_db_path(root).is_file()
    # ...and reads back through the index, by a path relative to the library.
    assert cache.get(key, 500, 72) == (b"resized", "image/jpeg")
    cache.close()


def test_library_layout_survives_the_library_being_moved(tmp_path):
    """Paths are stored relative to the library root, so copying the whole
    folder elsewhere keeps the cache usable."""
    root, album = library(tmp_path)
    cache = CoverCache(root, library_covers_db_path(root), per_album=True)
    key = hash_cover(b"artwork")
    cache.put(key, 500, 72, "image/jpeg", b"resized", album)
    cache.close()

    moved = tmp_path / "moved"
    root.rename(moved)
    reopened = CoverCache(moved, library_covers_db_path(moved), per_album=True)
    assert reopened.get(key, 500, 72) == (b"resized", "image/jpeg")
    reopened.close()


def test_a_read_only_library_does_not_break_the_cache(tmp_path):
    """put() failing is a cache miss, never a failed sync."""
    root, album = library(tmp_path)
    cache = CoverCache(root, library_covers_db_path(root), per_album=True)
    album.chmod(0o500)
    try:
        assert cache.put(hash_cover(b"artwork"), 500, 72, "image/jpeg", b"resized", album) is None
    finally:
        album.chmod(0o700)
    cache.close()


def test_open_cover_cache_picks_the_layout(tmp_path, monkeypatch):
    root, album = library(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    in_library = open_cover_cache(root, in_library=True)
    assert in_library.per_album and in_library.db_path == library_covers_db_path(root)
    in_library.close()

    in_app_data = open_cover_cache(root, in_library=False)
    assert not in_app_data.per_album
    assert str(tmp_path / "xdg") in str(in_app_data.db_path)
    in_app_data.close()
