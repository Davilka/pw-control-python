"""MPRIS multimedia control through gdbus."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import (
    ACTION_MEDIA_FAST_FORWARD,
    ACTION_MEDIA_NEXT,
    ACTION_MEDIA_PAUSE,
    ACTION_MEDIA_PLAY,
    ACTION_MEDIA_PLAY_PAUSE,
    ACTION_MEDIA_PREVIOUS,
    ACTION_MEDIA_REWIND,
    ACTION_MEDIA_STOP,
    AppConfig,
    MediaConfig,
)

LOG = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"

Runner = Callable[[Sequence[str]], str]

_MEDIA_METHODS = {
    ACTION_MEDIA_PLAY_PAUSE: "PlayPause",
    ACTION_MEDIA_PLAY: "Play",
    ACTION_MEDIA_PAUSE: "Pause",
    ACTION_MEDIA_STOP: "Stop",
    ACTION_MEDIA_NEXT: "Next",
    ACTION_MEDIA_PREVIOUS: "Previous",
}


class MediaError(RuntimeError):
    """Raised when an MPRIS player cannot be found or controlled."""


@dataclass(frozen=True, slots=True)
class MediaPlayer:
    bus_name: str
    status: str = "Unknown"


def _run(args: Sequence[str], timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaError(
            "gdbus is not installed. Install GLib utilities to use multimedia controls."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"command timed out: {' '.join(args)}") from exc
    except OSError as exc:
        raise MediaError(f"cannot run {' '.join(args)}: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise MediaError(
            f"{' '.join(args)} failed with exit code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


class MediaController:
    """Discover MPRIS players and invoke standard player methods."""

    def __init__(self, *, runner: Runner = _run) -> None:
        self._runner = runner
        self._last_player: str | None = None

    def _call(
        self,
        bus_name: str,
        method: str,
        *arguments: object,
        path: str = MPRIS_PATH,
    ) -> str:
        command = [
            "gdbus",
            "call",
            "--session",
            "--dest",
            bus_name,
            "--object-path",
            path,
            "--method",
            method,
        ]
        if arguments:
            # The separator is required for negative numeric arguments such as
            # the relative seek offset used by Rewind.
            command.append("--")
            command.extend(str(argument) for argument in arguments)
        return self._runner(command)

    def list_players(self) -> list[str]:
        output = self._call(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus.ListNames",
            path="/org/freedesktop/DBus",
        )
        names = {
            name
            for name in re.findall(
                rf"{re.escape(MPRIS_PREFIX)}[A-Za-z0-9_.-]+",
                output,
            )
        }
        return sorted(names)

    def playback_status(self, bus_name: str) -> str:
        try:
            output = self._call(
                bus_name,
                "org.freedesktop.DBus.Properties.Get",
                PLAYER_INTERFACE,
                "PlaybackStatus",
            )
        except MediaError:
            return "Unknown"
        match = re.search(r"'([^']+)'", output)
        return match.group(1) if match else "Unknown"

    def select_player(self, player_filter: str | None = None) -> MediaPlayer:
        players = self.list_players()
        if player_filter:
            players = [
                name for name in players if player_filter.casefold() in name.casefold()
            ]
        if not players:
            suffix = f" matching {player_filter!r}" if player_filter else ""
            raise MediaError(f"no MPRIS media player found{suffix}")

        statuses = {name: self.playback_status(name) for name in players}
        if self._last_player in players:
            previous = self._last_player
            if statuses.get(previous) in {"Playing", "Paused"}:
                return MediaPlayer(previous, statuses[previous])

        for wanted_status in ("Playing", "Paused"):
            for name in players:
                if statuses.get(name) == wanted_status:
                    self._last_player = name
                    return MediaPlayer(name, wanted_status)

        name = players[0]
        self._last_player = name
        return MediaPlayer(name, statuses.get(name, "Unknown"))

    def perform(
        self,
        action: str,
        config: MediaConfig | None = None,
    ) -> MediaPlayer:
        if action not in _MEDIA_METHODS and action not in (
            ACTION_MEDIA_REWIND,
            ACTION_MEDIA_FAST_FORWARD,
        ):
            raise MediaError(f"unknown multimedia action: {action}")

        settings = config or MediaConfig()
        player = self.select_player(settings.player)

        if action in (ACTION_MEDIA_REWIND, ACTION_MEDIA_FAST_FORWARD):
            offset = int(settings.seek_seconds * 1_000_000)
            if action == ACTION_MEDIA_REWIND:
                offset = -offset
            self._call(
                player.bus_name,
                f"{PLAYER_INTERFACE}.Seek",
                offset,
            )
        else:
            method = _MEDIA_METHODS[action]
            self._call(player.bus_name, f"{PLAYER_INTERFACE}.{method}")

        LOG.info(
            "MPRIS %s -> %s",
            action,
            player.bus_name,
        )
        return player


class MediaWorker(threading.Thread):
    """Run MPRIS calls outside the MIDI callback thread."""

    def __init__(
        self,
        controller: MediaController,
        get_config: Callable[[], AppConfig],
    ) -> None:
        super().__init__(name="pw-midi-volume-media-worker", daemon=True)
        self._controller = controller
        self._get_config = get_config
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop_requested = False

    def submit(self, action: str) -> None:
        if not self._stop_requested:
            self._queue.put(action)

    def stop(self) -> None:
        self._stop_requested = True
        self._queue.put(None)
        if self.is_alive():
            self.join(timeout=3.0)

    def run(self) -> None:
        while True:
            action = self._queue.get()
            if action is None:
                return
            try:
                self._controller.perform(action, self._get_config().media)
            except MediaError as exc:
                LOG.warning("cannot perform multimedia action %s: %s", action, exc)
            except Exception:  # never kill the media worker
                LOG.exception("unexpected error in multimedia action %s", action)
