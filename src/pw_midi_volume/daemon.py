"""Background daemon connecting MIDI input to PipeWire sinks."""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from .config import ConfigError, ConfigWatcher, default_config_path
from .engine import BindingDispatcher, VolumeWorker
from .midi import MidiInputManager, MidiUnavailable
from .pipewire import PipeWireController

LOG = logging.getLogger(__name__)


class Daemon:
    """Run the MIDI listener until SIGTERM/SIGINT is received."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        poll_interval: float = 0.5,
        midi_scan_interval: float = 2.0,
    ) -> None:
        self.config_path = Path(config_path or default_config_path()).expanduser()
        self.poll_interval = max(0.1, poll_interval)
        self.midi_scan_interval = max(0.5, midi_scan_interval)

        self._stop_event = threading.Event()
        self._reload_event = threading.Event()
        self._last_scan = 0.0
        self._last_scan_error = 0.0

        self.watcher = ConfigWatcher(self.config_path)
        self.controller = PipeWireController()
        self.worker = VolumeWorker(self.controller)
        self.dispatcher = BindingDispatcher(
            lambda: self.watcher.config,
            self.worker.submit,
        )
        self.midi = MidiInputManager(self.dispatcher.on_cc)

    def _install_signal_handlers(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: self._stop_event.set())
        if hasattr(signal, "SIGHUP"):
            signal.signal(
                signal.SIGHUP,
                lambda _signum, _frame: self._reload_event.set(),
            )

    def _log_config(self, event: str) -> None:
        config = self.watcher.config
        LOG.info(
            "%s: %d binding(s), MIDI input filter: %s",
            event,
            len(config.bindings),
            config.midi.port or "any",
        )

    def _poll_config(self) -> None:
        force = self._reload_event.is_set()
        self._reload_event.clear()
        if force:
            LOG.info("configuration reload requested by SIGHUP")

        result = self.watcher.poll() if not force else self.watcher.reload(force=True)
        if result.changed:
            self._log_config("configuration reloaded")
        elif result.status == "error" and self.watcher.config.bindings:
            LOG.warning("keeping previous configuration until the file is fixed")

    def _scan_midi(self) -> None:
        now = time.monotonic()
        if now - self._last_scan < self.midi_scan_interval:
            return
        self._last_scan = now
        try:
            self.midi.scan()
        except MidiUnavailable as exc:
            if now - self._last_scan_error >= 30.0:
                LOG.warning("MIDI device scan failed: %s", exc)
                self._last_scan_error = now

    def run(self) -> None:
        """Start all components and block until shutdown."""

        self._install_signal_handlers()

        try:
            self.watcher.initial_load()
        except ConfigError as exc:
            raise ConfigError(
                f"cannot initialise configuration at {self.config_path}: {exc}"
            ) from exc

        if self.watcher.config.bindings:
            self._log_config("starting with configuration")
        else:
            LOG.info(
                "no bindings configured yet; edit %s (the file is watched live)",
                self.config_path,
            )

        self.worker.start()
        try:
            self.midi.start()
        except MidiUnavailable:
            self.worker.stop()
            raise

        LOG.info(
            "daemon started (config: %s, MIDI inputs: %s)",
            self.config_path,
            ", ".join(self.midi.open_port_names) or "none",
        )

        self._last_scan = time.monotonic()
        try:
            while not self._stop_event.wait(self.poll_interval):
                self._poll_config()
                self._scan_midi()
        finally:
            LOG.info("shutting down")
            self.midi.close()
            self.worker.stop()


def run_daemon(
    config_path: str | Path | None = None,
    *,
    poll_interval: float = 0.5,
) -> None:
    """Convenience entry point used by the CLI."""

    Daemon(config_path, poll_interval=poll_interval).run()
