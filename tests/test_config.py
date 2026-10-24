from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pw_midi_volume.config import (
    AppConfig,
    Binding,
    ConfigError,
    ConfigWatcher,
    MidiConfig,
    ensure_config,
    load_config,
    loads_config,
    save_config,
)


class ConfigParsingTests(unittest.TestCase):
    def test_minimal_config(self) -> None:
        config = loads_config("version: 1\n")
        self.assertEqual(config.bindings, ())
        self.assertIsNone(config.midi.port)

    def test_full_config(self) -> None:
        config = loads_config(
            """
version: 1
midi:
  port: "Launch Control"
bindings:
  - sink: "alsa_output.test"
    cc: 7
    channel: 0
    min_percent: 5
    max_percent: 90
    enabled: false
"""
        )
        self.assertEqual(config.midi.port, "Launch Control")
        self.assertEqual(len(config.bindings), 1)
        binding = config.bindings[0]
        self.assertEqual(binding.sink, "alsa_output.test")
        self.assertEqual(binding.cc, 7)
        self.assertEqual(binding.channel, 0)
        self.assertEqual(binding.min_percent, 5.0)
        self.assertEqual(binding.max_percent, 90.0)
        self.assertFalse(binding.enabled)

    def test_null_channel_and_port(self) -> None:
        config = loads_config(
            """
version: 1
midi:
  port: null
bindings:
  - sink: foo
    cc: 1
    channel: null
"""
        )
        self.assertIsNone(config.midi.port)
        self.assertIsNone(config.bindings[0].channel)

    def test_volume_mapping(self) -> None:
        binding = Binding(sink="foo", cc=1, min_percent=0, max_percent=100)
        self.assertEqual(binding.volume_fraction(0), 0.0)
        self.assertAlmostEqual(binding.volume_fraction(64), 64 / 127)
        self.assertEqual(binding.volume_fraction(127), 1.0)

        ranged = Binding(sink="foo", cc=1, min_percent=20, max_percent=80)
        self.assertAlmostEqual(ranged.volume_fraction(0), 0.2)
        self.assertAlmostEqual(ranged.volume_fraction(127), 0.8)

    def test_invalid_cc(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("bindings:\n  - sink: foo\n    cc: 128\n")
        with self.assertRaises(ConfigError):
            loads_config("bindings:\n  - sink: foo\n    cc: true\n")

    def test_invalid_channel(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("bindings:\n  - sink: foo\n    cc: 1\n    channel: 16\n")

    def test_invalid_range(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config(
                "bindings:\n  - sink: foo\n    cc: 1\n"
                "    min_percent: 90\n    max_percent: 10\n"
            )

    def test_invalid_version(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("version: 2\n")

    def test_invalid_yaml(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("bindings: [\n")


class ConfigFileTests(unittest.TestCase):
    def test_ensure_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.yaml"
            self.assertTrue(ensure_config(path))
            self.assertFalse(ensure_config(path))
            self.assertEqual(load_config(path).bindings, ())

            config = AppConfig(
                midi=MidiConfig(port="controller"),
                bindings=(
                    Binding(
                        sink="sink.name",
                        cc=11,
                        channel=2,
                        min_percent=10,
                        max_percent=95,
                    ),
                ),
            )
            save_config(path, config)
            loaded = load_config(path)
            self.assertEqual(loaded, config)
            self.assertIn("sink: sink.name", path.read_text(encoding="utf-8"))

    def test_missing_file_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ConfigError):
            load_config(Path(directory) / "missing.yaml")


class ConfigWatcherTests(unittest.TestCase):
    def test_reload_and_keep_last_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            watcher = ConfigWatcher(path)
            result = watcher.initial_load()
            self.assertTrue(result.changed)
            self.assertEqual(len(watcher.config.bindings), 0)

            good = AppConfig(bindings=(Binding(sink="first", cc=1),))
            save_config(path, good)
            result = watcher.poll()
            self.assertTrue(result.changed)
            self.assertEqual(watcher.config.bindings[0].sink, "first")

            # Invalid content must not replace the last valid configuration.
            path.write_text(
                "bindings:\n  - sink: second\n    cc: 999\n",
                encoding="utf-8",
            )
            result = watcher.poll()
            self.assertEqual(result.status, "error")
            self.assertEqual(watcher.config.bindings[0].sink, "first")

            # The same broken content is not reported again.
            self.assertEqual(watcher.poll().status, "unchanged")

            # Fixing the file applies it.
            second = AppConfig(bindings=(Binding(sink="second", cc=22),))
            save_config(path, second)
            result = watcher.poll()
            self.assertTrue(result.changed)
            self.assertEqual(watcher.config.bindings[0].sink, "second")

    def test_force_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            watcher = ConfigWatcher(path)
            watcher.initial_load()
            result = watcher.reload(force=True)
            self.assertTrue(result.changed)


if __name__ == "__main__":
    unittest.main()
