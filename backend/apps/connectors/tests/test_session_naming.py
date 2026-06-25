"""_compose_session_name: explicit name verbatim; auto-name = agent · repo · time."""

from apps.connectors.service import _compose_session_name


def test_explicit_name_used_verbatim():
    # Operator's chosen name is respected (not over-composed).
    assert _compose_session_name("claude", "/Users/x/dev/Repo", "Hotfix") == "Hotfix"


def test_auto_name_is_agent_repo_time():
    n = _compose_session_name("claude", "/Users/x/dev/OpenRemote-Control", "")
    assert n.startswith("claude · OpenRemote-Control · ")
    assert n != "claude · OpenRemote-Control"  # has a time component


def test_auto_name_unknown_tool_falls_back_to_session():
    n = _compose_session_name("unknown", "/home/x/work/myrepo", "")
    assert n.startswith("session · myrepo · ")


def test_auto_name_empty_tool_falls_back_to_session():
    n = _compose_session_name("", "/home/x/work/myrepo", "")
    assert n.startswith("session · myrepo · ")


def test_auto_name_handles_trailing_slash_and_missing_repo():
    assert _compose_session_name("claude", "/Users/x/dev/Repo/", "").startswith("claude · Repo · ")
    # No workspace root -> agent · time (repo omitted)
    assert _compose_session_name("claude", "", "").startswith("claude · ")
