from __future__ import annotations

import time
import unittest

from pw_midi_volume.config import (
    ACTION_MEDIA_FAST_FORWARD,
    ACTION_MEDIA_NEXT,
    ACTION_MEDIA_PLAY_PAUSE,
    ACTION_MEDIA_REWIND,
    AppConfig,
    MediaConfig,
)
from pw_midi_volume.media import MediaController, MediaError, MediaWorker


class FakeRunner:
    def __init__(
        self,
        players: list[str],
        statuses: dict[str, str] | None = None,
    ) -> None:
        self.players = players
        self.statuses = statuses or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        if "org.freedesktop.DBus.ListNames" in args:
            names = ", ".join(f"'{name}'" for name in self.players)
            return f"([{names}],)"
        if "org.freedesktop.DBus.Properties.Get" in args:
            dest = args[args.index("--dest") + 1]
            status = self.statuses.get(dest, "Unknown")
            return f"(<'{status}'>,)"
        return "()"


class ControllerTests(unittest.TestCase):
    def test_list_and_select_playing_player(self) -> None:
        runner = FakeRunner(
            [
                "org.mpris.MediaPlayer2.paused",
                "org.mpris.MediaPlayer2.active",
            ],
            {
                "org.mpris.MediaPlayer2.paused": "Paused",
                "org.mpris.MediaPlayer2.active": "Playing",
            },
        )
        controller = MediaController(runner=runner)
        self.assertEqual(
            controller.list_players(),
            [
                "org.mpris.MediaPlayer2.active",
                "org.mpris.MediaPlayer2.paused",
            ],
        )
        selected = controller.select_player()
        self.assertEqual(selected.bus_name, "org.mpris.MediaPlayer2.active")
        self.assertEqual(selected.status, "Playing")

    def test_player_filter(self) -> None:
        runner = FakeRunner(
            [
                "org.mpris.MediaPlayer2.chromium.instance1",
                "org.mpris.MediaPlayer2.spotify",
            ],
            {"org.mpris.MediaPlayer2.spotify": "Paused"},
        )
        controller = MediaController(runner=runner)
        selected = controller.select_player("spotify")
        self.assertEqual(selected.bus_name, "org.mpris.MediaPlayer2.spotify")

    def test_no_players(self) -> None:
        controller = MediaController(runner=FakeRunner([]))
        with self.assertRaises(MediaError):
            controller.select_player()

    def test_perform_method(self) -> None:
        runner = FakeRunner(
            ["org.mpris.MediaPlayer2.spotify"],
            {"org.mpris.MediaPlayer2.spotify": "Playing"},
        )
        controller = MediaController(runner=runner)
        player = controller.perform(
            ACTION_MEDIA_PLAY_PAUSE,
            MediaConfig(),
        )
        self.assertEqual(player.bus_name, "org.mpris.MediaPlayer2.spotify")
        self.assertIn(
            "org.mpris.MediaPlayer2.Player.PlayPause",
            runner.calls[-1],
        )

    def test_seek_actions(self) -> None:
        runner = FakeRunner(
            ["org.mpris.MediaPlayer2.spotify"],
            {"org.mpris.MediaPlayer2.spotify": "Playing"},
        )
        controller = MediaController(runner=runner)
        controller.perform(
            ACTION_MEDIA_FAST_FORWARD,
            MediaConfig(seek_seconds=5),
        )
        self.assertIn("org.mpris.MediaPlayer2.Player.Seek", runner.calls[-1])
        self.assertEqual(runner.calls[-1][-1], "5000000")
        controller.perform(
            ACTION_MEDIA_REWIND,
            MediaConfig(seek_seconds=5),
        )
        self.assertIn("org.mpris.MediaPlayer2.Player.Seek", runner.calls[-1])
        self.assertEqual(runner.calls[-1][-2], "--")
        self.assertEqual(runner.calls[-1][-1], "-5000000")

    def test_unknown_action(self) -> None:
        runner = FakeRunner(
            ["org.mpris.MediaPlayer2.spotify"],
            {"org.mpris.MediaPlayer2.spotify": "Playing"},
        )
        controller = MediaController(runner=runner)
        with self.assertRaises(MediaError):
            controller.perform("bogus", MediaConfig())

    def test_next_method(self) -> None:
        runner = FakeRunner(
            ["org.mpris.MediaPlayer2.spotify"],
            {"org.mpris.MediaPlayer2.spotify": "Playing"},
        )
        controller = MediaController(runner=runner)
        controller.perform(ACTION_MEDIA_NEXT, MediaConfig())
        self.assertIn("org.mpris.MediaPlayer2.Player.Next", runner.calls[-1])


class WorkerTests(unittest.TestCase):
    class FakeController:
        def __init__(self) -> None:
            self.actions: list[str] = []

        def perform(self, action: str, config: MediaConfig) -> None:
            self.actions.append(action)

    def test_worker_processes_queue(self) -> None:
        controller = self.FakeController()
        worker = MediaWorker(
            controller,  # type: ignore[arg-type]
            lambda: AppConfig(media=MediaConfig()),
        )
        worker.start()
        try:
            worker.submit(ACTION_MEDIA_PLAY_PAUSE)
            worker.submit(ACTION_MEDIA_NEXT)
            deadline = time.monotonic() + 2
            while len(controller.actions) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(
                controller.actions,
                [ACTION_MEDIA_PLAY_PAUSE, ACTION_MEDIA_NEXT],
            )
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
