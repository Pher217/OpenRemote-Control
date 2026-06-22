"""Tests for the SDK turn runner's permission-title construction.

The full run_turn flow is integration-verified against the live SDK + CLI
(deny blocks a Write, allow lets it through); these unit tests cover the pure
approval-text helper that builds the Telegram prompt line.
"""

from agent_host.sdk_session import _permission_title


class _Ctx:
    def __init__(self, title=None):
        self.title = title


def test_prefers_sdk_title_when_present():
    """GIVEN the SDK supplies a title WHEN building the line THEN it is used verbatim."""
    assert _permission_title("Write", {"file_path": "/x"}, _Ctx("Claude wants to read foo")) == "Claude wants to read foo"


def test_builds_title_from_file_path():
    """GIVEN no SDK title WHEN the tool has a file_path THEN it is included."""
    assert _permission_title("Write", {"file_path": "/tmp/a.txt"}, _Ctx()) == "Claude wants to use Write: /tmp/a.txt"


def test_builds_title_from_command():
    """GIVEN a Bash command WHEN no title THEN the command is shown."""
    assert _permission_title("Bash", {"command": "rm -rf x"}, _Ctx()) == "Claude wants to use Bash: rm -rf x"


def test_falls_back_to_tool_name_only():
    """GIVEN no title and no known target field THEN just the tool name is shown."""
    assert _permission_title("SomeTool", {}, _Ctx()) == "Claude wants to use SomeTool"
