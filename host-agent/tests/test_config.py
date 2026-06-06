"""
Tests for config.py — HostConfig save/load round-trip and file permissions.
"""

from __future__ import annotations

import os
import stat

import pytest

from agent_host.config import HostConfig, config_path, load, save


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Redirect config_path() to a temp file via ORC_CONFIG_PATH."""
    cfg_file = tmp_path / "host.json"
    monkeypatch.setenv("ORC_CONFIG_PATH", str(cfg_file))
    return cfg_file


class TestConfigPath:
    def test_xdg_override_respected(self, tmp_path, monkeypatch):
        """
        GIVEN XDG_CONFIG_HOME is set
        WHEN config_path() is called
        THEN it returns a path under XDG_CONFIG_HOME.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("ORC_CONFIG_PATH", raising=False)
        p = config_path()
        assert str(tmp_path) in str(p)
        assert "openremote-control" in str(p)
        assert p.name == "host.json"

    def test_orc_config_path_takes_priority(self, tmp_path, monkeypatch):
        override = tmp_path / "override.json"
        monkeypatch.setenv("ORC_CONFIG_PATH", str(override))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert config_path() == override


class TestSaveLoad:
    def test_round_trip(self, tmp_config):
        """
        GIVEN a HostConfig
        WHEN save() is called and then load()
        THEN the returned HostConfig matches the original.
        """
        cfg = HostConfig(
            backend_url="https://orc.example.com",
            host_id="abc-123",
            token="supersecrettoken",
        )
        save(cfg)
        loaded = load()
        assert loaded is not None
        assert loaded.backend_url == cfg.backend_url
        assert loaded.host_id == cfg.host_id
        assert loaded.token == cfg.token

    def test_load_returns_none_when_missing(self, tmp_config, monkeypatch):
        """
        GIVEN no config file exists
        WHEN load() is called
        THEN it returns None.
        """
        assert not tmp_config.exists()
        result = load()
        assert result is None

    def test_file_mode_is_0600(self, tmp_config):
        """
        GIVEN a saved config
        WHEN the file mode is checked
        THEN it is 0o600 (owner read/write only).
        """
        cfg = HostConfig(
            backend_url="https://orc.example.com",
            host_id="abc-123",
            token="supersecrettoken",
        )
        save(cfg)
        file_stat = os.stat(tmp_config)
        # Extract permission bits.
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"

    def test_parent_dirs_created(self, tmp_path, monkeypatch):
        """save() creates parent directories if they do not exist."""
        deep = tmp_path / "a" / "b" / "c" / "host.json"
        monkeypatch.setenv("ORC_CONFIG_PATH", str(deep))
        cfg = HostConfig(backend_url="http://localhost", host_id="x", token="t")
        save(cfg)
        assert deep.exists()

    def test_overwrite_updates_values(self, tmp_config):
        """Calling save() twice overwrites the previous config."""
        cfg1 = HostConfig(backend_url="http://a", host_id="id1", token="tok1")
        cfg2 = HostConfig(backend_url="http://b", host_id="id2", token="tok2")
        save(cfg1)
        save(cfg2)
        loaded = load()
        assert loaded is not None
        assert loaded.host_id == "id2"
        assert loaded.token == "tok2"
