from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pw_midi_volume.config import (
    ACTION_MEDIA_NEXT,
    ACTION_MUTE,
    ACTION_MUTE_TOGGLE,
    ACTION_VOLUME,
    MEDIA_ACTIONS,
    MUTE_MODE_ANY,
    MUTE_MODE_PRESS,
    AppConfig,
    Binding,
    ConfigError,
    ConfigWatcher,
    MediaConfig,
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
        self.assertEqual(binding.action, ACTION_VOLUME)

    def test_media_binding_and_settings(self) -> None:
        config = loads_config(
            """
version: 1
media:
  player: spotify
  seek_seconds: 5
bindings:
  - action: media_next
    cc: 50
    channel: 0
  - action: media_rewind
    cc: 51
"""
        )
        self.assertEqual(config.media, MediaConfig(player="spotify", seek_seconds=5.0))
        self.assertEqual(config.bindings[0].action, ACTION_MEDIA_NEXT)
        self.assertEqual(config.bindings[0].sink, "")
        self.assertIn(config.bindings[0].action, MEDIA_ACTIONS)

    def test_media_binding_does_not_require_sink(self) -> None:
        config = loads_config("bindings:\n  - action: media_play_pause\n    cc: 1\n")
        self.assertEqual(config.bindings[0].sink, "")

    def test_invalid_media_seek(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("media:\n  seek_seconds: 0\n")
        with self.assertRaises(ConfigError):
            loads_config("media:\n  seek_seconds: 9999\n")

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

    def test_mute_actions(self) -> None:
        config = loads_config(
            """
version: 1
bindings:
  - sink: one
    cc: 20
    action: mute
  - sink: two
    cc: 21
    action: mute_toggle
    mute_mode: any
  - sink: three
    cc: 22
    action: mute_toggle
    mute_mode: press
"""
        )
        self.assertEqual(config.bindings[0].action, ACTION_MUTE)
        self.assertEqual(config.bindings[1].action, ACTION_MUTE_TOGGLE)
        self.assertEqual(config.bindings[1].mute_mode, MUTE_MODE_ANY)
        self.assertEqual(config.bindings[2].mute_mode, MUTE_MODE_PRESS)

    def test_mute_toggle_default_mode(self) -> None:
        config = loads_config(
            """
version: 1
bindings:
  - sink: foo
    cc: 1
    action: mute_toggle
"""
        )
        self.assertEqual(config.bindings[0].mute_mode, MUTE_MODE_PRESS)

    def test_invalid_mute_mode(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config(
                """
version: 1
bindings:
  - sink: foo
    cc: 1
    action: mute_toggle
    mute_mode: sometimes
"""
            )

    def test_invalid_action(self) -> None:
        with self.assertRaises(ConfigError):
            loads_config("bindings:\n  - sink: foo\n    cc: 1\n    action: explode\n")

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
            text = path.read_text(encoding="utf-8")
            self.assertIn("sink: sink.name", text)
            self.assertIn("action: volume", text)

    def test_mute_dump_omits_volume_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = AppConfig(
                bindings=(
                    Binding(
                        sink="sink.name",
                        cc=20,
                        action=ACTION_MUTE,
                    ),
                )
            )
            save_config(path, config)
            text = path.read_text(encoding="utf-8")
            self.assertIn("action: mute", text)
            self.assertNotIn("min_percent", text)
            self.assertNotIn("max_percent", text)
            self.assertEqual(load_config(path), config)

    def test_media_dump_omits_sink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = AppConfig(
                bindings=(
                    Binding(
                        sink="",
                        cc=50,
                        action=ACTION_MEDIA_NEXT,
                    ),
                )
            )
            save_config(path, config)
            text = path.read_text(encoding="utf-8")
            self.assertIn("action: media_next", text)
            self.assertNotIn("sink:", text)
            self.assertEqual(load_config(path), config)

    def test_toggle_dump_includes_mute_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = AppConfig(
                bindings=(
                    Binding(
                        sink="sink.name",
                        cc=21,
                        action=ACTION_MUTE_TOGGLE,
                        mute_mode=MUTE_MODE_ANY,
                    ),
                )
            )
            save_config(path, config)
            text = path.read_text(encoding="utf-8")
            self.assertIn("action: mute_toggle", text)
            self.assertIn("mute_mode: any", text)
            self.assertNotIn("min_percent", text)
            self.assertEqual(load_config(path), config)

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
