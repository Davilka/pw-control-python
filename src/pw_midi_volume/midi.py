"""MIDI input management with hot-plug support."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)


class MidiUnavailable(RuntimeError):
    """Raised when python-rtmidi or the MIDI subsystem is unavailable."""


@dataclass(frozen=True, slots=True)
class MidiEvent:
    """A raw MIDI message received from one input port."""

    port: str
    message: tuple[int, ...]
    timestamp: float


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _note_name(number: int) -> str:
    return f"{_NOTE_NAMES[number % 12]}{number // 12 - 1}"


def describe_midi_message(message: Sequence[int]) -> str:
    """Return a compact human-readable description of a MIDI message."""

    if not message:
        return "Empty message"

    status = message[0]
    if status >= 0xF0:
        system_messages = {
            0xF0: "SysEx",
            0xF1: "MIDI Time Code",
            0xF2: "Song Position",
            0xF3: "Song Select",
            0xF6: "Tune Request",
            0xF7: "End of SysEx",
            0xF8: "Clock",
            0xFA: "Start",
            0xFB: "Continue",
            0xFC: "Stop",
            0xFE: "Active Sensing",
            0xFF: "Reset",
        }
        name = system_messages.get(status, f"System 0x{status:02X}")
        return f"{name} ({len(message)} byte(s))"

    message_type = status & 0xF0
    channel = (status & 0x0F) + 1

    if message_type == 0x80 and len(message) >= 3:
        note = message[1] & 0x7F
        velocity = message[2] & 0x7F
        return f"ch {channel:02d}  Note Off {_note_name(note)} velocity {velocity}"
    if message_type == 0x90 and len(message) >= 3:
        note = message[1] & 0x7F
        velocity = message[2] & 0x7F
        if velocity == 0:
            return f"ch {channel:02d}  Note Off {_note_name(note)} velocity 0"
        return f"ch {channel:02d}  Note On {_note_name(note)} velocity {velocity}"
    if message_type == 0xA0 and len(message) >= 3:
        note = message[1] & 0x7F
        pressure = message[2] & 0x7F
        return f"ch {channel:02d}  Poly Pressure {_note_name(note)} = {pressure}"
    if message_type == 0xB0 and len(message) >= 3:
        cc = message[1] & 0x7F
        value = message[2] & 0x7F
        return f"ch {channel:02d}  CC {cc} = {value}"
    if message_type == 0xC0 and len(message) >= 2:
        return f"ch {channel:02d}  Program Change {message[1] & 0x7F}"
    if message_type == 0xD0 and len(message) >= 2:
        return f"ch {channel:02d}  Channel Pressure {message[1] & 0x7F}"
    if message_type == 0xE0 and len(message) >= 3:
        bend = (message[1] & 0x7F) | ((message[2] & 0x7F) << 7)
        return f"ch {channel:02d}  Pitch Bend {bend - 8192:+d}"

    raw = " ".join(f"{byte:02X}" for byte in message)
    return f"Raw MIDI: {raw}"


def port_matches(port_filter: str | None, port_name: str) -> bool:
    """Match the configured input filter against a MIDI port name."""

    if not port_filter:
        return True
    return port_filter.casefold() in port_name.casefold()


def _import_rtmidi() -> Any:
    try:
        import rtmidi
    except Exception as exc:  # import can fail due to system libraries
        raise MidiUnavailable(
            "python-rtmidi is not available. Install it with "
            "`pip install python-rtmidi` (and ALSA development headers if "
            "your platform has no binary wheel)."
        ) from exc
    return rtmidi


def list_input_ports() -> list[str]:
    """Return the names of all available MIDI input ports."""

    rtmidi = _import_rtmidi()
    probe = None
    try:
        probe = rtmidi.MidiIn(name="pw-midi-volume-probe")
        return list(probe.get_ports())
    except MidiUnavailable:
        raise
    except Exception as exc:  # rtmidi raises backend-specific exception types
        raise MidiUnavailable(f"cannot enumerate MIDI inputs: {exc}") from exc
    finally:
        probe = None


def parse_cc(message: Sequence[int]) -> tuple[int, int, int] | None:
    """Parse a channel message and return ``(channel, cc, value)``.

    Only Control Change messages are accepted. Channel is zero-based here.
    """

    if len(message) < 3:
        return None
    status = message[0]
    if not 0xB0 <= status <= 0xBF:
        return None
    return status & 0x0F, message[1] & 0x7F, message[2] & 0x7F


@dataclass(slots=True)
class _OpenPort:
    index: int
    name: str
    occurrence: int
    midi: Any


class MidiInputManager:
    """Open all MIDI inputs and keep them in sync with device hot-plug events.

    One ALSA sequencer client is created per physical port. Multiple clients
    (for example this daemon and the GUI MIDI monitor) may subscribe to
    the same controller at the same time.
    """

    def __init__(
        self,
        on_cc: Callable[[str, int, int, int], None] | None = None,
        *,
        on_message: Callable[[str, tuple[int, ...]], None] | None = None,
    ) -> None:
        if on_cc is None and on_message is None:
            raise ValueError("on_cc or on_message callback is required")
        self._on_cc = on_cc
        self._on_message = on_message
        self._rtmidi: Any | None = None
        self._probe: Any | None = None
        self._opened: dict[tuple[str, int], _OpenPort] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._rtmidi is not None:
                return
            self._rtmidi = _import_rtmidi()
            try:
                self._probe = self._rtmidi.MidiIn(name="pw-midi-volume-probe")
            except Exception as exc:
                self._rtmidi = None
                raise MidiUnavailable(
                    f"cannot initialise the MIDI backend: {exc}"
                ) from exc
            self.scan()

    @staticmethod
    def _desired_ports(ports: Sequence[str]) -> dict[tuple[str, int], int]:
        desired: dict[tuple[str, int], int] = {}
        occurrences: dict[str, int] = {}
        for index, name in enumerate(ports):
            occurrence = occurrences.get(name, 0)
            occurrences[name] = occurrence + 1
            desired[(name, occurrence)] = index
        return desired

    def _close_port(self, entry: _OpenPort) -> None:
        try:
            entry.midi.close_port()
        except Exception as exc:
            LOG.debug("error while closing MIDI port %s: %s", entry.name, exc)

    def scan(self) -> None:
        """Discover new inputs and drop disconnected ones."""

        with self._lock:
            if self._probe is None:
                return
            try:
                ports = list(self._probe.get_ports())
            except Exception as exc:
                raise MidiUnavailable(f"cannot enumerate MIDI inputs: {exc}") from exc

            desired = self._desired_ports(ports)

            for key, entry in list(self._opened.items()):
                if key not in desired or desired[key] != entry.index:
                    self._close_port(entry)
                    del self._opened[key]

            assert self._rtmidi is not None
            for key, index in desired.items():
                if key in self._opened:
                    continue
                name, occurrence = key
                midi = None
                try:
                    midi = self._rtmidi.MidiIn(name="pw-midi-volume")
                    midi.ignore_types(sysex=True, timing=True, active_sense=True)
                    midi.set_callback(
                        lambda event, _data=None, port_name=name: self._dispatch(
                            port_name, event
                        )
                    )
                    midi.open_port(index, name="pw-midi-volume")
                except Exception as exc:
                    if midi is not None:
                        try:
                            midi.close_port()
                        except Exception as close_exc:
                            LOG.debug(
                                "cannot close failed MIDI input %s: %s",
                                name,
                                close_exc,
                            )
                    LOG.warning("cannot open MIDI input %s: %s", name, exc)
                    continue

                self._opened[key] = _OpenPort(
                    index=index,
                    name=name,
                    occurrence=occurrence,
                    midi=midi,
                )
                LOG.info("listening on MIDI input: %s", name)

    def _dispatch(self, port_name: str, event: Any) -> None:
        try:
            if not event:
                return
            message = tuple(event[0])
            if not message:
                return
            if self._on_message is not None:
                self._on_message(port_name, message)
            if self._on_cc is not None:
                parsed = parse_cc(message)
                if parsed is not None:
                    channel, cc, value = parsed
                    self._on_cc(port_name, channel, cc, value)
        except Exception:  # never kill the MIDI callback thread
            LOG.exception("error while handling MIDI message from %s", port_name)

    def close(self) -> None:
        with self._lock:
            for entry in self._opened.values():
                self._close_port(entry)
            self._opened.clear()
            self._probe = None
            self._rtmidi = None

    @property
    def open_port_names(self) -> list[str]:
        with self._lock:
            return sorted({entry.name for entry in self._opened.values()})


class MidiEventStream:
    """Continuously capture MIDI messages for the GUI monitor and MIDI Learn."""

    def __init__(self, port_filter: str | None = None) -> None:
        self._port_filter = port_filter
        self._events: queue.Queue[MidiEvent] = queue.Queue()
        self._manager: MidiInputManager | None = None

    def _on_message(self, port: str, message: tuple[int, ...]) -> None:
        if not port_matches(self._port_filter, port):
            return
        self._events.put(MidiEvent(port=port, message=message, timestamp=time.time()))

    def start(self) -> None:
        if self._manager is not None:
            return
        manager = MidiInputManager(on_message=self._on_message)
        try:
            manager.start()
        except Exception:
            manager.close()
            raise
        self._manager = manager

    def scan(self) -> None:
        if self._manager is not None:
            self._manager.scan()

    def poll(self) -> MidiEvent | None:
        try:
            return self._events.get_nowait()
        except queue.Empty:
            return None

    @property
    def open_port_names(self) -> list[str]:
        return self._manager.open_port_names if self._manager is not None else []

    def stop(self) -> None:
        manager, self._manager = self._manager, None
        if manager is not None:
            manager.close()
