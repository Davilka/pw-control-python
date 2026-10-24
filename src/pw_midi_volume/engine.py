"""MIDI event dispatch and non-blocking volume updates."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .config import AppConfig
from .midi import port_matches
from .pipewire import PipeWireController, PipeWireError

LOG = logging.getLogger(__name__)


class BindingDispatcher:
    """Match incoming MIDI CC messages against the active configuration."""

    def __init__(
        self,
        get_config: Callable[[], AppConfig],
        submit_volume: Callable[[str, float], None],
    ) -> None:
        self._get_config = get_config
        self._submit_volume = submit_volume

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

            volume = binding.volume_fraction(value)
            self._submit_volume(binding.sink, volume)
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


class VolumeWorker(threading.Thread):
    """Apply the latest volume for each sink outside the MIDI callback thread.

    Subprocess calls are relatively slow, so incoming events are coalesced. This
    keeps fader movement smooth and prevents blocking the MIDI thread.
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
        self._pending: dict[str, float] = {}
        self._last_sent: dict[str, float] = {}
        self._last_error_at: dict[str, float] = {}
        self._stop_requested = False

    def submit(self, sink: str, volume: float) -> None:
        value = max(0.0, min(1.0, float(volume)))
        with self._condition:
            if self._stop_requested:
                return
            self._pending[sink] = value
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
        if self.is_alive():
            self.join(timeout=3.0)

    def _apply(self, sink: str, volume: float) -> None:
        previous = self._last_sent.get(sink)
        if previous is not None and abs(previous - volume) < self._min_change:
            return

        try:
            self._controller.set_volume(sink, volume)
        except PipeWireError as exc:
            now = time.monotonic()
            last = self._last_error_at.get(sink, 0.0)
            if now - last >= self._error_log_interval:
                LOG.warning("cannot set volume for sink %r: %s", sink, exc)
                self._last_error_at[sink] = now
            return

        self._last_sent[sink] = volume
        self._last_error_at.pop(sink, None)

    def run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop_requested:
                    self._condition.wait()
                if self._stop_requested and not self._pending:
                    return
                batch = self._pending
                self._pending = {}

            for sink, volume in batch.items():
                self._apply(sink, volume)
