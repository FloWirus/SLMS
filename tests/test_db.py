from music_sync.db import MusicDatabase, Track, device_db_path, library_db_path


def make_track(path="a.wav", hash_="h1", **overrides):
    values = dict(id=None, path=path, filename=path.split("/")[-1], hash=hash_, source_hash=hash_,
                  artist="A", album="B", title="C", track_number="1", track_total="2",
                  disc_number="1", year="2001", genre="Rock", format="wav", size=1, mtime=1.0)
    values.update(overrides)
    return Track(**values)


def test_upsert_is_keyed_on_path(tmp_path):
    db = MusicDatabase(tmp_path / "x.db")
    first = db.upsert_track(make_track(title="Old"))
    second = db.upsert_track(make_track(title="New"))
    assert first == second
    assert db.get_by_path("a.wav").title == "New"
    db.close()


def test_remove_missing_keeps_only_listed_paths(tmp_path):
    db = MusicDatabase(tmp_path / "x.db")
    db.upsert_track(make_track("a.wav", "h1"))
    db.upsert_track(make_track("b.wav", "h2"))
    db.remove_missing({"a.wav"})
    assert [t.path for t in db.all_tracks()] == ["a.wav"]
    db.close()


def test_reassign_source_hash_keeps_the_link_after_a_tag_edit(tmp_path):
    db = MusicDatabase(tmp_path / "device.db")
    db.upsert_track(make_track("Artist/a.wav", hash_="device-hash", source_hash="library-hash"))
    assert db.reassign_source_hash("library-hash", "new-library-hash") == 1
    assert db.get_by_source_hash("new-library-hash") is not None
    assert db.source_hashes() == {"new-library-hash"}
    db.close()


def test_batch_commits_once(tmp_path):
    db = MusicDatabase(tmp_path / "x.db")
    with db.batch():
        for i in range(5):
            db.upsert_track(make_track(f"{i}.wav", f"h{i}"))
    assert len(db.all_tracks()) == 5
    db.close()


def test_library_databases_are_per_source_directory(tmp_path):
    first = library_db_path(tmp_path, tmp_path / "music-one")
    second = library_db_path(tmp_path, tmp_path / "music-two")
    assert first != second
    assert library_db_path(tmp_path, tmp_path / "music-one") == first  # stable across calls


def test_legacy_library_path_has_no_source_root(tmp_path):
    assert library_db_path(tmp_path).name == "library.db"


def test_device_database_lives_in_a_hidden_directory(tmp_path):
    assert device_db_path(tmp_path).parent.name.startswith(".")


def test_device_database_migrates_the_old_visible_directory(tmp_path):
    old = tmp_path / "music_db"
    old.mkdir()
    (old / "device.db").write_bytes(b"")
    path = device_db_path(tmp_path)
    assert path.parent.name == ".music_db" and path.is_file()
    assert not old.exists()
