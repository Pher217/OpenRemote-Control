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
        path.write_text(
            "# a comment\n\nTELEGRAM_BOT_TOKEN=old\nTELEGRAM_FORUM_CHAT_ID=keep\n",
            encoding="utf-8",
        )
        update_env(path, {"TELEGRAM_BOT_TOKEN": "new"})
        content = path.read_text(encoding="utf-8")
        assert "# a comment" in content.splitlines()
        assert "" in content.splitlines()

    def test_updating_existing_key_preserves_ordering(self, tmp_path):
        """
        GIVEN an env file with keys TELEGRAM_FORUM_CHAT_ID then TELEGRAM_BOT_TOKEN
        WHEN update_env changes TELEGRAM_BOT_TOKEN's value
        THEN TELEGRAM_FORUM_CHAT_ID still appears before TELEGRAM_BOT_TOKEN
        """
        path = tmp_path / ".env"
        path.write_text(
            "TELEGRAM_FORUM_CHAT_ID=1\nTELEGRAM_BOT_TOKEN=old\n", encoding="utf-8"
        )
        update_env(path, {"TELEGRAM_BOT_TOKEN": "new"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines.index("TELEGRAM_FORUM_CHAT_ID=1") < lines.index(
            "TELEGRAM_BOT_TOKEN='new'"
        )

    def test_updating_existing_key_changes_its_value(self, tmp_path):
        """
        GIVEN an env file with TELEGRAM_BOT_TOKEN=old
        WHEN update_env sets TELEGRAM_BOT_TOKEN to "new"
        THEN read_env reports TELEGRAM_BOT_TOKEN as "new"
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")
        update_env(path, {"TELEGRAM_BOT_TOKEN": "new"})
        assert read_env(path)["TELEGRAM_BOT_TOKEN"] == "new"

    def test_new_key_is_appended(self, tmp_path):
        """
        GIVEN an env file that does not contain ANTHROPIC_API_KEY
        WHEN update_env sets ANTHROPIC_API_KEY
        THEN ANTHROPIC_API_KEY is present afterward with the given value
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")
        update_env(path, {"ANTHROPIC_API_KEY": "new-value"})
        assert read_env(path)["ANTHROPIC_API_KEY"] == "new-value"

    def test_file_is_created_when_absent(self, tmp_path):
        """
        GIVEN no env file exists yet at the target path
        WHEN update_env is called
        THEN the file exists afterward
        """
        path = tmp_path / ".env"
        update_env(path, {"TELEGRAM_BOT_TOKEN": "bar"})
        assert path.exists()

    def test_file_created_when_absent_has_correct_value(self, tmp_path):
        """
        GIVEN no env file exists yet at the target path
        WHEN update_env is called
        THEN the new file contains the written key/value
        """
        path = tmp_path / ".env"
        update_env(path, {"TELEGRAM_BOT_TOKEN": "bar"})
        assert read_env(path)["TELEGRAM_BOT_TOKEN"] == "bar"

    def test_written_file_has_owner_only_permissions(self, tmp_path):
        """
        GIVEN a fresh update_env call
        WHEN the resulting file's mode is inspected
        THEN it is exactly 0o600 (owner read/write only)
        """
        path = tmp_path / ".env"
        update_env(path, {"TELEGRAM_BOT_TOKEN": "bar"})
        assert _mode(path) == 0o600

    def test_value_containing_equals_round_trips(self, tmp_path):
        """
        GIVEN a value that itself contains an "=" character
        WHEN it is written and then re-read
        THEN the full value is recovered unchanged
        """
        path = tmp_path / ".env"
        update_env(path, {"OLLAMA_BASE_URL": "http://host/db?sslmode=require"})
        assert read_env(path)["OLLAMA_BASE_URL"] == "http://host/db?sslmode=require"

    def test_no_tmp_file_left_after_successful_write(self, tmp_path):
        """
        GIVEN a successful update_env call
        WHEN the target directory is scanned for leftover *.tmp files
        THEN none are found
        """
        path = tmp_path / ".env"
        update_env(path, {"TELEGRAM_BOT_TOKEN": "bar"})
        assert _tmp_leftovers(tmp_path) == []

    def test_written_value_is_single_quoted_in_raw_file(self, tmp_path):
        """
        GIVEN a fresh update_env call
        WHEN the raw file content is inspected
        THEN the value appears single-quoted (Compose $VAR interpolation defense)
        """
        path = tmp_path / ".env"
        update_env(path, {"TELEGRAM_BOT_TOKEN": "bar"})
        content = path.read_text(encoding="utf-8")
        assert "TELEGRAM_BOT_TOKEN='bar'" in content

    def test_value_containing_dollar_var_round_trips_through_read_env(self, tmp_path):
        """
        GIVEN a value containing a literal "$VAR" style reference
        WHEN it is written (single-quoted) and then re-read
        THEN the value is recovered unchanged, not interpolated
        """
        path = tmp_path / ".env"
        update_env(path, {"OLLAMA_BASE_URL": "http://$HOST/path"})
        assert read_env(path)["OLLAMA_BASE_URL"] == "http://$HOST/path"


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
        original = "TELEGRAM_BOT_TOKEN=bar\n"
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
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"TELEGRAM_BOT_TOKEN": "evil\nDEBUG=true"})

    def test_value_with_newline_does_not_modify_the_file(self, tmp_path):
        """
        GIVEN an existing env file
        WHEN update_env is rejected for a newline-carrying value
        THEN the file is byte-identical to before the call
        """
        path = tmp_path / ".env"
        original = "TELEGRAM_BOT_TOKEN=bar\n"
        path.write_text(original, encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"TELEGRAM_BOT_TOKEN": "evil\nDEBUG=true"})
        assert path.read_bytes() == original.encode("utf-8")

    def test_value_with_carriage_return_raises(self, tmp_path):
        """
        GIVEN a value containing "\\r"
        WHEN update_env is called
        THEN EnvWriteError is raised
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"TELEGRAM_BOT_TOKEN": "evil\rDEBUG=true"})

    def test_no_tmp_file_left_after_rejected_newline_write(self, tmp_path):
        """
        GIVEN an update rejected for carrying a newline
        WHEN the target directory is scanned for leftover *.tmp files
        THEN none are found
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"TELEGRAM_BOT_TOKEN": "evil\nDEBUG=true"})
        assert _tmp_leftovers(tmp_path) == []

    @pytest.mark.parametrize(
        "separator",
        [
            "\x0b",  # line tabulation
            "\x0c",  # form feed
            "\x1c",  # file separator
            "\x1d",  # group separator
            "\x1e",  # record separator
            "\x85",  # next line
            " ",  # line separator
            " ",  # paragraph separator
        ],
    )
    def test_validate_value_rejects_every_splitlines_separator(self, separator):
        """
        GIVEN a value containing a character str.splitlines() treats as a line break
        WHEN validate_value is called
        THEN EnvWriteError is raised
        """
        with pytest.raises(EnvWriteError):
            validate_value(f"evil{separator}DEBUG=true")

    def test_validate_value_rejects_nul(self):
        """
        GIVEN a value containing a NUL byte
        WHEN validate_value is called
        THEN EnvWriteError is raised
        """
        with pytest.raises(EnvWriteError):
            validate_value("evil\x00value")

    def test_validate_value_rejects_single_quote(self):
        """
        GIVEN a value containing a single quote
        WHEN validate_value is called
        THEN EnvWriteError is raised
        """
        with pytest.raises(EnvWriteError):
            validate_value("evil'value")

    def test_second_order_injection_via_vertical_tab_is_rejected(self, tmp_path):
        """
        GIVEN an attacker-controlled value using \\x0b to smuggle a second
             assignment line (splitlines() would later parse it as a real line,
             even though a naive \\n/\\r-only check would miss it)
        WHEN update_env is called with that value
        THEN EnvWriteError is raised
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(
                path,
                {"TELEGRAM_BOT_TOKEN": "x\x0bORC_CONNECTOR_TOKEN=pwned"},
            )

    def test_second_order_injection_leaves_no_smuggled_key_after_later_legit_write(
        self, tmp_path
    ):
        """
        GIVEN a rejected \\x0b-smuggled update attempt against ORC_CONNECTOR_TOKEN
        WHEN a subsequent legitimate update_env call is made
        THEN the file contains no line starting with "ORC_CONNECTOR_TOKEN"
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(
                path,
                {"TELEGRAM_BOT_TOKEN": "x\x0bORC_CONNECTOR_TOKEN=pwned"},
            )
        update_env(path, {"TELEGRAM_BOT_TOKEN": "legit-value"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert not any(line.startswith("ORC_CONNECTOR_TOKEN") for line in lines)


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
        GIVEN a key matching ^[A-Z][A-Z0-9_]*$ that is also allowlisted
        WHEN validate_key is called
        THEN no exception is raised
        """
        validate_key("TELEGRAM_BOT_TOKEN")

    def test_update_env_rejects_invalid_key_and_leaves_file_untouched(self, tmp_path):
        """
        GIVEN an existing env file
        WHEN update_env is called with a shape-invalid key
        THEN EnvWriteError is raised and the file is unchanged
        """
        path = tmp_path / ".env"
        original = "TELEGRAM_BOT_TOKEN=bar\n"
        path.write_text(original, encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {"bad-key": "value"})
        assert path.read_bytes() == original.encode("utf-8")

    @pytest.mark.parametrize(
        "non_allowlisted_key",
        ["SECRET_KEY", "POSTGRES_PASSWORD", "ALLOWED_HOSTS", "DEBUG"],
    )
    def test_validate_key_rejects_shape_valid_but_non_allowlisted_key(
        self, non_allowlisted_key
    ):
        """
        GIVEN a key that matches ^[A-Z][A-Z0-9_]*$ but is not in WRITABLE_KEYS
        WHEN validate_key is called
        THEN EnvWriteError is raised
        """
        with pytest.raises(EnvWriteError):
            validate_key(non_allowlisted_key)

    @pytest.mark.parametrize(
        "non_allowlisted_key",
        ["SECRET_KEY", "POSTGRES_PASSWORD", "ALLOWED_HOSTS", "DEBUG"],
    )
    def test_update_env_rejects_non_allowlisted_key_and_leaves_file_untouched(
        self, tmp_path, non_allowlisted_key
    ):
        """
        GIVEN an existing env file
        WHEN update_env is called with a shape-valid but non-allowlisted key
        THEN EnvWriteError is raised and the file is unchanged
        """
        path = tmp_path / ".env"
        original = "TELEGRAM_BOT_TOKEN=bar\n"
        path.write_text(original, encoding="utf-8")
        with pytest.raises(EnvWriteError):
            update_env(path, {non_allowlisted_key: "value"})
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
        path.write_text(
            "this line has no separator\nTELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8"
        )
        assert read_env(path) == {"TELEGRAM_BOT_TOKEN": "bar"}

    def test_read_env_parses_a_normal_key_value_line(self, tmp_path):
        """
        GIVEN a well-formed KEY=value line
        WHEN read_env is called
        THEN the dict maps KEY to value
        """
        path = tmp_path / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=bar\n", encoding="utf-8")
        assert read_env(path) == {"TELEGRAM_BOT_TOKEN": "bar"}
