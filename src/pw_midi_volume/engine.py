"""MIDI event dispatch and non-blocking sink updates."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import (
    ACTION_MUTE,
    ACTION_MUTE_TOGGLE,
    ACTION_VOLUME,
    MEDIA_ACTIONS,
    MUTE_MODE_ANY,
    MUTE_MODE_PRESS,
    MUTE_THRESHOLD,
    AppConfig,
    Binding,
)
from .midi import port_matches
from .pipewire import PipeWireController, PipeWireError

LOG = logging.getLogger(__name__)

# A press-only MIDI button may emit a short burst of 127 messages with no 0.
# Treat messages within this window as one physical press.
BUTTON_PRESS_DEBOUNCE = 0.25


class BindingDispatcher:
    """Match incoming MIDI CC messages against the active configuration."""

    def __init__(
        self,
        get_config: Callable[[], AppConfig],
        submit_volume: Callable[[str, float], None],
        submit_mute: Callable[[str, bool], None] | None = None,
        submit_mute_toggle: Callable[[str], None] | None = None,
        submit_media: Callable[[str], None] | None = None,
    ) -> None:
        self._get_config = get_config
        self._submit_volume = submit_volume
        self._submit_mute = submit_mute or (lambda _sink, _muted: None)
        self._submit_mute_toggle = submit_mute_toggle or (lambda _sink: None)
        self._submit_media = submit_media or (lambda _action: None)
        self._toggle_pressed: dict[tuple[str, str, int, int | None], bool] = {}
        self._mute_press_state: dict[
            tuple[str, str, int, int | None], tuple[bool, float]
        ] = {}
        self._toggle_lock = threading.Lock()

    def _button_press_edge(
        self,
        key: tuple[str, str, int, int | None],
        value: int,
    ) -> bool:
        """Return True once per physical button press.

        Press-only controls may send a burst of 127 messages and no 0. The
        debounce window collapses that burst into one press, while the next
        press after a pause is accepted again. A 0 release resets the state
        immediately.
        """

        if value < MUTE_THRESHOLD:
            with self._toggle_lock:
                self._mute_press_state.pop(key, None)
            return False

        now = time.monotonic()
        with self._toggle_lock:
            was_pressed, last_high = self._mute_press_state.get(key, (False, 0.0))
            new_press = not was_pressed or now - last_high >= BUTTON_PRESS_DEBOUNCE
            self._mute_press_state[key] = (True, now)
        return new_press

    def _handle_mute_toggle(
        self,
        port_name: str,
        binding: Binding,
        channel: int,
        value: int,
    ) -> None:
        if binding.mute_mode == MUTE_MODE_ANY:
            # Latching controls send 127 on one press and 0 on the next.
            self._submit_mute_toggle(binding.sink)
            return

        if binding.mute_mode == MUTE_MODE_PRESS:
            # Momentary controls may send only 127 for each press (without a
            # release message), send 127 on press and 0 on release, or emit a
            # short burst of 127 messages for one press. A release resets the
            # state; otherwise a new 127 is accepted only after a short debounce
            # interval. This makes press-only buttons work while preventing a
            # single press from toggling twice.
            key = (port_name, binding.sink, binding.cc, binding.channel)
            if self._button_press_edge(key, value):
                LOG.info(
                    "MIDI mute toggle: %s ch %d CC %d value %d -> %s",
                    port_name,
                    channel + 1,
                    binding.cc,
                    value,
                    binding.sink,
                )
                self._submit_mute_toggle(binding.sink)
            return

        # Edge mode: toggle only on the transition from a low value to a high
        # value. This prevents repeated 127 messages from toggling repeatedly
        # while a button is held.
        key = (port_name, binding.sink, binding.cc, binding.channel)
        pressed = value >= MUTE_THRESHOLD
        with self._toggle_lock:
            was_pressed = self._toggle_pressed.get(key, False)
            self._toggle_pressed[key] = pressed
        if pressed and not was_pressed:
            self._submit_mute_toggle(binding.sink)

    def on_cc(
        self,
        port_name: str,
        channel: int,
        cc: int,
        value: int,
    ) -> int:
        config = self._get_config()
        if not port_matches(config.midi.port, port_name):
            return 0

        matched = 0
        for binding in config.bindings:
            if not binding.enabled:
                continue
            if binding.cc != cc:
                continue
            if binding.channel is not None and binding.channel != channel:
                continue

            if binding.action == ACTION_MUTE:
                self._submit_mute(binding.sink, value >= MUTE_THRESHOLD)
            elif binding.action == ACTION_MUTE_TOGGLE:
                self._handle_mute_toggle(port_name, binding, channel, value)
            elif binding.action == ACTION_VOLUME:
                self._submit_volume(binding.sink, binding.volume_fraction(value))
            elif binding.action in MEDIA_ACTIONS:
                key = (
                    port_name,
                    binding.action,
                    binding.cc,
                    binding.channel,
                )
                if self._button_press_edge(key, value):
                    LOG.info(
                        "MIDI multimedia: %s ch %d CC %d -> %s",
                        port_name,
                        channel + 1,
                        binding.cc,
                        binding.action,
                    )
                    self._submit_media(binding.action)
            else:  # pragma: no cover - configuration validation prevents this
                LOG.error("unknown binding action: %s", binding.action)
                continue
            matched += 1

        if matched:
            LOG.debug(
                "MIDI %s ch %d CC %d = %d matched %d binding(s)",
                port_name,
                channel + 1,
                cc,
                value,
                matched,
            )
        return matched


@dataclass(frozen=True, slots=True)
class _Command:
    kind: str
    sink: str
    volume: float | None = None
    muted: bool | None = None


class SinkWorker(threading.Thread):
    """Apply sink commands outside the MIDI callback thread.

    Subprocess calls are relatively slow, so state changes are coalesced. Mute
    toggles are kept as individual events: two quick button presses must toggle
    twice rather than collapse into one. Volume and absolute mute commands are
    idempotent, so only their latest value is required.
    """

    def __init__(
        self,
        controller: PipeWireController,
        *,
        min_change: float = 0.001,
        error_log_interval: float = 5.0,
    ) -> None:
        super().__init__(name="pw-midi-volume-worker", daemon=True)
        self._controller = controller
        self._min_change = min_change
        self._error_log_interval = error_log_interval
        self._condition = threading.Condition()
        self._pending: dict[tuple[str, str, object], _Command] = {}
        self._toggle_counter = 0
        self._last_volume: dict[str, float] = {}
        self._last_error_at: dict[tuple[str, str], float] = {}
        self._stop_requested = False

    def _put(self, key: tuple[str, str, object], command: _Command) -> None:
        with self._condition:
            if self._stop_requested:
                return
            self._pending.pop(key, None)
            self._pending[key] = command
            self._condition.notify()

    def submit_volume(self, sink: str, volume: float) -> None:
        value = max(0.0, min(1.0, float(volume)))
        self._put(
            (sink, "volume", None),
            _Command(kind="volume", sink=sink, volume=value),
        )

    # Kept as a convenient alias for callers that only control volume.
    submit = submit_volume

    def submit_mute(self, sink: str, muted: bool) -> None:
        self._put(
            (sink, "mute", None),
            _Command(kind="mute", sink=sink, muted=bool(muted)),
        )

    def submit_mute_toggle(self, sink: str) -> None:
        with self._condition:
            if self._stop_requested:
                return
            self._toggle_counter += 1
            key = (sink, "toggle", self._toggle_counter)
            self._pending[key] = _Command(kind="toggle", sink=sink)
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
        if self.is_alive():
            self.join(timeout=3.0)

    def _log_error(self, command: _Command, exc: PipeWireError) -> None:
        key = (command.sink, command.kind)
        now = time.monotonic()
        last = self._last_error_at.get(key, 0.0)
        if now - last >= self._error_log_interval:
            LOG.warning(
                "cannot apply %s action to sink %r: %s",
                command.kind,
                command.sink,
                exc,
            )
            self._last_error_at[key] = now

    def _apply(self, command: _Command) -> None:
        if command.kind == "volume":
            assert command.volume is not None
            previous = self._last_volume.get(command.sink)
            if (
                previous is not None
                and abs(previous - command.volume) < self._min_change
            ):
                return
            try:
                self._controller.set_volume(command.sink, command.volume)
            except PipeWireError as exc:
                self._log_error(command, exc)
                return
            self._last_volume[command.sink] = command.volume
            self._last_error_at.pop((command.sink, command.kind), None)
            return

        if command.kind == "mute":
            assert command.muted is not None
            # Absolute mute commands are always applied. The sink can be
            # muted/unmuted by another application between CC messages, so
            # caching the previous command would leave it out of sync.
            try:
                self._controller.set_mute(command.sink, command.muted)
            except PipeWireError as exc:
                self._log_error(command, exc)
                return
            self._last_error_at.pop((command.sink, command.kind), None)
            return

        if command.kind == "toggle":
            try:
                self._controller.toggle_mute(command.sink)
            except PipeWireError as exc:
                self._log_error(command, exc)
                return
            self._last_error_at.pop((command.sink, command.kind), None)
            return

        LOG.error("unknown sink command: %s", command.kind)

    def run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop_requested:
                    self._condition.wait()
                if self._stop_requested and not self._pending:
                    return
                batch = list(self._pending.values())
                self._pending.clear()

            for command in batch:
                self._apply(command)
