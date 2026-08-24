from music_sync.cover_cache import CoverCache, hash_cover


def test_put_then_get_roundtrip(tmp_path):
    cache = CoverCache(tmp_path / "covers")
    key = hash_cover(b"original artwork")
    cache.put(key, 500, 72, "image/jpeg", b"resized")
    assert cache.get(key, 500, 72) == (b"resized", "image/jpeg")
    cache.close()


def test_different_resize_settings_are_different_entries(tmp_path):
    cache = CoverCache(tmp_path / "covers")
    key = hash_cover(b"artwork")
    cache.put(key, 500, 72, "image/jpeg", b"small")
    assert cache.get(key, 1000, 72) is None
    cache.close()


def test_a_deleted_cache_file_is_a_miss_not_an_error(tmp_path):
    cache = CoverCache(tmp_path / "covers")
    key = hash_cover(b"artwork")
    cache.put(key, 500, 72, "image/jpeg", b"resized")
    for path in (tmp_path / "covers").rglob("*.jpg"):
        path.unlink()
    assert cache.get(key, 500, 72) is None
    cache.close()


def test_nothing_is_written_outside_the_cache_directory(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    cache = CoverCache(tmp_path / "covers")
    cache.put(hash_cover(b"artwork"), 500, 72, "image/png", b"resized")
    assert list(library.iterdir()) == []
    cache.close()
