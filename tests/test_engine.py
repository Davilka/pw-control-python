from __future__ import annotations

import threading
import time
import unittest

from pw_midi_volume.config import AppConfig, Binding, MidiConfig
from pw_midi_volume.engine import BindingDispatcher, VolumeWorker
from pw_midi_volume.midi import parse_cc, port_matches
from pw_midi_volume.pipewire import PipeWireError


class DispatcherTests(unittest.TestCase):
    def make_dispatcher(
        self, config: AppConfig
    ) -> tuple[BindingDispatcher, list[tuple[str, float]]]:
        updates: list[tuple[str, float]] = []
        dispatcher = BindingDispatcher(
            lambda: config,
            lambda sink, volume: updates.append((sink, volume)),
        )
        return dispatcher, updates

    def test_matches_cc_and_submits_volume(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(sink="speakers", cc=7, channel=0),
                Binding(sink="headphones", cc=7, channel=0),
                Binding(sink="other", cc=8),
            )
        )
        dispatcher, updates = self.make_dispatcher(config)
        matched = dispatcher.on_cc("Controller", 0, 7, 64)
        self.assertEqual(matched, 2)
        self.assertEqual(len(updates), 2)
        self.assertEqual({sink for sink, _ in updates}, {"speakers", "headphones"})
        self.assertTrue(all(abs(volume - 64 / 127) < 1e-6 for _, volume in updates))

    def test_channel_filter_and_all_channels(self) -> None:
        config = AppConfig(
            bindings=(
                Binding(sink="one", cc=1, channel=2),
                Binding(sink="two", cc=1, channel=None),
            )
        )
        dispatcher, updates = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Controller", 2, 1, 127), 2)
        self.assertEqual(len(updates), 2)

        updates.clear()
        self.assertEqual(dispatcher.on_cc("Controller", 3, 1, 127), 1)
        self.assertEqual(updates, [("two", 1.0)])

    def test_disabled_binding(self) -> None:
        config = AppConfig(bindings=(Binding(sink="speakers", cc=7, enabled=False),))
        dispatcher, updates = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Controller", 0, 7, 100), 0)
        self.assertEqual(updates, [])

    def test_port_filter(self) -> None:
        config = AppConfig(
            midi=MidiConfig(port="launch control"),
            bindings=(Binding(sink="speakers", cc=7),),
        )
        dispatcher, updates = self.make_dispatcher(config)
        self.assertEqual(dispatcher.on_cc("Launch Control MIDI 1", 0, 7, 1), 1)
        self.assertEqual(dispatcher.on_cc("Other Controller", 0, 7, 1), 0)
        self.assertEqual(len(updates), 1)

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
        dispatcher, updates = self.make_dispatcher(config)
        dispatcher.on_cc("Controller", 0, 7, 0)
        dispatcher.on_cc("Controller", 0, 7, 127)
        self.assertAlmostEqual(updates[0][1], 0.2)
        self.assertAlmostEqual(updates[1][1], 0.8)


class FakeController:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, float]] = []
        self.fail = fail
        self.lock = threading.Lock()

    def set_volume(self, sink: str, volume: float) -> None:
        with self.lock:
            if self.fail:
                raise PipeWireError("simulated failure")
            self.calls.append((sink, volume))


class VolumeWorkerTests(unittest.TestCase):
    def wait_for(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_applies_latest_value(self) -> None:
        controller = FakeController()
        worker = VolumeWorker(controller, min_change=0.0)
        worker.submit("speakers", 0.1)
        worker.submit("speakers", 0.8)
        worker.start()
        try:
            self.assertTrue(
                self.wait_for(lambda: ("speakers", 0.8) in controller.calls)
            )
            self.assertEqual(controller.calls[-1], ("speakers", 0.8))
        finally:
            worker.stop()
        self.assertFalse(worker.is_alive())

    def test_deduplicates_unchanged_values(self) -> None:
        controller = FakeController()
        worker = VolumeWorker(controller, min_change=0.001)
        worker.start()
        try:
            worker.submit("speakers", 0.5)
            self.assertTrue(self.wait_for(lambda: len(controller.calls) >= 1))
            worker.submit("speakers", 0.5005)
            time.sleep(0.1)
            worker.submit("speakers", 0.8)
            self.assertTrue(self.wait_for(lambda: controller.calls[-1][1] == 0.8))
            self.assertEqual(len(controller.calls), 2)
        finally:
            worker.stop()

    def test_failure_does_not_kill_worker(self) -> None:
        controller = FakeController(fail=True)
        worker = VolumeWorker(controller, error_log_interval=100)
        worker.start()
        try:
            worker.submit("speakers", 0.5)
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


if __name__ == "__main__":
    unittest.main()
