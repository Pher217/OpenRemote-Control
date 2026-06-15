"""Aider runtime adapter.

Locates ``.aider.chat.history.md`` Markdown chat-history files and parses
user/assistant turns into normalized conversation records (role/text/uuid/session_id).
"""
import os

from apps.observe.runtimes import JsonlScanMixin, register_runtime_adapter

# TODO-VERIFY: Aider Markdown chat-history format is loosely documented and
# varies by version. The following constants isolate every assumption so they
# can be updated once the real format is known.

# --- Turn markers ---------------------------------------------------------
# Assumed: user message lines start with this exact Markdown heading prefix.
# TODO-VERIFY: confirm exact prefix in .aider.chat.history.md across versions.
AIDER_USER_PREFIX = "#### "

# --- Assistant turn assumptions -------------------------------------------
# Assumed: text between user markers belongs to the assistant turn; there is
# no explicit assistant marker in the Markdown history.
# TODO-VERIFY: newer Aider versions may introduce an assistant marker.
AIDER_ASSISTANT_MARKER = None

# --- Structural / non-conversational lines --------------------------------
# Assumed: common markdown structural markers are non-conversational and
# should be dropped. Empty lines are handled before this check.
# TODO-VERIFY: expand this list once the real format is known.
AIDER_STRUCTURAL_PREFIXES = {"---", "***", "___", "```"}


@register_runtime_adapter
class AiderAdapter(JsonlScanMixin):
    provider = "aider"
    source_kind = "file"
    default_root_env = "OBSERVE_AIDER_PROJECTS_DIR"
    default_root = os.path.expanduser("~")
    discovery_glob = "**/.aider.chat.history.md"

    def parse_turn(self, raw: str) -> dict | None:
        text = raw.rstrip()
        if not text:
            return None

        if text.startswith(AIDER_USER_PREFIX):
            message = text[len(AIDER_USER_PREFIX) :].strip()
            if not message:
                return None
            role = "user"
        else:
            if any(text.startswith(prefix) for prefix in AIDER_STRUCTURAL_PREFIXES):
                return None
            message = text
            role = "assistant"

        if not message.strip():
            return None

        return {
            "role": role,
            "text": message,
            "uuid": None,
            "session_id": None,
            "source": self.provider,
            "taint": "observed",
        }

    def extract_session_meta(self, raw: str) -> dict:
        return {}

