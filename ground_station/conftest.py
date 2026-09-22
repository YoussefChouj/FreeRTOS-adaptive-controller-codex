"""Session-wide guard: no test module may leave the stdlib patched.

A test file patched ``socket.socket`` at import time to keep its own
constructors from binding UDP 14550, and never put it back.  Every module
collected afterwards -- the whole `service` tree, which sorts after
`livewatch` -- then got a ``MagicMock`` where the socket class should be, and
failed while unpacking ``self.socket.getsockname()`` into nothing.  That was
68 failures and 32 errors in a full-tree run, and zero when any of those files
was run on its own, so the suite looked healthy in exactly the way it is
usually checked.

The guard is here rather than in the offending file because the failure mode
belongs to the tree, not to that file: the next module-level patch of a
shared global would be just as invisible, and would again be blamed on the
code it broke rather than on the code that did it.
"""
import socket
import subprocess
import time

import pytest

# Attributes whose replacement leaks across every later module. Each is one a
# test has a plausible reason to fake, and a catastrophic one to keep faked.
_GLOBALS = [
    (socket, "socket"),
    (socket, "create_connection"),
    (subprocess, "run"),
    (subprocess, "Popen"),
    (time, "sleep"),
]


# Snapshotted here, at conftest import, which pytest does before it imports a
# single test module. A fixture cannot take this snapshot: its setup runs after
# the module body, so an import-time patch -- the one that actually happened --
# would already be baked into the "pristine" values it recorded.
_PRISTINE = [(mod, name, getattr(mod, name)) for mod, name in _GLOBALS]


@pytest.fixture(autouse=True, scope="module")
def _stdlib_is_not_left_patched():
    """Fail the module that replaced a shared global, not its victims."""
    yield
    leaked = []
    for mod, name, original in _PRISTINE:
        if getattr(mod, name) is not original:
            leaked.append("%s.%s" % (mod.__name__, name))
            # Put it back: one bad module should cost one failure, not all of
            # the failures it would otherwise cause downstream.
            setattr(mod, name, original)
    if leaked:
        pytest.fail(
            "this module left the stdlib patched: %s. Patch inside a fixture "
            "or `mock.patch` context so it is restored; a module-level "
            "assignment persists for the whole pytest session."
            % ", ".join(leaked))
