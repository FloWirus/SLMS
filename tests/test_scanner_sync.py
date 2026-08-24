"""End-to-end coverage of the scan -> sync -> delete path, on real files in a
temp directory. No GUI and no ffmpeg involved."""

import os

import pytest

from music_sync import tags as tagsmod
from music_sync.db import MusicDatabase, device_db_path
from music_sync.scanner import scan_directory
from music_sync.sync import (
    ConflictResolution,
    delete_many_from_device,
    sync_to_device,
)

from conftest import write_wav


@pytest.fixture
def db(tmp_path):
    database = MusicDatabase(tmp_path / "library.db")
    yield database
    database.close()


def tagged_library(root):
    for index, frequency in enumerate((440, 660), start=1):
        path = write_wav(root / "Artist" / "Album" / f"{index:02d}.wav", frequency=frequency)
        tagsmod.write_tags(path, {
            "artist": "Artist", "album": "Album", "title": f"Track {index}",
            "track_number": str(index), "track_total": "2", "disc_number": "1",
            "year": "2001", "genre": "Rock",
        })
    return root


def test_scan_reads_tags_and_populates_the_database(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    tracks = scan_directory(root, db)
    assert len(tracks) == 2
    assert {t.title for t in tracks} == {"Track 1", "Track 2"}
    assert all(t.track_total == "2" and t.disc_number == "1" for t in tracks)


def test_rescan_reuses_unchanged_rows(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    first = scan_directory(root, db)
    second = scan_directory(root, db)
    assert [t.hash for t in first] == [t.hash for t in second]


def test_scan_drops_rows_for_deleted_files(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    scan_directory(root, db)
    (root / "Artist" / "Album" / "01.wav").unlink()
    assert len(scan_directory(root, db)) == 1


def test_scan_survives_an_unreadable_file(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    scan_directory(root, db)
    denied = root / "Artist" / "Album" / "01.wav"
    os.chmod(denied, 0o000)
    try:
        tracks = scan_directory(root, db)
    finally:
        os.chmod(denied, 0o644)
    # The unreadable file keeps its known-good row instead of taking the
    # whole scan (and every row after it) down.
    assert len(tracks) == 2


def test_scan_does_not_follow_symlinked_directories(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    (root / "loop").symlink_to(root, target_is_directory=True)
    assert len(scan_directory(root, db)) == 2


def sync(library_root, tracks, device_root, on_conflict=None, **kwargs):
    return sync_to_device(
        library_root, tracks, device_root, "{artist}/{album}", "{track}. {title}",
        on_conflict=on_conflict or (lambda track, path: ConflictResolution.SKIP),
        **kwargs,
    )


def test_sync_copies_tracks_under_the_template_path(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    result = sync(root, scan_directory(root, db), device)
    assert result.copied == 2
    assert (device / "Artist" / "Album" / "01. Track 1.wav").is_file()


def test_sync_records_every_field_on_the_device(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    sync(root, scan_directory(root, db), device)
    device_db = MusicDatabase(device_db_path(device))
    try:
        rows = device_db.all_tracks()
        assert all(row.track_total == "2" and row.disc_number == "1" for row in rows)
    finally:
        device_db.close()


def test_second_sync_copies_nothing(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    tracks = scan_directory(root, db)
    sync(root, tracks, device)
    result = sync(root, tracks, device)
    assert (result.copied, result.already_present) == (0, 2)


def test_a_file_deleted_from_the_device_is_copied_again(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    tracks = scan_directory(root, db)
    sync(root, tracks, device)
    (device / "Artist" / "Album" / "01. Track 1.wav").unlink()
    assert sync(root, tracks, device).copied == 1


def test_conflict_is_reported_and_can_be_skipped(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    tracks = scan_directory(root, db)
    stray = device / "Artist" / "Album" / "01. Track 1.wav"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"someone else's file")

    asked = []
    result = sync(root, tracks, device, on_conflict=lambda t, p: (asked.append(p), ConflictResolution.SKIP)[1])
    assert asked and result.skipped == 1
    assert stray.read_bytes() == b"someone else's file"


def test_sync_leaves_no_temp_files_behind(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    sync(root, scan_directory(root, db), device)
    assert not list(device.rglob("*.musicsync-tmp"))


def test_cancelling_stops_early(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    result = sync(root, scan_directory(root, db), device, should_stop=lambda: True)
    assert result.cancelled and result.copied == 0


def test_delete_removes_files_and_their_now_empty_directories(tmp_path, db):
    root = tagged_library(tmp_path / "library")
    device = tmp_path / "device"
    sync(root, scan_directory(root, db), device)

    device_db = MusicDatabase(device_db_path(device))
    rows = device_db.all_tracks()
    device_db.close()

    delete_many_from_device(device, rows)
    assert not list(device.rglob("*.wav"))
    assert not (device / "Artist").exists()

    device_db = MusicDatabase(device_db_path(device))
    try:
        assert device_db.all_tracks() == []
    finally:
        device_db.close()
