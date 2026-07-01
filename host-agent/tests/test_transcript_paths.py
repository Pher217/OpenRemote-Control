

from agent_host.transcript_paths import claude_transcript_path


def test_primary_path_found(tmp_path, monkeypatch):
    """GIVEN the encoded cwd dir exists with the session file
    WHEN claude_transcript_path is called with that cwd and session_id
    THEN the exact primary path is returned.
    """
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(
        "agent_host.transcript_paths.os.path.expanduser",
        lambda _: str(projects),
    )

    enc = "-Users-x-dev-My-Proj"
    sess_dir = projects / enc
    sess_dir.mkdir()
    transcript = sess_dir / "abc123.jsonl"
    transcript.write_text("{}")

    result = claude_transcript_path("/Users/x/dev/My.Proj", "abc123")
    assert result == str(transcript)


def test_fallback_glob_single_match(tmp_path, monkeypatch):
    """GIVEN the primary path does not exist but a single glob match does
    WHEN claude_transcript_path is called
    THEN the fallback path is returned.
    """
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(
        "agent_host.transcript_paths.os.path.expanduser",
        lambda _: str(projects),
    )

    other = projects / "some-other-enc"
    other.mkdir()
    transcript = other / "sess42.jsonl"
    transcript.write_text("{}")

    result = claude_transcript_path("/does/not/match", "sess42")
    assert result == str(transcript)


def test_not_found_returns_none(tmp_path, monkeypatch):
    """GIVEN an empty projects directory
    WHEN claude_transcript_path is called
    THEN None is returned.
    """
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(
        "agent_host.transcript_paths.os.path.expanduser",
        lambda _: str(projects),
    )

    result = claude_transcript_path("/any/cwd", "nonexistent")
    assert result is None


def test_ambiguous_glob_returns_none(tmp_path, monkeypatch):
    """GIVEN two directories each contain the same session_id file
    WHEN claude_transcript_path is called
    THEN None is returned because the match is ambiguous.
    """
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(
        "agent_host.transcript_paths.os.path.expanduser",
        lambda _: str(projects),
    )

    (projects / "dir-a").mkdir()
    (projects / "dir-a" / "dup77.jsonl").write_text("{}")
    (projects / "dir-b").mkdir()
    (projects / "dir-b" / "dup77.jsonl").write_text("{}")

    result = claude_transcript_path("/any/cwd", "dup77")
    assert result is None
