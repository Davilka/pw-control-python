"""Configuration model, YAML serialisation and live file watching."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger(__name__)

CONFIG_VERSION = 1
MAX_PERCENT = 100.0

DEFAULT_CONFIG_TEXT = """\
# pw-midi-volume configuration
#
# Changes to this file are picked up automatically by the daemon (about 0.5 s).
# You can also send SIGHUP to the daemon to force a reload.
#
# Example:
#
#   bindings:
#     - sink: "alsa_output.pci-0000_00_1f.3.analog-stereo"
#       cc: 7
#       channel: 0          # 0-15, or null for all MIDI channels
#       min_percent: 0
#       max_percent: 100
#       enabled: true
#
# A sink is addressed by its PipeWire node.name. The GUI writes this field for
# you. Several bindings may target the same sink, and one CC may control
# several sinks at once.

version: 1
midi:
  # null = listen to every MIDI input, or a case-insensitive part of a port
  # name, for example "Launch Control".
  port: null
bindings: []
"""


class ConfigError(ValueError):
    """Raised when a configuration file cannot be parsed or is invalid."""


@dataclass(frozen=True, slots=True)
class MidiConfig:
    """Global MIDI input settings."""

    port: str | None = None


@dataclass(frozen=True, slots=True)
class Binding:
    """A single MIDI CC -> PipeWire sink binding."""

    sink: str
    cc: int
    channel: int | None = None
    min_percent: float = 0.0
    max_percent: float = 100.0
    enabled: bool = True

    def volume_fraction(self, midi_value: int) -> float:
        """Map a 0..127 MIDI value to a 0.0..1.0 PipeWire volume."""

        value = max(0, min(127, int(midi_value)))
        percent = self.min_percent + (self.max_percent - self.min_percent) * (
            value / 127.0
        )
        return max(0.0, min(1.0, percent / 100.0))


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    midi: MidiConfig = field(default_factory=MidiConfig)
    bindings: tuple[Binding, ...] = ()
    version: int = CONFIG_VERSION


@dataclass(frozen=True, slots=True)
class ReloadResult:
    """Outcome of a configuration poll."""

    status: str
    config: AppConfig | None = None
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.status == "reloaded"


def default_config_path() -> Path:
    """Return the per-user configuration path following the XDG spec."""

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "pw-midi-volume" / "config.yaml"


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} must be a mapping")
    return value


def _as_int(value: Any, what: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{what} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{what} must be between {minimum} and {maximum}")
    return value


def _as_percent(value: Any, what: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{what} must be a number")
    number = float(value)
    if not 0.0 <= number <= MAX_PERCENT:
        raise ConfigError(f"{what} must be between 0 and {MAX_PERCENT:g}")
    return number


def _parse_binding(raw: Any, index: int) -> Binding:
    what = f"bindings[{index}]"
    data = _mapping(raw, what)

    sink = data.get("sink")
    if not isinstance(sink, str) or not sink.strip():
        raise ConfigError(f"{what}.sink must be a non-empty string")

    if "cc" not in data:
        raise ConfigError(f"{what}.cc is required")
    cc = _as_int(data["cc"], f"{what}.cc", 0, 127)

    channel_raw = data.get("channel")
    channel: int | None
    if channel_raw is None:
        channel = None
    else:
        channel = _as_int(channel_raw, f"{what}.channel", 0, 15)

    min_percent = _as_percent(data.get("min_percent"), f"{what}.min_percent", 0.0)
    max_percent = _as_percent(
        data.get("max_percent"), f"{what}.max_percent", MAX_PERCENT
    )
    if min_percent > max_percent:
        raise ConfigError(f"{what}.min_percent must not be greater than max_percent")

    enabled_raw = data.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise ConfigError(f"{what}.enabled must be true or false")

    return Binding(
        sink=sink.strip(),
        cc=cc,
        channel=channel,
        min_percent=min_percent,
        max_percent=max_percent,
        enabled=enabled_raw,
    )


def loads_config(text: str, *, source: str | None = None) -> AppConfig:
    """Parse and validate configuration from a YAML string."""

    location = f" in {source}" if source else ""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML{location}: {exc}") from exc

    if raw is None:
        return AppConfig()
    data = _mapping(raw, "configuration root")

    version = data.get("version", CONFIG_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError(f"version{location} must be an integer")
    if version != CONFIG_VERSION:
        raise ConfigError(
            f"unsupported configuration version {version}{location}; "
            f"expected {CONFIG_VERSION}"
        )

    midi_raw = data.get("midi")
    midi_data = _mapping(midi_raw, "midi") if midi_raw is not None else {}
    port_raw = midi_data.get("port")
    if port_raw is None:
        port = None
    elif isinstance(port_raw, str):
        port = port_raw.strip() or None
    else:
        raise ConfigError("midi.port must be a string or null")

    bindings_raw = data.get("bindings", [])
    if bindings_raw is None:
        bindings_raw = []
    if not isinstance(bindings_raw, list):
        raise ConfigError("bindings must be a list")

    bindings = tuple(_parse_binding(item, i) for i, item in enumerate(bindings_raw))
    return AppConfig(
        midi=MidiConfig(port=port),
        bindings=bindings,
        version=version,
    )


def load_config(path: str | Path) -> AppConfig:
    """Read and validate a configuration file."""

    config_path = Path(path).expanduser()
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file does not exist: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc
    return loads_config(text, source=str(config_path))


def dump_config(config: AppConfig) -> str:
    """Serialise a configuration to canonical YAML text."""

    data: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "midi": {"port": config.midi.port},
        "bindings": [
            {
                "sink": binding.sink,
                "cc": binding.cc,
                "channel": binding.channel,
                "min_percent": binding.min_percent,
                "max_percent": binding.max_percent,
                "enabled": binding.enabled,
            }
            for binding in config.bindings
        ],
    }
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    header = (
        "# pw-midi-volume configuration\n"
        "# The daemon watches this file and reloads it automatically.\n"
        "# Bindings are addressed by PipeWire sink node.name.\n\n"
    )
    return header + body


def _atomic_write_text(config_path: Path, text: str) -> None:
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if config_path.exists():  # noqa: SIM108 - clearer than a ternary
                mode = config_path.stat().st_mode & 0o777
            else:
                mode = 0o644
            os.chmod(temporary, mode)
            os.replace(temporary, config_path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot write {config_path}: {exc}") from exc


def save_config(path: str | Path, config: AppConfig) -> None:
    """Atomically write configuration so readers never see a partial file."""

    config_path = Path(path).expanduser()
    _atomic_write_text(config_path, dump_config(config))


def ensure_config(path: str | Path) -> bool:
    """Create a documented empty configuration if none exists.

    Returns ``True`` when a new file was created.
    """

    config_path = Path(path).expanduser()
    if config_path.exists():
        return False
    _atomic_write_text(config_path, DEFAULT_CONFIG_TEXT)
    return True


class ConfigWatcher:
    """Poll a YAML file and keep the last valid configuration in memory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.config = AppConfig()
        self._digest: bytes | None = None
        self._failure_key: str | None = None
        self._lock = threading.Lock()

    def initial_load(self) -> ReloadResult:
        """Create the file if needed and perform the first load."""

        ensure_config(self.path)
        return self.reload(force=True)

    def _report_failure(self, key: str, message: str) -> None:
        if key != self._failure_key:
            LOG.error("%s", message)
            self._failure_key = key

    def reload(self, *, force: bool = False) -> ReloadResult:
        """Check for changes and apply a valid new configuration.

        Invalid YAML is reported once and the previously loaded configuration
        remains active. The caller can continue running safely.
        """

        with self._lock:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                message = f"configuration file disappeared: {self.path}"
                self._report_failure("missing", message)
                return ReloadResult("error", None, message)
            except OSError as exc:
                message = f"cannot read {self.path}: {exc}"
                self._report_failure(f"read:{exc}", message)
                return ReloadResult("error", None, message)

            digest = hashlib.sha256(raw).digest()
            if not force and digest == self._digest:
                return ReloadResult("unchanged", self.config)

            # Remember the digest even for invalid content. This prevents a
            # broken config from being logged on every poll.
            self._digest = digest

            try:
                text = raw.decode("utf-8")
                config = loads_config(text, source=str(self.path))
            except (UnicodeDecodeError, ConfigError) as exc:
                message = f"invalid configuration in {self.path}: {exc}"
                self._report_failure(f"invalid:{digest.hex()}", message)
                return ReloadResult("error", None, message)

            self.config = config
            self._failure_key = None
            LOG.info(
                "configuration loaded from %s: %d binding(s), MIDI input filter: %s",
                self.path,
                len(config.bindings),
                config.midi.port or "any",
            )
            return ReloadResult("reloaded", config)

    def poll(self) -> ReloadResult:
        """Non-forced reload check."""

        return self.reload(force=False)
