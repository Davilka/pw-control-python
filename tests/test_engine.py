from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from pw_midi_volume.config import (
    ACTION_MEDIA_NEXT,
    ACTION_MEDIA_PLAY_PAUSE,
    ACTION_MUTE,
    ACTION_MUTE_TOGGLE,
    MUTE_MODE_ANY,
    MUTE_MODE_EDGE,
    MUTE_MODE_PRESS,
    AppConfig,
    Binding,
    MidiConfig,
)
from pw_midi_volume.engine import BindingDispatcher, SinkWorker
from pw_midi_volume.midi import describe_midi_message, parse_cc, port_matches
from pw_midi_volume.pipewire import PipeWireError


class DispatcherTests(unittest.TestCase):
    def make_dispatcher(
        self, config: AppConfig
    ) -> tuple[BindingDispatcher, list[tuple[str, str, object]]]:
        calls: list[tuple[str, str, object]] = []
        dispatcher = BindingDispatcher(
            lambda: config,
            lambda sink, volume: calls.append(("volume", sink, volume)),
            lambda sink, muted: calls.append(("mute", sink, muted)),
            lambda sink: calls.append(("toggle", sink, None)),
            lambda action: calls.append(("media", "", action)),
        )
        return dispatcher, calls

    def volume_calls(
        self, calls: list[tuple[str, str, object]]
    ) -> list[tuple[str, float]]:
        return [(sink, value) for kind, sink, value in calls if kind == "volume"]

    def test_matches_cc_and_submits_volume(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(sink="speakers", cc=7, channel=0),
                Binding(sink="headphones", cc=7, channel=0),
                Binding(sink="other", cc=8),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        matched = dispatcher.on_cc("Controller", 0, 7, 64)
        self.assertEqual(matched, 2)
        volumes = self.volume_calls(calls)
        self.assertEqual(len(volumes), 2)
        self.assertEqual({sink for sink, _ in volumes}, {"speakers", "headphones"})
        self.assertTrue(all(abs(volume - 64 / 127) < 1e-6 for _, volume in volumes))

    def test_channel_filter_and_all_channels(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(sink="one", cc=1, channel=2),
                Binding(sink="two", cc=1, channel=None),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Controller", 2, 1, 127), 2)
        self.assertEqual(len(self.volume_calls(calls)), 2)

        calls.clear()
        self.assertEqual(dispatcher.on_cc("Controller", 3, 1, 127), 1)
        self.assertEqual(self.volume_calls(calls), [("two", 1.0)])

    def test_disabled_binding(self) -> None:
        config = AppConfig(bindings=(Binding(sink="speakers", cc=7, enabled=False),))
        dispatcher, calls = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Controller", 0, 7, 100), 0)
        self.assertEqual(calls, [])

    def test_port_filter(self) -> None:
        config = AppConfig(
            midi=MidiConfig(port="launch control"),
            bindings=(Binding(sink="speakers", cc=7),),
        )
        dispatcher, calls = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Launch Control MIDI 1", 0, 7, 1), 1)
        self.assertEqual(dispatcher.on_cc("Other Controller", 0, 7, 1), 0)
        self.assertEqual(len(self.volume_calls(calls)), 1)

    def test_range_mapping(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="speakers",
                    cc=7,
                    min_percent=20,
                    max_percent=80,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 7, 0)
        dispatcher.on_cc("Controller", 0, 7, 127)
        self.assertAlmostEqual(self.volume_calls(calls)[0][1], 0.2)
        self.assertAlmostEqual(self.volume_calls(calls)[1][1], 0.8)

    def test_absolute_mute_binding(self) -> None:
        config = AppConfig(
            bindings=(Binding(sink="speakers", cc=20, action=ACTION_MUTE),)
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 20, 0)
        dispatcher.on_cc("Controller", 0, 20, 63)
        dispatcher.on_cc("Controller", 0, 20, 64)
        dispatcher.on_cc("Controller", 0, 20, 127)
        self.assertEqual(
            calls,
            [
                ("mute", "speakers", False),
                ("mute", "speakers", False),
                ("mute", "speakers", True),
                ("mute", "speakers", True),
            ],
        )

    def test_mute_toggle_edge_mode(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="speakers",
                    cc=21,
                    action=ACTION_MUTE_TOGGLE,
                    mute_mode=MUTE_MODE_EDGE,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 21, 127)
        dispatcher.on_cc("Controller", 0, 21, 127)  # held, no new press
        dispatcher.on_cc("Controller", 0, 21, 0)
        dispatcher.on_cc("Controller", 0, 21, 127)
        self.assertEqual(
            calls,
            [
                ("toggle", "speakers", None),
                ("toggle", "speakers", None),
            ],
        )

    def test_mute_toggle_press_mode_127_only(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="speakers",
                    cc=21,
                    action=ACTION_MUTE_TOGGLE,
                    mute_mode=MUTE_MODE_PRESS,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        # Three messages in one burst are one physical press. A high message
        # after the debounce window is a new press.
        with patch(
            "pw_midi_volume.engine.time.monotonic",
            side_effect=[100.0, 100.05, 100.15, 100.50],
        ):
            for value in (127, 127, 127, 127):
                dispatcher.on_cc("Controller", 0, 21, value)
        self.assertEqual(len(calls), 2)

    def test_mute_toggle_press_mode_ignores_release(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="speakers",
                    cc=21,
                    action=ACTION_MUTE_TOGGLE,
                    mute_mode=MUTE_MODE_PRESS,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 21, 127)
        dispatcher.on_cc("Controller", 0, 21, 0)
        dispatcher.on_cc("Controller", 0, 21, 127)
        dispatcher.on_cc("Controller", 0, 21, 0)
        self.assertEqual(len(calls), 2)

    def test_mute_toggle_any_mode_alternating_127_0(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="speakers",
                    cc=21,
                    action=ACTION_MUTE_TOGGLE,
                    mute_mode=MUTE_MODE_ANY,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        for value in (127, 0, 127, 0):
            dispatcher.on_cc("Controller", 0, 21, value)
        self.assertEqual(len(calls), 4)

    def test_multimedia_action_uses_one_press_per_burst(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(
                    sink="",
                    cc=50,
                    channel=0,
                    action=ACTION_MEDIA_PLAY_PAUSE,
                ),
            )
        )
        dispatcher, calls = self.make_dispatcher(config)
        with patch(
            "pw_midi_volume.engine.time.monotonic",
            side_effect=[100.0, 100.05, 100.15, 100.5],
        ):
            for value in (127, 127, 127, 127):
                dispatcher.on_cc("Controller", 0, 50, value)
        self.assertEqual(
            calls,
            [
                ("media", "", ACTION_MEDIA_PLAY_PAUSE),
                ("media", "", ACTION_MEDIA_PLAY_PAUSE),
            ],
        )

    def test_multimedia_release_resets_press_state(self) -> None:
        config = AppConfig(
            bindings=(Binding(sink="", cc=51, channel=0, action=ACTION_MEDIA_NEXT),)
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 51, 127)
        dispatcher.on_cc("Controller", 0, 51, 0)
        dispatcher.on_cc("Controller", 0, 51, 127)
        self.assertEqual(len(calls), 2)

    def test_mute_toggle_is_independent_per_port(self) -> None:
        config = AppConfig(
            bindings=(Binding(sink="speakers", cc=21, action=ACTION_MUTE_TOGGLE),)
        )
        dispatcher, calls = self.make_dispatcher(config)
        dispatcher.on_cc("Controller A", 0, 21, 127)
        dispatcher.on_cc("Controller B", 0, 21, 127)
        self.assertEqual(len(calls), 2)


class FakeController:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.fail = fail
        self.lock = threading.Lock()

    def _record(self, kind: str, sink: str, value: object = None) -> None:
        with self.lock:
            if self.fail:
                raise PipeWireError("simulated failure")
            self.calls.append((kind, sink, value))

    def set_volume(self, sink: str, volume: float) -> None:
        self._record("volume", sink, volume)

    def set_mute(self, sink: str, muted: bool) -> None:
        self._record("mute", sink, muted)

    def toggle_mute(self, sink: str) -> None:
        self._record("toggle", sink)


class SinkWorkerTests(unittest.TestCase):
    def wait_for(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_applies_latest_value(self) -> None:
        controller = FakeController()
        worker = SinkWorker(controller, min_change=0.0)
        worker.submit_volume("speakers", 0.1)
        worker.submit_volume("speakers", 0.8)
        worker.start()
        try:
            self.assertTrue(
                self.wait_for(lambda: ("volume", "speakers", 0.8) in controller.calls)
            )
            self.assertEqual(controller.calls[-1], ("volume", "speakers", 0.8))
        finally:
            worker.stop()
        self.assertFalse(worker.is_alive())

    def test_deduplicates_unchanged_values(self) -> None:
        controller = FakeController()
        worker = SinkWorker(controller, min_change=0.001)
        worker.start()
        try:
            worker.submit_volume("speakers", 0.5)
            self.assertTrue(self.wait_for(lambda: len(controller.calls) >= 1))
            worker.submit_volume("speakers", 0.5005)
            time.sleep(0.1)
            worker.submit_volume("speakers", 0.8)
            self.assertTrue(
                self.wait_for(
                    lambda: controller.calls[-1] == ("volume", "speakers", 0.8)
                )
            )
            self.assertEqual(len(controller.calls), 2)
        finally:
            worker.stop()

    def test_mute_commands(self) -> None:
        controller = FakeController()
        worker = SinkWorker(controller)
        worker.start()
        try:
            worker.submit_mute("speakers", True)
            self.assertTrue(
                self.wait_for(lambda: ("mute", "speakers", True) in controller.calls)
            )
            worker.submit_mute("speakers", False)
            self.assertTrue(
                self.wait_for(lambda: ("mute", "speakers", False) in controller.calls)
            )
            self.assertEqual(
                [call for call in controller.calls if call[0] == "mute"],
                [("mute", "speakers", True), ("mute", "speakers", False)],
            )
        finally:
            worker.stop()

    def test_toggle_commands_are_not_coalesced(self) -> None:
        controller = FakeController()
        worker = SinkWorker(controller)
        worker.submit_mute_toggle("speakers")
        worker.submit_mute_toggle("speakers")
        worker.start()
        try:
            self.assertTrue(
                self.wait_for(
                    lambda: (
                        len([call for call in controller.calls if call[0] == "toggle"])
                        >= 2
                    )
                )
            )
            self.assertEqual(
                [call for call in controller.calls if call[0] == "toggle"],
                [("toggle", "speakers", None), ("toggle", "speakers", None)],
            )
        finally:
            worker.stop()

    def test_failure_does_not_kill_worker(self) -> None:
        controller = FakeController(fail=True)
        worker = SinkWorker(controller, error_log_interval=100)
        worker.start()
        try:
            worker.submit_volume("speakers", 0.5)
            worker.submit_mute("speakers", True)
            time.sleep(0.1)
            self.assertTrue(worker.is_alive())
        finally:
            worker.stop()


class MidiParserTests(unittest.TestCase):
    def test_parse_cc(self) -> None:
        self.assertEqual(parse_cc([0xB2, 7, 100]), (2, 7, 100))
        self.assertEqual(parse_cc([0xB0, 127, 0]), (0, 127, 0))

    def test_ignore_other_messages(self) -> None:
        self.assertIsNone(parse_cc([0x90, 60, 127]))
        self.assertIsNone(parse_cc([0xB0, 7]))
        self.assertIsNone(parse_cc([0xF8]))

    def test_port_matches(self) -> None:
        self.assertTrue(port_matches(None, "Anything"))
        self.assertTrue(port_matches("launch", "Launch Control MIDI 1"))
        self.assertFalse(port_matches("launch", "Other"))

    def test_describe_midi_message(self) -> None:
        self.assertEqual(describe_midi_message([0xB0, 7, 64]), "ch 01  CC 7 = 64")
        self.assertEqual(
            describe_midi_message([0x91, 60, 100]),
            "ch 02  Note On C4 velocity 100",
        )
        self.assertEqual(
            describe_midi_message([0x91, 60, 0]),
            "ch 02  Note Off C4 velocity 0",
        )
        self.assertEqual(
            describe_midi_message([0xC3, 5]),
            "ch 04  Program Change 5",
        )
        self.assertEqual(
            describe_midi_message([0xE0, 0, 64]),
            "ch 01  Pitch Bend +0",
        )
        self.assertEqual(
            describe_midi_message([0xF0, 0x7D, 1, 2, 0xF7]),
            "SysEx (5 byte(s))",
        )
        self.assertEqual(
            describe_midi_message([0x80, 60]),
            "Raw MIDI: 80 3C",
        )


if __name__ == "__main__":
    unittest.main()
