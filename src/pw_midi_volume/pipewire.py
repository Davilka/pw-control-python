"""Small PipeWire/WirePlumber adapter built on pw-dump and wpctl."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

Runner = Callable[[Sequence[str]], str]


class PipeWireError(RuntimeError):
    """Raised when a PipeWire command fails."""


class SinkNotFoundError(PipeWireError):
    """Raised when a configured sink cannot be found."""


@dataclass(frozen=True, slots=True)
class Sink:
    """A PipeWire audio sink."""

    id: int
    name: str
    description: str
    state: str | None = None

    @property
    def display_name(self) -> str:
        if self.description and self.description != self.name:
            return f"{self.description} — {self.name}"
        return self.name


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
        raise PipeWireError(
            f"command not found: {args[0]}. Is PipeWire/WirePlumber installed "
            "and running?"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipeWireError(f"command timed out: {' '.join(args)}") from exc
    except OSError as exc:
        raise PipeWireError(f"cannot run {' '.join(args)}: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PipeWireError(
            f"{' '.join(args)} failed with exit code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def parse_pw_dump(raw: str) -> list[Sink]:
    """Extract audio sinks from the JSON emitted by ``pw-dump``."""

    try:
        objects = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipeWireError(f"pw-dump returned invalid JSON: {exc}") from exc

    if not isinstance(objects, list):
        raise PipeWireError("pw-dump returned an unexpected JSON document")

    sinks: list[Sink] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "PipeWire:Interface:Node":
            continue

        info = obj.get("info")
        if not isinstance(info, dict):
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("media.class") != "Audio/Sink":
            continue

        node_id = obj.get("id")
        name = props.get("node.name")
        if not isinstance(node_id, int) or not isinstance(name, str) or not name:
            continue

        description = (
            props.get("node.description")
            or props.get("node.nick")
            or props.get("object.description")
            or name
        )
        state = info.get("state")
        sinks.append(
            Sink(
                id=node_id,
                name=name,
                description=str(description),
                state=str(state) if state is not None else None,
            )
        )

    sinks.sort(key=lambda item: (item.description.casefold(), item.name.casefold()))
    return sinks


def list_sinks(timeout: float = 5.0) -> list[Sink]:
    """Ask PipeWire for the current list of audio sinks."""

    return parse_pw_dump(_run(["pw-dump"], timeout=timeout))


class PipeWireController:
    """Resolve sinks by stable name and set their volume.

    ``pw-dump`` is cached briefly because a moving MIDI fader can generate many
    events per second. If a sink disappears, the cache is refreshed once before
    reporting an error.
    """

    def __init__(
        self,
        *,
        cache_ttl: float = 3.0,
        runner: Runner = _run,
    ) -> None:
        self._cache_ttl = max(0.0, cache_ttl)
        self._runner = runner
        self._lock = threading.RLock()
        self._sinks: list[Sink] = []
        self._fetched_at: float | None = None

    def _fetch_locked(self) -> None:
        raw = self._runner(["pw-dump"])
        self._sinks = parse_pw_dump(raw)
        self._fetched_at = time.monotonic()

    def invalidate(self) -> None:
        with self._lock:
            self._fetched_at = None

    def list_sinks(self, *, force: bool = False) -> list[Sink]:
        with self._lock:
            fresh = (
                self._fetched_at is not None
                and time.monotonic() - self._fetched_at < self._cache_ttl
            )
            if not force and fresh:
                return list(self._sinks)

            try:
                self._fetch_locked()
            except PipeWireError as exc:
                if self._sinks and not force:
                    LOG.warning("cannot refresh sink list, using cached data: %s", exc)
                    return list(self._sinks)
                raise
            return list(self._sinks)

    @staticmethod
    def _find(sinks: Sequence[Sink], key: str) -> Sink | None:
        key = key.strip()
        if not key:
            return None

        if key.isdecimal():
            numeric = int(key)
            for sink in sinks:
                if sink.id == numeric:
                    return sink

        for sink in sinks:
            if sink.name == key:
                return sink
        for sink in sinks:
            if sink.name.casefold() == key.casefold():
                return sink
        for sink in sinks:
            if sink.description == key:
                return sink
        for sink in sinks:
            if sink.description.casefold() == key.casefold():
                return sink
        return None

    def resolve(self, sink_key: str, *, force: bool = False) -> Sink | None:
        with self._lock:
            sinks = self.list_sinks(force=force)
            return self._find(sinks, sink_key)

    def set_volume(self, sink_key: str, volume: float) -> Sink:
        """Set all channels of a sink to ``volume`` (0.0 .. 1.0)."""

        value = max(0.0, min(1.0, float(volume)))
        last_error: PipeWireError | None = None

        for attempt in range(2):
            with self._lock:
                sink = self.resolve(sink_key, force=attempt > 0)
                if sink is None:
                    last_error = SinkNotFoundError(f"sink not found: {sink_key!r}")
                    continue

                args = [
                    "wpctl",
                    "set-volume",
                    "--limit",
                    "1.0",
                    str(sink.id),
                    f"{value:.6f}",
                ]
                try:
                    self._runner(args)
                except PipeWireError as exc:
                    last_error = exc
                    self.invalidate()
                    continue

                LOG.debug(
                    "set sink %s (%s) volume to %.1f%%",
                    sink.name,
                    sink.id,
                    value * 100.0,
                )
                return sink

        assert last_error is not None
        raise last_error
