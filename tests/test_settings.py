import json

from music_sync.constants import DB_DIRNAME, SETTINGS_FILENAME
from music_sync.settings import Settings


def settings_file(root):
    return root / DB_DIRNAME / SETTINGS_FILENAME


def test_save_and_load_roundtrip(tmp_path):
    settings = Settings(dir_template="{artist}", language="pl", profiles=[{"name": "car"}])
    settings.save(tmp_path)
    loaded = Settings.load(tmp_path)
    assert loaded.dir_template == "{artist}"
    assert loaded.language == "pl"
    assert loaded.profiles == [{"name": "car"}]


def test_save_leaves_no_temp_file_behind(tmp_path):
    Settings().save(tmp_path)
    assert [p.name for p in settings_file(tmp_path).parent.iterdir()] == [SETTINGS_FILENAME]


def test_missing_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path) == Settings()


def test_corrupt_file_gives_defaults(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")
    assert Settings.load(tmp_path) == Settings()


def test_values_of_the_wrong_type_fall_back_to_defaults(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"dir_template": 42, "profiles": "not a list", "language": "pl"}))
    loaded = Settings.load(tmp_path)
    assert loaded.dir_template == Settings().dir_template
    assert loaded.profiles == []
    assert loaded.language == "pl"  # the one sane value is still honoured


def test_malformed_profiles_are_dropped(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"profiles": [{"name": "ok"}, "nonsense", {"no_name": 1}]}))
    assert Settings.load(tmp_path).profiles == [{"name": "ok"}]


def test_unknown_keys_are_ignored(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"from_a_future_version": True}))
    assert Settings.load(tmp_path) == Settings()


def test_tidal_countries_default_and_roundtrip(tmp_path):
    from music_sync.tidal_cover import DEFAULT_SEARCH_COUNTRIES

    assert Settings().tidal_countries == list(DEFAULT_SEARCH_COUNTRIES)
    settings = Settings(tidal_countries=["DE", "PL"])
    settings.save(tmp_path)
    assert Settings.load(tmp_path).tidal_countries == ["DE", "PL"]
