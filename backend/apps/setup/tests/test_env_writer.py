"""Tests for apps.setup.env_writer: atomic, injection-safe .env updates."""

from __future__ import annotations

import stat

import pytest

from apps.setup.env_writer import (
    EnvWriteError,
    read_env,
    update_env,
    validate_key,
    validate_value,
)


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _tmp_leftovers(dir_path) -> list:
    return list(dir_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# update_env — happy path
# ---------------------------------------------------------------------------


class TestUpdateEnvHappyPath:
    def test_updating_existing_key_preserves_comments_and_blank_lines(self, tmp_path):
        """
        GIVEN an env file with a comment, a blank line, and an existing key
        WHEN update_env changes the value of that existing key
        THEN the comment and blank line survive unchanged
        """
        path = tmp_path / ".env"
        path.write_text("# a comment\n\nFOO=old\nBAR=keep\n", encoding="utf-8")
        update_env(path, {"FOO": "new"})
        content = path.read_text(encoding="utf-8")
        assert "# a comment" in content.splitlines()
        assert "" in content.splitlines()

    def test_updating_existing_key_preserves_ordering(self, tmp_path):
        """
        GIVEN an env file with keys BAR then FOO
        WHEN update_env changes FOO's value
        THEN BAR still appears before FOO in the rewritten file
        """
        path = tmp_path / ".env"
        path.write_text("BAR=1\nFOO=old\n", encoding="utf-8")
        update_env(path, {"FOO": "new"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines.index("BAR=1") < lines.index("FOO=new")

    def test_updating_existing_key_changes_its_value(self, tmp_path):
        """
        GIVEN an env file with FOO=old
        WHEN update_env sets FOO to "new"
        THEN read_env reports FOO as "new"
        """
        path = tmp_path / ".env"
        path.write_text("FOO=old\n", encoding="utf-8")
        update_env(path, {"FOO": "new"})
        assert read_env(path)["FOO"] == "new"

    def test_new_key_is_appended(self, tmp_path):
        """
        GIVEN an env file that does not contain BAZ
        WHEN update_env sets BAZ
        THEN BAZ is present afterward with the given value
        """
        path = tmp_path / ".env"
        path.write_text("FOO=old\n", encoding="utf-8")
        update_env(path, {"BAZ": "new-value"})
        assert read_env(path)["BAZ"] == "new-value"

    def test_file_is_created_when_absent(self, tmp_path):
        """
        GIVEN no env file exists yet at the target path
        WHEN update_env is called
        THEN the file exists afterward
        """
        path = tmp_path / ".env"
        update_env(path, {"FOO": "bar"})
        assert path.exists()

    def test_file_created_when_absent_has_correct_value(self, tmp_path):
        """
        GIVEN no env file exists yet at the target path
        WHEN update_env is called
        THEN the new file contains the written key/value
        """
        path = tmp_path / ".env"
        update_env(path, {"FOO": "bar"})
        assert read_env(path)["FOO"] == "bar"

    def test_written_file_has_owner_only_permissions(self, tmp_path):
        """
        GIVEN a fresh update_env call
        WHEN the resulting file's mode is inspected
        THEN it is exactly 0o600 (owner read/write only)
        """
        path = tmp_path / ".env"
        update_env(path, {"FOO": "bar"})
        assert _mode(path) == 0o600

    def test_value_containing_equals_round_trips(self, tmp_path):
        """
        GIVEN a value that itself contains an "=" character
        WHEN it is written and then re-read
        THEN the full value is recovered unchanged
        """
        path = tmp_path / ".env"
        update_env(path, {"DATABASE_URL": "postgres://user:pass@host/db?sslmode=require"})
        assert read_env(path)["DATABASE_URL"] == "postgres://user:pass@host/db?sslmode=require"

    def test_no_tmp_file_left_after_successful_write(self, tmp_path):
        """
        GIVEN a successful update_env call
        WHEN the target directory is scanned for leftover *.tmp files
        THEN none are found
        """
        path = tmp_path / ".env"
        update_env(path, {"FOO": "bar"})
        assert _tmp_leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# update_env — no-op
# ---------------------------------------------------------------------------


class TestUpdateEnvNoOp:
    def test_empty_updates_does_not_touch_existing_file(self, tmp_path):
        """
        GIVEN an existing env file
        WHEN update_env is called with an empty updates dict
        THEN the file content is byte-identical afterward
        """
        path = tmp_path / ".env"
        original = "FOO=bar\n"
        path.write_text(original, encoding="utf-8")
        update_env(path, {})
        assert path.read_bytes() == original.encode("utf-8")

    def test_empty_updates_does_not_create_a_missing_file(self, tmp_path):
        """
        GIVEN no env file exists
        WHEN update_env is called with an empty updates dict
        THEN no file is created
        """
        path = tmp_path / ".env"
        update_env(path, {})
        assert not path.exists()


# ---------------------------------------------------------------------------
# update_env — injection defense (values)
# ---------------------------------------------------------------------------


class TestUpdateEnvNewlineInjection:
    def test_value_with_newline_raises(self, tmp_path):
        """
        GIVEN a value containing "\\n"
        WHEN update_env is called
        THEN EnvWriteError is raised
        """
        path = tmp_path / ".env"
        path.write_text("FOO=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"FOO": "evil\nDEBUG=true"})

    def test_value_with_newline_does_not_modify_the_file(self, tmp_path):
        """
        GIVEN an existing env file
        WHEN update_env is rejected for a newline-carrying value
        THEN the file is byte-identical to before the call
        """
        path = tmp_path / ".env"
        original = "FOO=bar\n"
        path.write_text(original, encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"FOO": "evil\nDEBUG=true"})
        assert path.read_bytes() == original.encode("utf-8")

    def test_value_with_carriage_return_raises(self, tmp_path):
        """
        GIVEN a value containing "\\r"
        WHEN update_env is called
        THEN EnvWriteError is raised
        """
        path = tmp_path / ".env"
        path.write_text("FOO=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"FOO": "evil\rDEBUG=true"})

    def test_no_tmp_file_left_after_rejected_newline_write(self, tmp_path):
        """
        GIVEN an update rejected for carrying a newline
        WHEN the target directory is scanned for leftover *.tmp files
        THEN none are found
        """
        path = tmp_path / ".env"
        path.write_text("FOO=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"FOO": "evil\nDEBUG=true"})
        assert _tmp_leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# validate_key / update_env — invalid keys
# ---------------------------------------------------------------------------


class TestInvalidKeys:
    @pytest.mark.parametrize(
        "bad_key",
        ["lowercase", "1LEADINGDIGIT", "HAS SPACE", "A-B", ""],
    )
    def test_validate_key_rejects_invalid_key(self, bad_key):
        """
        GIVEN a key that does not match ^[A-Z][A-Z0-9_]*$
        WHEN validate_key is called
        THEN EnvWriteError is raised
        """
        with pytest.raises(EnvWriteError):
            validate_key(bad_key)

    def test_validate_key_accepts_a_valid_key(self):
        """
        GIVEN a key matching ^[A-Z][A-Z0-9_]*$
        WHEN validate_key is called
        THEN no exception is raised
        """
        validate_key("TELEGRAM_BOT_TOKEN")

    def test_update_env_rejects_invalid_key_and_leaves_file_untouched(self, tmp_path):
        """
        GIVEN an existing env file
        WHEN update_env is called with an invalid key
        THEN EnvWriteError is raised and the file is unchanged
        """
        path = tmp_path / ".env"
        original = "FOO=bar\n"
        path.write_text(original, encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"bad-key": "value"})
        assert path.read_bytes() == original.encode("utf-8")


class TestValidateValue:
    def test_validate_value_accepts_a_plain_value(self):
        """
        GIVEN a value with no newline or carriage return
        WHEN validate_value is called
        THEN no exception is raised
        """
        validate_value("a-perfectly-normal-value")


# ---------------------------------------------------------------------------
# read_env
# ---------------------------------------------------------------------------


class TestReadEnv:
    def test_read_env_missing_file_returns_empty_dict(self, tmp_path):
        """
        GIVEN a path that does not exist
        WHEN read_env is called
        THEN an empty dict is returned
        """
        assert read_env(tmp_path / "does-not-exist.env") == {}

    def test_read_env_skips_comments(self, tmp_path):
        """
        GIVEN a file containing only a comment line
        WHEN read_env is called
        THEN the resulting dict is empty
        """
        path = tmp_path / ".env"
        path.write_text("# TOKEN=should-not-appear\n", encoding="utf-8")
        assert read_env(path) == {}

    def test_read_env_skips_blank_lines(self, tmp_path):
        """
        GIVEN a file containing only blank lines
        WHEN read_env is called
        THEN the resulting dict is empty
        """
        path = tmp_path / ".env"
        path.write_text("\n\n   \n", encoding="utf-8")
        assert read_env(path) == {}

    def test_read_env_skips_malformed_lines_without_equals(self, tmp_path):
        """
        GIVEN a line that has no "=" separator
        WHEN read_env is called
        THEN that line contributes nothing to the resulting dict
        """
        path = tmp_path / ".env"
        path.write_text("this line has no separator\nFOO=bar\n", encoding="utf-8")
        assert read_env(path) == {"FOO": "bar"}

    def test_read_env_parses_a_normal_key_value_line(self, tmp_path):
        """
        GIVEN a well-formed KEY=value line
        WHEN read_env is called
        THEN the dict maps KEY to value
        """
        path = tmp_path / ".env"
        path.write_text("FOO=bar\n", encoding="utf-8")
        assert read_env(path) == {"FOO": "bar"}
