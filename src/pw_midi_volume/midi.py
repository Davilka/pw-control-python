"""MIDI input management with hot-plug support."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)


class MidiUnavailable(RuntimeError):
    """Raised when python-rtmidi or the MIDI subsystem is unavailable."""


@dataclass(frozen=True, slots=True)
class CapturedCc:
    port: str
    channel: int
    cc: int
    value: int


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
    (for example this daemon and the GUI's MIDI-learn helper) may subscribe to
    the same controller at the same time.
    """

    def __init__(
        self,
        on_cc: Callable[[str, int, int, int], None],
    ) -> None:
        self._on_cc = on_cc
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
            message = event[0]
            parsed = parse_cc(message)
            if parsed is None:
                return
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


class MidiSniffer:
    """Capture the next CC message for the GUI's MIDI-learn feature."""

    def __init__(self, port_filter: str | None = None) -> None:
        self._port_filter = port_filter
        self._captured: queue.Queue[CapturedCc] = queue.Queue()
        self._manager: MidiInputManager | None = None

    def _on_cc(self, port: str, channel: int, cc: int, value: int) -> None:
        if not port_matches(self._port_filter, port):
            return
        self._captured.put(CapturedCc(port=port, channel=channel, cc=cc, value=value))

    def start(self) -> None:
        if self._manager is not None:
            return
        manager = MidiInputManager(self._on_cc)
        try:
            manager.start()
        except Exception:
            manager.close()
            raise
        self._manager = manager

    def scan(self) -> None:
        if self._manager is not None:
            self._manager.scan()

    def poll(self) -> CapturedCc | None:
        try:
            return self._captured.get_nowait()
        except queue.Empty:
            return None

    @property
    def open_port_names(self) -> list[str]:
        return self._manager.open_port_names if self._manager is not None else []

    def stop(self) -> None:
        manager, self._manager = self._manager, None
        if manager is not None:
            manager.close()
