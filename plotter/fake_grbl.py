"""``FakeGrbl``: an in-memory GRBL 1.1 emulator that behaves like a serial port.

Used by the test suite and by ``plotter send --dry-run`` so the real streaming code runs
without hardware. It models what matters for the host protocol:

* the boot banner (and again after a soft reset ``0x18``),
* a 128-byte RX buffer — it **asserts** the host never has more than 127 unprocessed bytes,
* ``ok`` / ``error:N`` / ``ALARM:N`` responses (errors and alarms can be injected per line),
* real-time ``?`` status reports, ``!`` hold, ``~`` resume,
* ``$$``, ``$I``, ``$H``, ``$X``, ``$J=`` system commands.

Lines are processed lazily (one per ``readline`` when no output is pending), which keeps the
host's receive buffer full the way a busy machine would.
"""

from __future__ import annotations

import re
from collections import deque

from plotter.errors import GrblError

RX_BUFFER_BYTES = 128
MAX_UNPROCESSED = RX_BUFFER_BYTES - 1
_REALTIME = {ord("?"), ord("!"), ord("~"), 0x18}
_XY_RE = re.compile(r"([XY])\s*([-+]?\d*\.?\d+)")

DEFAULT_SETTINGS: dict[int, str] = {
    0: "10",
    1: "255",
    20: "1",
    21: "1",
    22: "1",
    23: "3",
    100: "80.000",
    101: "80.000",
    110: "5000.000",
    111: "5000.000",
    120: "500.000",
    121: "500.000",
    130: "230.000",
    131: "320.000",
}


class RxOverflowError(AssertionError):
    """The host sent more than GRBL's RX buffer can hold."""


class FakeGrbl:
    """Serial-port stand-in that emulates GRBL 1.1h."""

    def __init__(
        self,
        *,
        version: str = "1.1h",
        error_on_line: dict[int, int] | None = None,
        alarm_on_line: dict[int, int] | None = None,
        silent: bool = False,
        boot_alarm: bool = False,
    ) -> None:
        self.version = version
        self.error_on_line = dict(error_on_line or {})
        self.alarm_on_line = dict(alarm_on_line or {})
        self.silent = silent
        self.boot_alarm = boot_alarm
        self.timeout = 0.1
        self.is_open = True
        self.rx = bytearray()
        self.out: deque[bytes] = deque()
        self.received: list[str] = []
        self.realtime: list[str] = []
        self.max_unprocessed = 0
        self.gcode_lines = 0
        self.pos = [0.0, 0.0]
        self.state = "Idle"
        self.settings = dict(DEFAULT_SETTINGS)
        self._boot()

    # ------------------------------------------------------------------ serial API
    @property
    def in_waiting(self) -> int:
        """Bytes of output ready to read."""
        return sum(len(b) for b in self.out)

    def write(self, data: bytes) -> int:
        """Receive bytes from the host. Real-time bytes act immediately."""
        if not self.is_open:
            raise GrblError("port is closed")
        for b in data:
            if b in _REALTIME:
                self._realtime(b)
            else:
                self.rx.append(b)
        self.max_unprocessed = max(self.max_unprocessed, len(self.rx))
        if len(self.rx) > MAX_UNPROCESSED:
            raise RxOverflowError(
                f"host sent {len(self.rx)} unprocessed bytes; GRBL's limit is {MAX_UNPROCESSED}"
            )
        return len(data)

    def readline(self) -> bytes:
        """Return the next response line (processing one buffered line if needed)."""
        if not self.out:
            self._process_one()
        return self.out.popleft() if self.out else b""

    def reset_input_buffer(self) -> None:
        """Let GRBL catch up with everything received, then drop unread output."""
        while self._process_one():
            pass
        self.out.clear()

    def close(self) -> None:
        """Close the fake port."""
        self.is_open = False

    # ------------------------------------------------------------------ emulation
    def _emit(self, line: str) -> None:
        if not self.silent:
            self.out.append((line + "\r\n").encode("ascii"))

    def _boot(self) -> None:
        self.rx.clear()
        self._emit(f"Grbl {self.version} ['$' for help]")
        if self.boot_alarm:
            self.state = "Alarm"
            self._emit("[MSG:'$H'|'$X' to unlock]")

    def _realtime(self, b: int) -> None:
        ch = chr(b)
        self.realtime.append(ch)
        if ch == "?":
            x, y = self.pos
            self._emit(f"<{self.state}|MPos:{x:.3f},{y:.3f},0.000|FS:0,0>")
        elif ch == "!" and self.state in ("Idle", "Run"):
            self.state = "Hold:0"
        elif ch == "~" and self.state.startswith("Hold"):
            self.state = "Idle"
        elif b == 0x18:
            self.out.clear()
            self.state = "Idle"
            self._boot()

    def _process_one(self) -> bool:
        """Consume one line from the RX buffer. Returns False if none is complete."""
        for i, b in enumerate(self.rx):
            if b in (0x0A, 0x0D):
                raw = bytes(self.rx[:i]).decode("ascii", "replace").strip()
                del self.rx[: i + 1]
                self._execute(raw)
                return True
        return False

    def _execute(self, line: str) -> None:
        if not line:
            self._emit("ok")
            return
        self.received.append(line)
        if line.startswith("$"):
            self._system(line)
            return
        if self.state == "Alarm":
            self._emit("error:9")
            return
        self.gcode_lines += 1
        n = self.gcode_lines
        if n in self.error_on_line:
            self._emit(f"error:{self.error_on_line[n]}")
            return
        if n in self.alarm_on_line:
            self.state = "Alarm"
            self._emit(f"ALARM:{self.alarm_on_line[n]}")
            return
        for axis, val in _XY_RE.findall(line.upper()):
            self.pos["XY".index(axis)] = float(val)
        self._emit("ok")

    def _system(self, line: str) -> None:
        cmd = line.upper()
        if cmd == "$$":
            for k, v in sorted(self.settings.items()):
                self._emit(f"${k}={v}")
            self._emit("ok")
        elif cmd == "$I":
            self._emit(f"[VER:{self.version}.20190830:]")
            self._emit("[OPT:V,15,128]")
            self._emit("ok")
        elif cmd == "$H":
            self.state = "Idle"
            self.pos = [0.0, 0.0]
            self._emit("ok")
        elif cmd == "$X":
            self.state = "Idle"
            self._emit("[MSG:Caution: Unlocked]")
            self._emit("ok")
        elif cmd.startswith("$J="):
            if self.state == "Alarm":
                self._emit("error:9")
                return
            for axis, val in _XY_RE.findall(cmd):
                self.pos["XY".index(axis)] += float(val)
            self._emit("ok")
        elif cmd in ("$G", "$#", "$N"):
            self._emit("ok")
        else:
            self._emit("error:3")
