from __future__ import annotations

import json
import unittest

from pw_midi_volume.pipewire import (
    PipeWireController,
    PipeWireError,
    SinkNotFoundError,
    parse_pw_dump,
)

PW_DUMP = json.dumps(
    [
        {
            "id": 0,
            "type": "PipeWire:Interface:Core",
        },
        {
            "id": 41,
            "type": "PipeWire:Interface:Node",
            "info": {
                "state": "running",
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "alsa_output.good",
                    "node.description": "Good Speakers",
                },
            },
        },
        {
            "id": 42,
            "type": "PipeWire:Interface:Node",
            "info": {
                "state": "suspended",
                "props": {
                    "media.class": "Audio/Source",
                    "node.name": "alsa_input.microphone",
                    "node.description": "Microphone",
                },
            },
        },
        {
            "id": 43,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "alsa_output.no-description",
                },
            },
        },
    ]
)


class ParseTests(unittest.TestCase):
    def test_parse_sinks(self) -> None:
        sinks = parse_pw_dump(PW_DUMP)
        self.assertEqual(len(sinks), 2)
        by_name = {sink.name: sink for sink in sinks}
        good = by_name["alsa_output.good"]
        generic = by_name["alsa_output.no-description"]
        self.assertEqual(good.id, 41)
        self.assertEqual(good.description, "Good Speakers")
        self.assertEqual(good.state, "running")
        self.assertEqual(generic.description, "alsa_output.no-description")

    def test_display_name(self) -> None:
        by_name = {sink.name: sink for sink in parse_pw_dump(PW_DUMP)}
        sink = by_name["alsa_output.good"]
        self.assertEqual(sink.display_name, "Good Speakers — alsa_output.good")

    def test_invalid_json(self) -> None:
        with self.assertRaises(PipeWireError):
            parse_pw_dump("{not json")


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[list[str]] = []
        self.dump_count = 0

        def runner(args: list[str]) -> str:
            self.calls.append(list(args))
            if args[0] == "pw-dump":
                self.dump_count += 1
                return PW_DUMP
            if args[0] == "wpctl":
                return ""
            raise AssertionError(args)

        self.controller = PipeWireController(cache_ttl=60, runner=runner)

    def test_lists_and_caches(self) -> None:
        first = self.controller.list_sinks()
        second = self.controller.list_sinks()
        self.assertEqual(len(first), 2)
        self.assertEqual(first, second)
        self.assertEqual(self.dump_count, 1)

    def test_resolve_by_name_description_and_id(self) -> None:
        self.assertEqual(self.controller.resolve("alsa_output.good").id, 41)
        self.assertEqual(self.controller.resolve("Good Speakers").id, 41)
        self.assertEqual(self.controller.resolve("41").id, 41)

    def test_set_volume(self) -> None:
        sink = self.controller.set_volume("alsa_output.good", 0.5)
        self.assertEqual(sink.id, 41)
        command = self.calls[-1]
        self.assertEqual(command[:4], ["wpctl", "set-volume", "--limit", "1.0"])
        self.assertEqual(command[4], "41")
        self.assertAlmostEqual(float(command[5]), 0.5)

    def test_volume_is_clamped(self) -> None:
        self.controller.set_volume("alsa_output.good", 2.0)
        self.assertAlmostEqual(float(self.calls[-1][5]), 1.0)
        self.controller.set_volume("alsa_output.good", -1.0)
        self.assertAlmostEqual(float(self.calls[-1][5]), 0.0)

    def test_missing_sink(self) -> None:
        with self.assertRaises(SinkNotFoundError):
            self.controller.set_volume("does-not-exist", 0.5)


if __name__ == "__main__":
    unittest.main()
