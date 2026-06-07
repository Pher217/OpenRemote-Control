"""
Tests for tailer.py — read_new_lines and OffsetStore.
"""

from __future__ import annotations

from agent_host.tailer import OffsetStore, read_new_lines


class TestReadNewLines:
    def test_reads_complete_lines_from_start(self, tmp_path):
        """
        GIVEN a file with two complete lines
        WHEN read_new_lines is called with offset=0
        THEN both lines are returned and new_offset points past the last newline.
        """
        f = tmp_path / "session.jsonl"
        f.write_bytes(b'{"a":1}\n{"b":2}\n')

        lines, new_offset = read_new_lines(str(f), 0)

        assert lines == ['{"a":1}\n', '{"b":2}\n']
        assert new_offset == len(b'{"a":1}\n{"b":2}\n')

    def test_partial_last_line_excluded(self, tmp_path):
        """
        GIVEN a file where the last line has no trailing newline
        WHEN read_new_lines is called
        THEN the partial line is excluded and offset stops at the last complete newline.
        """
        f = tmp_path / "session.jsonl"
        content = b'{"a":1}\n{"partial"'
        f.write_bytes(content)

        lines, new_offset = read_new_lines(str(f), 0)

        assert lines == ['{"a":1}\n']
        assert new_offset == len(b'{"a":1}\n')

    def test_offset_advances_correctly(self, tmp_path):
        """
        GIVEN a file with two lines already read
        WHEN more lines are appended and read_new_lines called with the previous offset
        THEN only the new lines are returned.
        """
        f = tmp_path / "session.jsonl"
        first_content = b'{"a":1}\n{"b":2}\n'
        f.write_bytes(first_content)

        _, offset = read_new_lines(str(f), 0)

        # Append more.
        with open(f, "ab") as fh:
            fh.write(b'{"c":3}\n')

        lines, new_offset = read_new_lines(str(f), offset)
        assert lines == ['{"c":3}\n']
        assert new_offset == len(first_content) + len(b'{"c":3}\n')

    def test_no_new_data_returns_same_offset(self, tmp_path):
        """
        GIVEN no new bytes since last call
        WHEN read_new_lines is called
        THEN it returns empty list and the same offset.
        """
        f = tmp_path / "session.jsonl"
        f.write_bytes(b'{"a":1}\n')
        _, offset = read_new_lines(str(f), 0)

        lines, new_offset = read_new_lines(str(f), offset)
        assert lines == []
        assert new_offset == offset

    def test_missing_file_returns_empty(self, tmp_path):
        """A missing file returns empty list and unchanged offset."""
        lines, off = read_new_lines(str(tmp_path / "nope.jsonl"), 0)
        assert lines == []
        assert off == 0

    def test_file_with_only_partial_line(self, tmp_path):
        """A file with no newlines at all returns empty list."""
        f = tmp_path / "session.jsonl"
        f.write_bytes(b'incomplete')

        lines, offset = read_new_lines(str(f), 0)
        assert lines == []
        assert offset == 0

    def test_unicode_content(self, tmp_path):
        """Lines with UTF-8 multi-byte characters are handled correctly."""
        f = tmp_path / "session.jsonl"
        line = '{"msg":"héllo"}\n'
        f.write_bytes(line.encode("utf-8"))

        lines, _ = read_new_lines(str(f), 0)
        assert lines == [line]


class TestOffsetStore:
    def test_get_returns_zero_for_unknown_path(self, tmp_path):
        """
        GIVEN an empty offset store
        WHEN get() is called for an unknown path
        THEN it returns 0.
        """
        store = OffsetStore(tmp_path / "offsets.json")
        assert store.get("/some/path.jsonl") == 0

    def test_set_then_get_returns_value(self, tmp_path):
        """
        GIVEN an offset is set for a path
        WHEN get() is called for the same path
        THEN it returns the stored value.
        """
        store = OffsetStore(tmp_path / "offsets.json")
        store.set("/some/path.jsonl", 1024)
        assert store.get("/some/path.jsonl") == 1024

    def test_persisted_across_instances(self, tmp_path):
        """
        GIVEN an offset is saved by one OffsetStore instance
        WHEN a new instance is created with the same path
        THEN it can read the saved offset.
        """
        path = tmp_path / "offsets.json"
        store1 = OffsetStore(path)
        store1.set("/file.jsonl", 512)

        store2 = OffsetStore(path)
        assert store2.get("/file.jsonl") == 512

    def test_multiple_paths_stored_independently(self, tmp_path):
        store = OffsetStore(tmp_path / "offsets.json")
        store.set("/a.jsonl", 100)
        store.set("/b.jsonl", 200)

        assert store.get("/a.jsonl") == 100
        assert store.get("/b.jsonl") == 200

    def test_overwrite_updates_value(self, tmp_path):
        store = OffsetStore(tmp_path / "offsets.json")
        store.set("/a.jsonl", 100)
        store.set("/a.jsonl", 999)
        assert store.get("/a.jsonl") == 999
