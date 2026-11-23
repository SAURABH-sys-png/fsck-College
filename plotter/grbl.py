"""Serial link to GRBL 1.1 — the only module that talks to the serial port (CLAUDE.md §4).

Streaming uses GRBL's **character-counting** protocol: the bytes of every line sent but not yet
acknowledged are tracked and kept ≤ 127 (GRBL's RX buffer is 128 bytes); each ``ok`` frees
the oldest line. Real-time commands (``?``, ``!``, ``~``, ``0x18``) bypass the buffer.

Safety (CLAUDE.md §11): this module never writes ``$`` settings — :meth:`Grbl.command`
refuses them. On ``error:N`` streaming stops and the pen is lifted; on ``ALARM:N`` streaming
stops and :class:`~plotter.errors.GrblAlarm` explains how to recover.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from plotter.config import Config
from plotter.errors import GcodeError, GrblAlarm, GrblError
from plotter.gcode import fmt, strip_comment
from plotter.grbl_codes import alarm_message, error_message

logger = logging.getLogger(__name__)

RX_BUFFER_BYTES = 128
MAX_IN_FLIGHT = RX_BUFFER_BYTES - 1
ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86}  # Arduino LLC, Arduino SRL, WCH CH340 clones
HOME_TIMEOUT_S = 120.0
_BANNER_RE = re.compile(r"Grbl\s+(\d+\.\d+\w*)")
_STATUS_RE = re.compile(r"<([^|>]+)(.*)>")
_SETTING_WRITE_RE = re.compile(r"^\$(\d+|N\d*|RST)\s*=", re.IGNORECASE)

ProgressCallback = Callable[[int, int], None]


class SerialLike(Protocol):
    """The subset of ``serial.Serial`` used here (``FakeGrbl`` implements it too)."""

    @property
    def in_waiting(self) -> int:
        """Bytes ready to read."""
        ...

    def write(self, data: bytes, /) -> int | None:
        """Write bytes."""
        ...

    def readline(self) -> bytes:
        """Read one line (``b""`` on timeout)."""
        ...

    def reset_input_buffer(self) -> None:
        """Discard unread input."""
        ...

    def close(self) -> None:
        """Close the port."""
        ...


@dataclass(frozen=True)
class GrblStatus:
    """A parsed ``<State|MPos:x,y,z|…>`` report."""

    state: str
    mpos: tuple[float, ...] | None
    wpos: tuple[float, ...] | None
    raw: str


@dataclass(frozen=True)
class StreamResult:
    """Outcome of :meth:`Grbl.stream`."""

    lines: int
    acked: int
    max_in_flight: int
    seconds: float
    dry_run: bool


def find_port() -> str:
    """Return the serial port of the first Arduino-like device.

    Raises:
        GrblError: If none is found.
    """
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    for p in ports:
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if p.vid in ARDUINO_VIDS or "arduino" in desc or "ch340" in desc:
            logger.info("auto-detected %s (%s)", p.device, p.description)
            return str(p.device)
    seen = ", ".join(p.device for p in ports) or "none"
    raise GrblError(
        f"no Arduino found (ports: {seen}). Plug it in, install the CH340 driver if it is a "
        "clone, or pass --port (e.g. COM3, /dev/ttyACM0, /dev/cu.usbmodem1101)."
    )


def parse_status(line: str) -> GrblStatus:
    """Parse a GRBL 1.1 status report.

    Raises:
        GrblError: If ``line`` is not a status report.
    """
    m = _STATUS_RE.match(line.strip())
    if not m:
        raise GrblError(f"not a status report: {line!r}")
    fields = dict(f.split(":", 1) for f in m.group(2).strip("|").split("|") if ":" in f)

    def coords(key: str) -> tuple[float, ...] | None:
        return tuple(float(v) for v in fields[key].split(",")) if key in fields else None

    return GrblStatus(m.group(1), coords("MPos"), coords("WPos"), line.strip())


class Grbl:
    """A connection to GRBL, normally used as a context manager.

    Example::

        with Grbl(cfg) as g:
            g.stream(lines, progress_cb)
    """

    def __init__(
        self,
        cfg: Config,
        port: str | None = None,
        *,
        serial: SerialLike | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.port = port or cfg.machine.serial_port
        self._ser = serial
        self._sleep = sleep
        self._clock = clock
        self.banner = ""
        self.version = ""
        self.last_status: GrblStatus | None = None
        self.messages: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> Grbl:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> str:
        """Open the port, wake GRBL and check its banner.

        Sends two blank lines (CR LF CR LF), waits ``machine.connect_wait_s``, flushes, and
        reads the ``Grbl 1.1x`` banner (a soft reset re-prints it if it was missed).

        Returns:
            The banner line.

        Raises:
            GrblError: If the port cannot be opened or no GRBL 1.1 banner arrives.
        """
        if self._ser is None:
            import serial

            port = find_port() if self.port == "auto" else self.port
            try:
                self._ser = serial.Serial(port, self.cfg.machine.baud, timeout=0.1)
            except serial.SerialException as exc:
                raise GrblError(f"cannot open {port}: {exc}. Is another program using it?") from exc
            self.port = port
        ser = self._ser
        ser.write(b"\r\n\r\n")
        self._sleep(self.cfg.machine.connect_wait_s)
        banner = ""
        while ser.in_waiting and not banner:
            banner = self._match_banner(ser.readline())
        ser.reset_input_buffer()
        if not banner:
            ser.write(b"\x18")
            deadline = self._clock() + max(self.cfg.machine.connect_wait_s, 2.0)
            while not banner and self._clock() < deadline:
                banner = self._match_banner(ser.readline())
        if not banner:
            raise GrblError(
                f"no GRBL banner on {self.port}. Check the port and baud ({self.cfg.machine.baud})"
                " and that GRBL 1.1 is flashed (firmware/GRBL_SETUP.md)."
            )
        self.banner = banner
        m = _BANNER_RE.search(banner)
        self.version = m.group(1) if m else ""
        if not self.version.startswith("1.1"):
            raise GrblError(f"expected GRBL 1.1, found {banner!r}; flash GRBL 1.1")
        logger.info("connected to %s on %s", banner, self.port)
        return banner

    @staticmethod
    def _match_banner(raw: bytes) -> str:
        line = raw.decode("ascii", "replace").strip()
        return line if _BANNER_RE.search(line) else ""

    def close(self) -> None:
        """Close the serial port (does not move the machine)."""
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    @property
    def _port(self) -> SerialLike:
        if self._ser is None:
            raise GrblError("not connected; call connect() first")
        return self._ser

    # ------------------------------------------------------------------ low level
    def _readline(self, timeout: float) -> str:
        deadline = self._clock() + timeout
        while True:
            raw = self._port.readline()
            if raw:
                line = raw.decode("ascii", "replace").strip()
                if line:
                    return line
            elif self._clock() >= deadline:
                raise GrblError(
                    f"GRBL did not answer within {timeout:g} s; check the USB cable and power"
                )

    def _response(self, timeout: float) -> tuple[str, list[str]]:
        """Read until ``ok`` or ``error:N``; return it with the info lines seen before it."""
        info: list[str] = []
        while True:
            line = self._readline(timeout)
            if line == "ok" or line.startswith("error:"):
                return line, info
            if line.startswith("ALARM:"):
                code = int(line.split(":", 1)[1])
                raise GrblAlarm(f"ALARM:{code} — {alarm_message(code)}")
            if line.startswith("<"):
                self.last_status = parse_status(line)
            elif _BANNER_RE.search(line):
                raise GrblError("GRBL reset unexpectedly (banner received); position may be lost")
            else:
                if line.startswith("[MSG:"):
                    logger.info("GRBL %s", line)
                    self.messages.append(line)
                info.append(line)

    # ------------------------------------------------------------------ commands
    def command(self, line: str, timeout: float | None = None) -> list[str]:
        """Send one line and wait for ``ok``.

        Args:
            line: A G-code line or a read-only/system ``$`` command.
            timeout: Seconds to wait (default ``machine.response_timeout_s``).

        Returns:
            Informational lines GRBL printed before ``ok`` (e.g. ``$$`` output).

        Raises:
            GrblError: On ``error:N``, a timeout, or an attempt to write a ``$`` setting.
            GrblAlarm: On ``ALARM:N``.
        """
        text = strip_comment(line)
        if _SETTING_WRITE_RE.match(text):
            raise GrblError(
                "refusing to write GRBL settings automatically; apply them yourself from "
                "firmware/grbl_settings.txt (CLAUDE.md §11)"
            )
        self._port.write((text + "\n").encode("ascii"))
        resp, info = self._response(timeout or self.cfg.machine.response_timeout_s)
        if resp.startswith("error:"):
            code = int(resp.split(":", 1)[1])
            raise GrblError(f"{text!r} → error:{code} — {error_message(code)}")
        return info

    def stream(
        self,
        lines: Iterable[str],
        progress_cb: ProgressCallback | None = None,
        dry_run: bool = False,
    ) -> StreamResult:
        """Stream a job with the character-counting protocol.

        Comments and blank lines are stripped first. With ``dry_run=True`` nothing is
        written to the port; lines are checked and progress is reported.

        Args:
            lines: G-code lines (already validated with :func:`plotter.gcode.validate`).
            progress_cb: Called as ``progress_cb(acked, total)`` after each ``ok``.
            dry_run: Do not touch the serial port.

        Returns:
            Counts, the peak bytes in flight and the elapsed time.

        Raises:
            GrblError: On ``error:N`` (streaming stops and the pen is lifted) or a timeout.
            GrblAlarm: On ``ALARM:N`` (streaming stops).
            GcodeError: If a line is too long or not ASCII.
        """
        job = [t for t in (strip_comment(x) for x in lines) if t]
        for t in job:
            if len(t) + 1 > MAX_IN_FLIGHT or not t.isascii():
                raise GcodeError(f"cannot stream line {t[:40]!r}: too long or not ASCII")
        total = len(job)
        start = self._clock()
        if dry_run:
            for i in range(total):
                if progress_cb:
                    progress_cb(i + 1, total)
            return StreamResult(total, total, 0, 0.0, True)

        port = self._port
        timeout = self.cfg.machine.response_timeout_s
        in_flight: deque[tuple[int, int]] = deque()  # (job index, bytes)
        used = peak = acked = 0

        def ack() -> None:
            nonlocal used, acked
            resp, _ = self._response(timeout)
            idx, size = in_flight.popleft()
            used -= size
            if resp.startswith("error:"):
                code = int(resp.split(":", 1)[1])
                raise GrblError(
                    f"line {idx + 1} {job[idx]!r} → error:{code} — {error_message(code)}"
                )
            acked += 1
            if progress_cb:
                progress_cb(acked, total)

        try:
            for i, t in enumerate(job):
                data = (t + "\n").encode("ascii")
                while in_flight and used + len(data) > MAX_IN_FLIGHT:
                    ack()
                port.write(data)
                in_flight.append((i, len(data)))
                used += len(data)
                peak = max(peak, used)
            while in_flight:
                ack()
        except GrblAlarm:
            logger.error("alarm during streaming; stopped after %d/%d lines", acked, total)
            raise
        except GrblError:
            self._stop_after_error(in_flight, timeout)
            raise
        return StreamResult(total, acked, peak, self._clock() - start, False)

    def _stop_after_error(self, in_flight: deque[tuple[int, int]], timeout: float) -> None:
        """After error:N: stop sending, drain what GRBL already has, then lift the pen."""
        try:
            while in_flight:
                self._response(timeout)
                in_flight.popleft()
            self.pen(up=True)
            logger.warning("streaming stopped after an error; pen lifted")
        except GrblError as exc:  # the link itself failed; nothing more we can do safely
            logger.error("could not lift the pen after the error: %s", exc)

    # ------------------------------------------------------------------ real-time
    def status(self, timeout: float = 2.0) -> GrblStatus:
        """Send ``?`` and return the parsed status report."""
        self._port.write(b"?")
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            line = self._readline(timeout)
            if line.startswith("<"):
                self.last_status = parse_status(line)
                return self.last_status
        raise GrblError("no status report from GRBL")

    def hold(self) -> None:
        """Feed hold (``!``): decelerate and pause."""
        self._port.write(b"!")

    def resume(self) -> None:
        """Cycle start (``~``): resume after a hold."""
        self._port.write(b"~")

    def reset(self) -> None:
        """Soft reset (``0x18``)."""
        self._port.write(b"\x18")

    def abort(self) -> None:
        """Stop now: feed hold, let the machine decelerate, then soft reset."""
        self.hold()
        self._sleep(0.3)
        self.reset()
        logger.warning("job aborted (feed hold + soft reset)")

    # ------------------------------------------------------------------ helpers
    def home(self) -> list[str]:
        """Run the homing cycle (``$H``)."""
        return self.command("$H", timeout=HOME_TIMEOUT_S)

    def unlock(self) -> list[str]:
        """Clear an alarm without homing (``$X``)."""
        return self.command("$X")

    def settings(self) -> dict[int, str]:
        """Read (never write) the ``$$`` settings."""
        out: dict[int, str] = {}
        for line in self.command("$$"):
            m = re.match(r"^\$(\d+)=(\S+)", line)
            if m:
                out[int(m.group(1))] = m.group(2)
        return out

    def jog(self, dx: float, dy: float, feed: float) -> None:
        """Relative jog (``$J=G91 G21 X… Y… F…``); soft limits apply after homing."""
        if feed <= 0:
            raise GrblError("jog feed must be > 0")
        d = self.cfg.motion.coord_decimals
        self.command(f"$J=G91 G21 X{fmt(dx, d)} Y{fmt(dy, d)} F{fmt(feed, 0)}")

    def pen(self, up: bool) -> None:
        """Raise or lower the pen and wait for the servo to settle."""
        self.command(self.cfg.pen.up_cmd if up else self.cfg.pen.down_cmd)
        self.command(f"G4 P{fmt(self.cfg.pen.settle_s, 3)}")
