from app import settings_store


def test_defaults_when_nothing_stored(SessionFactory):
    eff = settings_store.effective()
    assert eff.movies_dir == "Movies"
    assert eff.write_nfo is True
    assert eff.sync_interval_hours == 0


def test_save_and_effective_roundtrip(SessionFactory):
    settings_store.save(
        {
            "movies_dir": "Films",
            "write_nfo": "false",
            "sync_interval_hours": "8",
            "tmdb_enabled": "true",
            "tmdb_api_key": "abc123",
        }
    )
    eff = settings_store.effective()
    assert eff.movies_dir == "Films"
    assert eff.write_nfo is False
    assert eff.sync_interval_hours == 8
    assert eff.tmdb_enabled is True
    assert eff.tmdb_api_key == "abc123"


def test_blank_string_falls_back_to_default(SessionFactory):
    settings_store.save({"movies_dir": ""})
    assert settings_store.effective().movies_dir == "Movies"


def test_bad_int_is_ignored(SessionFactory):
    settings_store.save({"sync_interval_hours": "not-a-number"})
    assert settings_store.effective().sync_interval_hours == 0


def test_unchecked_bool_becomes_false(SessionFactory):
    settings_store.save({"tmdb_enabled": "true"})
    assert settings_store.effective().tmdb_enabled is True
    settings_store.save({"movies_dir": "X"})  # form without tmdb_enabled key
    assert settings_store.effective().tmdb_enabled is False
