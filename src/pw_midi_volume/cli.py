"""Command line interface for the pw-midi-volume daemon and helpers."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, default_config_path, load_config
from .daemon import run_daemon
from .midi import MidiUnavailable, list_input_ports
from .pipewire import PipeWireError, list_sinks


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pw-midi-volume",
        description=(
            "Control PipeWire sink volumes with MIDI CC controllers. "
            "Run without a command to start the daemon."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="logging verbosity (default: %(default)s)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="configuration polling interval (default: %(default)s)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("daemon", "check", "sinks", "midi-ports"),
        default="daemon",
        help="action to perform (default: daemon)",
    )
    return parser


def _print_config(path: Path) -> int:
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"config: {path}")
    print(f"MIDI input filter: {config.midi.port or 'any'}")
    print(f"bindings: {len(config.bindings)}")
    for index, binding in enumerate(config.bindings, start=1):
        channel = "all" if binding.channel is None else str(binding.channel + 1)
        state = "enabled" if binding.enabled else "disabled"
        print(
            f"  {index:2d}. CC {binding.cc:3d} ch {channel:>3} -> "
            f"{binding.sink} ({binding.min_percent:g}-"
            f"{binding.max_percent:g}%, {state})"
        )
    return 0


def _print_sinks() -> int:
    try:
        sinks = list_sinks()
    except PipeWireError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not sinks:
        print("No PipeWire audio sinks found.")
        return 0
    for sink in sinks:
        state = f" [{sink.state}]" if sink.state else ""
        print(f"{sink.id:5d}  {sink.name}  —  {sink.description}{state}")
    return 0


def _print_midi_ports() -> int:
    try:
        ports = list_input_ports()
    except MidiUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not ports:
        print("No MIDI input ports found.")
        return 0
    for name in ports:
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    args = _build_parser().parse_args(argv)

    if args.command == "check":
        return _print_config(args.config)
    if args.command == "sinks":
        return _print_sinks()
    if args.command == "midi-ports":
        return _print_midi_ports()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    log = logging.getLogger("pw_midi_volume")

    try:
        run_daemon(args.config, poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        return 0
    except (ConfigError, MidiUnavailable, PipeWireError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
