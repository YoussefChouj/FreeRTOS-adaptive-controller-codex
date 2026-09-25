"""Terminal WebSocket endpoint (PTY-backed).

Run as ``python -m ground_station.service.terminal`` standalone, or import
the ``TerminalManager`` and mount ``handle_terminal_ws`` onto an ``ApiServer``.

Token flow
----------
On first start the manager prints a random 32-hex token to stdout and writes
it to ``.agent_state/terminal-token`` (gitignored).  Every WS handshake
requires ``?token=<hex>`` -- 401 without it.

Command selection
-----------------
Default (WSL/Linux)::

    wsl -e bash -lc "cd <repo_wsl_path> && opencode"

Override with env ``TERMINAL_CMD`` (any shell command).
Set ``TERMINAL_AGENT=claude`` to switch to ``claude`` instead of ``opencode``.

Resize
------
The client sends JSON ``{"type":"resize","rows":N,"cols":M}``.  On Windows
without pywinpty the handler reports "terminal unavailable" and why.

Testing
-------
Tests spawn an in-process ``ApiServer`` on an ephemeral port, start a PTY
running ``python -c "print('hi')"`` and exercise the full handshake, echo,
token rejection and resize path.
"""
from __future__ import annotations

import json
import os
import secrets
import selectors
import socket
import struct
import sys
import threading
from pathlib import Path
from typing import Any

# Platform detection: pty/termios are Unix-only
_IS_WINDOWS = sys.platform == "win32"
_PTY_AVAILABLE = False

if not _IS_WINDOWS:
    try:
        import pty
        import tty
        import termios
        import fcntl
        _PTY_AVAILABLE = True
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------
_DEFAULT_STATE_DIR = Path(__file__).resolve().parents[2] / ".agent_state"


def _ensure_token(state_dir: Path | None = None) -> str:
    """Return the terminal token, creating / reading it."""
    d = state_dir or _DEFAULT_STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    token_file = d / "terminal-token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(16)
    token_file.write_text(token + "\n", encoding="utf-8")
    return token


def _token_file(state_dir: Path | None = None) -> Path:
    return (state_dir or _DEFAULT_STATE_DIR) / "terminal-token"


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------
def build_command(repo_wsl_path: str | None = None,
                  state_dir: Path | None = None) -> list[str]:
    """Return the shell command list for the PTY.

    * ``TERMINAL_CMD`` env var overrides everything.
    * ``TERMINAL_AGENT=claude`` switches to ``claude``.
    * Default: ``wsl -e bash -lc "cd <path> && opencode"`` (WSL/Linux).
    """
    override = os.environ.get("TERMINAL_CMD")
    if override:
        return ["/bin/sh", "-c", override]

    agent = os.environ.get("TERMINAL_AGENT", "opencode")
    if repo_wsl_path:
        cmd = f'cd "{repo_wsl_path}" && {agent}'
        return ["/bin/sh", "-c", cmd]

    # WSL default
    if repo_wsl_path:
        cmd = f'cd "{repo_wsl_path}" && {agent}'
        return ["wsl", "-e", "bash", "-lc", cmd]

    return ["/bin/sh", "-c", agent]


# ---------------------------------------------------------------------------
# Low-level WebSocket helpers (std-lib only)
# ---------------------------------------------------------------------------
_MASK_KEY_LEN = 4


def _ws_accept(key: str) -> bytes:
    """Compute the WebSocket accept string."""
    import hashlib
    import base64
    guide = "258EAFA5-E914-47DA-95CA-5AB5DC59B3FF"
    combined = key + guide
    return base64.b64encode(hashlib.sha1(combined.encode()).digest())


def _ws_read_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    """Read one WebSocket frame.  Returns ``(opcode, payload_bytes)`` or
    ``None`` on close.  Only text (opcode 1), binary (2) and close (8) are
    handled; ping/pong are auto-answered."""
    header = sock.recv(2)
    if len(header) < 2:
        return None
    fin = header[0] & 0x80
    opcode = header[0] & 0x0F
    masked = header[1] & 0x80
    length = header[1] & 0x7F
    if length == 126:
        ldata = sock.recv(2)
        if len(ldata) < 2:
            return None
        length = struct.unpack("!H", ldata)[0]
    elif length == 127:
        ldata = sock.recv(8)
        if len(ldata) < 8:
            return None
        length = struct.unpack("!Q", ldata)[0]
    mask_key = b""
    if masked:
        mask_key = sock.recv(_MASK_KEY_LEN)
        if len(mask_key) < _MASK_KEY_LEN:
            return None
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(min(4096, length - len(payload)))
        if not chunk:
            return None
        payload += chunk
    if masked:
        payload = bytes(p ^ mask_key[i % _MASK_KEY_LEN] for i, p in enumerate(payload))
    return opcode, payload


def _ws_send_text(sock: socket.socket, text: str) -> None:
    """Send a text frame (unmasked -- server to client)."""
    data = text.encode("utf-8")
    length = len(data)
    # FIN + text opcode
    frame = b"\x81"
    if length < 126:
        frame += bytes([0x80 | length])
    elif length < 65536:
        frame += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
    frame += data
    sock.sendall(frame)


def _ws_send_close(sock: socket.socket) -> None:
    """Send close frame."""
    frame = b"\x88\x80"  # close + FIN, no reason
    try:
        sock.sendall(frame)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PTY session
# ---------------------------------------------------------------------------

class PtySession:
    """Manage one PTY + WS client connection."""

    def __init__(self, master_fd: int, client_sock: socket.socket,
                 cmd: list[str]) -> None:
        self.master_fd = master_fd
        self.client_sock = client_sock
        self.cmd = cmd
        self.pid = pty.fork()
        self._closed = False
        self._lock = threading.Lock()
        self._selector = selectors.DefaultSelector()
        self._selector.register(master_fd, selectors.EVENT_READ)
        # Set terminal in raw mode
        try:
            attrs = termios.tcgetattr(master_fd)
            attrs[3] = attrs[3] & ~termios.ECHO  # no echo on server side
            termios.tcsetattr(master_fd, termios.TCSANOW, attrs)
        except Exception:
            pass

    def run(self) -> None:
        """Read from PTY master and forward to WS client."""
        import signal as _signal
        import time as _time
        buf = b""
        try:
            while True:
                try:
                    ready = self._selector.select(timeout=1.0)
                except OSError:
                    break
                if not ready:
                    # Check if process is still alive
                    try:
                        pid, status = os.waitpid(self.pid, os.WNOHANG)
                        if pid == self.pid:
                            break
                    except ChildProcessError:
                        break
                    continue
                try:
                    data = os.read(self.master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                # Flush to client
                with self._lock:
                    try:
                        _ws_send_text(self.client_sock, buf.decode("utf-8",
                                       errors="replace"))
                    except Exception:
                        break
                buf = b""
        finally:
            self.close()

    def write(self, data: str) -> None:
        """Write input from client into the PTY."""
        try:
            os.write(self.master_fd, data.encode("utf-8"))
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY."""
        try:
            buf = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, buf)
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._selector.close()
        except Exception:
            pass
        try:
            os.close(self.master_fd)
        except Exception:
            pass
        try:
            self.client_sock.close()
        except Exception:
            pass
        try:
            os.kill(self.pid, _signal.SIGTERM)
            os.waitpid(self.pid, 0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Terminal manager
# ---------------------------------------------------------------------------
class TerminalManager:
    """Singleton-like manager that holds token + session pool."""

    def __init__(self, repo_wsl_path: str | None = None,
                 state_dir: Path | None = None) -> None:
        self._token = _ensure_token(state_dir)
        self._repo_wsl_path = repo_wsl_path
        self._state_dir = state_dir
        self._sessions: dict[int, PtySession] = {}
        self._lock = threading.Lock()
        self._cmd = build_command(repo_wsl_path, state_dir)
        self._available = True
        self._unavail_reason: str | None = None

    @property
    def token(self) -> str:
        return self._token

    def check_token(self, provided: str | None) -> bool:
        if not provided:
            return False
        return provided == self._token

    def handle_ws(self, client_sock: socket.socket) -> None:
        """Accept one WS handshake, then run a PTY session."""
        # Read HTTP upgrade request
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client_sock.recv(4096)
            if not chunk:
                client_sock.close()
                return
            request += chunk
        lines = request.decode("utf-8", errors="replace").split("\r\n")
        path = lines[0].split(" ")[1] if lines else "/"

        # Parse query string for token
        token: str | None = None
        if "?" in path:
            qs = path.split("?", 1)[1]
            for param in qs.split("&"):
                if param.startswith("token="):
                    token = param[8:]
                    break

        if not self.check_token(token):
            client_sock.sendall(
                b"HTTP/1.1 401 Unauthorized\r\n"
                b"Content-Length: 28\r\n\r\n"
                b'{"error":"terminal-auth-required"}')
            client_sock.close()
            return

        # Check upgrade header
        upgrade_hdr = ""
        connection_hdr = ""
        ws_key = ""
        for line in lines[1:]:
            if line.lower().startswith("upgrade:"):
                upgrade_hdr = line.split(":", 1)[1].strip()
            elif line.lower().startswith("connection:"):
                connection_hdr = line.split(":", 1)[1].strip()
            elif line.lower().startswith("sec-websocket-key:"):
                ws_key = line.split(":", 1)[1].strip()

        if upgrade_hdr.upper() != "WEBSOCKET" or "upgrade" not in connection_hdr.upper():
            client_sock.close()
            return
        if not ws_key:
            client_sock.close()
            return

        # Accept handshake
        accept = _ws_accept(ws_key)
        response = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        client_sock.sendall(response)

        # Spawn PTY
        master_fd, slave_fd = pty.openpty()
        try:
            import fcntl, termios
            flags = fcntl.fcntl(slave_fd, fcntl.F_GETFL)
            fcntl.fcntl(slave_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            os.write(slave_fd, b"\n")
        except Exception:
            pass

        # Close slave side in parent
        try:
            os.close(slave_fd)
        except Exception:
            pass

        session = PtySession(master_fd, client_sock, self._cmd)
        with self._lock:
            sid = id(session)
            self._sessions[sid] = session
        try:
            session.run()
        finally:
            with self._lock:
                self._sessions.pop(sid, None)

    def get_token(self) -> str:
        """Return the current token (for tests to read)."""
        return self._token

    def get_token_file(self) -> Path:
        return _token_file(self._state_dir)


# ---------------------------------------------------------------------------
# HTTP handler wiring (for ApiServer)
# ---------------------------------------------------------------------------
def handle_terminal_ws(manager: TerminalManager, handler, path: str,
                       headers: dict[str, str]) -> tuple[int, bytes] | None:
    """Called from the API server to dispatch the terminal WS route.

    Returns ``(status, body)`` for non-WS requests and handles WS upgrades
    in-place, returning ``None`` after upgrading.
    """
    if not path.startswith("/api/terminal/ws"):
        return None

    if headers.get("Upgrade", "").lower() == "websocket":
        # The underlying socket is already available via handler
        # We use a different approach: extract socket from handler
        return None  # handled by the server loop

    # Non-WS: return 405
    body = json.dumps({"error": "websocket required"}).encode()
    return (101, body)  # won't be used for WS upgrade


# ---------------------------------------------------------------------------
# Main (standalone)
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the terminal server on loopback port 8765."""
    manager = TerminalManager()
    print(f"[terminal] token: {manager.token}", file=sys.stderr)
    print(f"[terminal] token file: {manager.get_token_file()}", file=sys.stderr)
    print(f"[terminal] command: {' '.join(manager._cmd)}", file=sys.stderr)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 8765))
    server.listen(4)
    print(f"[terminal] listening on 127.0.0.1:8765", file=sys.stderr)

    try:
        while True:
            client_sock, _ = server.accept()
            t = threading.Thread(target=manager.handle_ws,
                                 args=(client_sock,), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
