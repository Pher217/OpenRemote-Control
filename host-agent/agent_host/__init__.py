"""
agent_host — Host-agent daemon for OpenRemote Control.

Public re-exports from the safety core and PTY session manager.
Importing this package does NOT import libtmux; that import is deferred
inside PtySession methods so that the package is safe to use in
environments where libtmux is not installed (e.g. pure-unit-test runners).
"""

from agent_host.input_policy import Risk, classify_input, is_control_sequence

# PtySession is imported at the name level so callers can do:
#   from agent_host import PtySession
# without triggering a libtmux import.  libtmux is only touched when a
# PtySession *method* is actually called.
from agent_host.pty_session import PtySession

__all__ = [
    "Risk",
    "classify_input",
    "is_control_sequence",
    "PtySession",
]
