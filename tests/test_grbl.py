from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import numpy as np
import pytest

from plotter import gcode as G
from plotter.config import Config
from plotter.errors import GcodeError, GrblAlarm, GrblError
from plotter.fake_grbl import FakeGrbl, RxOverflowError
from plotter.grbl import MAX_IN_FLIGHT, Grbl, find_port, parse_status
from plotter.grbl_codes import alarm_message, error_message

Sleep = Callable[[float], None]


def _job(cfg: Config, n: int = 60) -> list[str]:
    t = np.linspace(0, 2 * np.pi, n)
    circle = np.column_stack([105 + 60 * np.cos(t), 150 + 60 * np.sin(t)])
    return G.to_gcode([circle, circle[::3] * 0.5 + 30], cfg)


def _connected(cfg: Config, fake: FakeGrbl, sleep: Sleep) -> Grbl:
    g = Grbl(cfg, "fake", serial=fake, sleep=sleep)
    g.connect()
    return g


def test_connect_reads_banner(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    assert g.banner.startswith("Grbl 1.1h")
    assert g.version == "1.1h"
    assert fake_grbl.received == []  # wake-up blank lines are not G-code
    assert not fake_grbl.out  # flushed


def test_connect_soft_resets_if_banner_missed(cfg: Config, no_sleep: Sleep) -> None:
    fake = FakeGrbl()
    fake.out.clear()  # banner was printed before we opened the port
    g = _connected(cfg, fake, no_sleep)
    assert g.banner.startswith("Grbl")
    assert "\x18" in fake.realtime


def test_connect_rejects_silence_and_old_versions(cfg: Config, no_sleep: Sleep) -> None:
    ticks = iter(range(0, 1000))
    g = Grbl(cfg, "fake", serial=FakeGrbl(silent=True), sleep=no_sleep, clock=lambda: next(ticks))
    with pytest.raises(GrblError, match="no GRBL banner"):
        g.connect()
    with pytest.raises(GrblError, match=r"expected GRBL 1\.1"):
        _connected(cfg, FakeGrbl(version="0.9j"), no_sleep)


def test_context_manager_closes(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    with Grbl(cfg, "fake", serial=fake_grbl, sleep=no_sleep) as g:
        assert g.version
    assert not fake_grbl.is_open
    with pytest.raises(GrblError, match="not connected"):
        g.status()


def test_stream_char_counting_never_exceeds_127(
    cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep
) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    lines = _job(cfg, 400)
    progress: list[tuple[int, int]] = []
    res = g.stream(lines, lambda a, t: progress.append((a, t)))
    sent = [line for line in lines if line]
    assert res.lines == len(sent)
    assert res.acked == len(sent)
    assert fake_grbl.received == sent  # every line, in order
    assert res.max_in_flight <= MAX_IN_FLIGHT
    assert fake_grbl.max_unprocessed <= 127  # FakeGrbl asserts this on every write too
    assert res.max_in_flight > 100  # the buffer is actually used, not one line at a time
    assert progress[-1] == (len(sent), len(sent))
    assert [a for a, _ in progress] == list(range(1, len(sent) + 1))


def test_fake_grbl_detects_overflow() -> None:
    fake = FakeGrbl()
    with pytest.raises(RxOverflowError):
        fake.write(b"G1 X1 Y1\n" * 20)


def test_stream_strips_comments(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    g.stream(["; header", "", "G21 (mm)", "G90 ; abs"])
    assert fake_grbl.received == ["G21", "G90"]


def test_error_stops_streaming_and_lifts_pen(cfg: Config, no_sleep: Sleep) -> None:
    fake = FakeGrbl(error_on_line={12: 20})
    g = _connected(cfg, fake, no_sleep)
    lines = _job(cfg, 400)
    with pytest.raises(GrblError, match=r"error:20 — Unsupported"):
        g.stream(lines)
    # the last thing GRBL received is the pen-up command + settle dwell
    assert fake.received[-2:] == [cfg.pen.up_cmd, "G4 P0.15"]
    job_part = fake.received[:-2]
    assert len(job_part) < len(lines) - 50  # streaming stopped early
    assert job_part == lines[: len(job_part)]


def test_alarm_stops_streaming(cfg: Config, no_sleep: Sleep) -> None:
    fake = FakeGrbl(alarm_on_line={8: 2})
    g = _connected(cfg, fake, no_sleep)
    with pytest.raises(GrblAlarm, match=r"ALARM:2.*soft limit.*unlock"):
        g.stream(_job(cfg))
    assert len(fake.received) < 30


def test_dry_run_touches_nothing(cfg: Config) -> None:
    g = Grbl(cfg, "fake")  # never connected
    seen: list[int] = []
    res = g.stream(_job(cfg), lambda a, t: seen.append(a), dry_run=True)
    assert res.dry_run
    assert seen[-1] == res.lines


def test_stream_rejects_bad_lines(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    with pytest.raises(GcodeError):
        g.stream(["G1 X" + "1" * 200])
    with pytest.raises(GcodeError):
        g.stream(["G1 X1 Y1 ; ok", "G1 Xé"])


def test_status_and_realtime(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    g.command("G0 X12.5 Y40")
    st = g.status()
    assert st.state == "Idle"
    assert st.mpos == (12.5, 40.0, 0.0)
    g.hold()
    assert g.status().state.startswith("Hold")
    g.resume()
    assert g.status().state == "Idle"
    g.abort()
    assert fake_grbl.realtime[-2:] == ["!", "\x18"]


def test_parse_status() -> None:
    st = parse_status("<Run|MPos:1.000,2.500,0.000|FS:500,0|WCO:0.000,0.000,0.000>")
    assert st.state == "Run"
    assert st.mpos == (1.0, 2.5, 0.0)
    assert st.wpos is None
    with pytest.raises(GrblError):
        parse_status("ok")


def test_commands(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    assert g.settings()[100] == "80.000"
    g.home()
    g.unlock()
    assert any("Unlocked" in m for m in g.messages)
    g.jog(10, -5, 1000)
    assert fake_grbl.received[-1] == "$J=G91 G21 X10 Y-5 F1000"
    g.pen(up=False)
    assert fake_grbl.received[-2:] == [cfg.pen.down_cmd, "G4 P0.15"]
    with pytest.raises(GrblError, match="error:3"):
        g.command("$BOGUS")
    with pytest.raises(GrblError, match="feed"):
        g.jog(1, 1, 0)


@pytest.mark.parametrize("cmd", ["$100=81", "$1 = 255", "$N0=G21", "$RST=*"])
def test_never_writes_settings(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep, cmd: str) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    with pytest.raises(GrblError, match="refusing"):
        g.command(cmd)
    assert cmd not in fake_grbl.received


def test_boot_alarm_blocks_gcode(cfg: Config, no_sleep: Sleep) -> None:
    fake = FakeGrbl(boot_alarm=True)
    g = _connected(cfg, fake, no_sleep)
    with pytest.raises(GrblError, match="error:9"):
        g.command("G0 X1 Y1")
    g.unlock()
    g.command("G0 X1 Y1")


def test_timeout(cfg: Config, no_sleep: Sleep) -> None:
    fake = FakeGrbl()
    ticks = iter(range(0, 10_000))
    g = Grbl(cfg, "fake", serial=fake, sleep=no_sleep, clock=lambda: float(next(ticks)))
    g.connect()
    fake.silent = True
    with pytest.raises(GrblError, match="did not answer"):
        g.command("G0 X1 Y1")


def test_unexpected_reset_detected(cfg: Config, fake_grbl: FakeGrbl, no_sleep: Sleep) -> None:
    g = _connected(cfg, fake_grbl, no_sleep)
    fake_grbl._emit("Grbl 1.1h ['$' for help]")
    with pytest.raises(GrblError, match="was reset"):
        g.command("G0 X1 Y1")


def test_find_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from serial.tools import list_ports

    ports = [
        SimpleNamespace(device="/dev/ttyS0", vid=None, description="ttyS0", manufacturer=None),
        SimpleNamespace(device="COM7", vid=0x1A86, description="USB-SERIAL CH340", manufacturer=""),
    ]
    monkeypatch.setattr(list_ports, "comports", lambda: ports)
    assert find_port() == "COM7"
    monkeypatch.setattr(list_ports, "comports", lambda: ports[:1])
    with pytest.raises(GrblError, match="no Arduino found"):
        find_port()


def test_codes() -> None:
    assert "Unsupported" in error_message(20)
    assert "unknown" in error_message(999)
    assert "Fix:" in alarm_message(1)
    assert "home" in alarm_message(9).lower()
    assert "unknown" in alarm_message(99)


def test_connect_opens_real_port_path(
    cfg: Config, no_sleep: Sleep, monkeypatch: pytest.MonkeyPatch
) -> None:
    import serial

    opened: list[tuple[str, int]] = []

    def fake_serial(port: str, baud: int, timeout: float) -> FakeGrbl:
        opened.append((port, baud))
        return FakeGrbl()

    monkeypatch.setattr(serial, "Serial", fake_serial)
    monkeypatch.setattr("plotter.grbl.find_port", lambda: "/dev/ttyACM9")
    g = Grbl(cfg, sleep=no_sleep)  # port "auto" from config
    g.connect()
    assert opened == [("/dev/ttyACM9", 115200)]
    assert g.port == "/dev/ttyACM9"

    def broken(port: str, baud: int, timeout: float) -> FakeGrbl:
        raise serial.SerialException("busy")

    monkeypatch.setattr(serial, "Serial", broken)
    with pytest.raises(GrblError, match="cannot open COM9"):
        Grbl(cfg, "COM9", sleep=no_sleep).connect()
