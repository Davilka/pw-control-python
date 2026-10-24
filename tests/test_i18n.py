from __future__ import annotations

import os
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

import yaml

from pw_midi_volume.i18n import (
    LocaleManager,
    Translator,
    load_language_preference,
    save_language_preference,
    system_language_code,
)


class LocaleManagerTests(unittest.TestCase):
    def test_default_locales_are_installed_and_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = LocaleManager(Path(directory) / "locales")
            locales = manager.refresh()

            self.assertEqual(
                {locale.info.code for locale in locales},
                {"en", "ru"},
            )
            self.assertEqual(
                {path.name for path in (Path(directory) / "locales").iterdir()},
                {"en.yaml", "ru.yaml"},
            )
            russian = manager.translator("ru")
            self.assertEqual(russian("button.save"), "Сохранить")
            english = manager.translator("en")
            self.assertEqual(english("button.save"), "Save")

    def test_custom_locale_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locale_dir = Path(directory) / "locales"
            manager = LocaleManager(locale_dir)
            manager.refresh()
            (locale_dir / "de.yaml").write_text(
                yaml.safe_dump(
                    {
                        "language": "de",
                        "name": "Deutsch",
                        "strings": {
                            "button.save": "Speichern",
                            "status.loaded": "Geladen: {count}",
                        },
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            locales = manager.refresh()
            self.assertIn("de", {locale.info.code for locale in locales})

            german = manager.translator("de")
            self.assertEqual(german("button.save"), "Speichern")
            self.assertEqual(german("status.loaded", count=3), "Geladen: 3")
            # Missing keys fall back to the packaged English locale.
            self.assertEqual(german("button.reload"), "Reload")

    def test_old_default_locale_gets_new_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locale_dir = Path(directory) / "locales"
            locale_dir.mkdir(parents=True)
            (locale_dir / "en.yaml").write_text(
                yaml.safe_dump(
                    {
                        "language": "en",
                        "name": "English",
                        "strings": {"window.title": "My title"},
                    }
                ),
                encoding="utf-8",
            )
            (locale_dir / "ru.yaml").write_text(
                yaml.safe_dump(
                    {
                        "language": "ru",
                        "name": "Русский",
                        "strings": {"window.title": "Мой заголовок"},
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            manager = LocaleManager(locale_dir)
            manager.refresh()
            english = yaml.safe_load(
                (locale_dir / "en.yaml").read_text(encoding="utf-8")
            )
            russian = yaml.safe_load(
                (locale_dir / "ru.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(english["strings"]["window.title"], "My title")
            self.assertEqual(russian["strings"]["window.title"], "Мой заголовок")
            self.assertIn("button.save", english["strings"])
            self.assertIn("action.media_play_pause", russian["strings"])

    def test_invalid_locale_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locale_dir = Path(directory) / "locales"
            manager = LocaleManager(locale_dir)
            manager.refresh()
            (locale_dir / "broken.yaml").write_text(
                "language: broken\nname: Broken\nstrings: []\n",
                encoding="utf-8",
            )
            locales = manager.refresh()
            self.assertEqual(
                {locale.info.code for locale in locales},
                {"en", "ru"},
            )

    def test_choose_initial_prefers_saved_then_system(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = LocaleManager(Path(directory) / "locales")
            manager.refresh()
            self.assertEqual(manager.choose_initial_code("ru"), "ru")
            with patch.dict(
                os.environ,
                {"LC_ALL": "ru_RU.UTF-8", "LANG": "", "LC_MESSAGES": ""},
                clear=False,
            ):
                self.assertEqual(manager.choose_initial_code(), "ru")
            with patch.dict(
                os.environ,
                {"LC_ALL": "en_US.UTF-8", "LANG": "", "LC_MESSAGES": ""},
                clear=False,
            ):
                self.assertEqual(manager.choose_initial_code(), "en")


class TranslatorTests(unittest.TestCase):
    def test_placeholder_errors_fall_back_to_template(self) -> None:
        translator = Translator(
            None,
            {"hello": "Hello, {name}!"},
        )
        self.assertEqual(translator("hello", name="World"), "Hello, World!")
        self.assertEqual(translator("hello"), "Hello, {name}!")
        self.assertEqual(translator("missing"), "missing")


class PreferenceTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.yaml"
            self.assertIsNone(load_language_preference(path))
            save_language_preference(path, "ru")
            self.assertEqual(load_language_preference(path), "ru")

    def test_invalid_preference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.yaml"
            path.write_text("- not a mapping\n", encoding="utf-8")
            self.assertIsNone(load_language_preference(path))

    def test_system_language_code(self) -> None:
        with patch.dict(
            os.environ,
            {"LC_ALL": "ru_RU.UTF-8", "LC_MESSAGES": "", "LANG": ""},
            clear=False,
        ):
            self.assertEqual(system_language_code(), "ru")
        with patch.dict(
            os.environ,
            {"LC_ALL": "C", "LC_MESSAGES": "", "LANG": ""},
            clear=False,
        ):
            self.assertIsNone(system_language_code())


class ShippedLocaleTests(unittest.TestCase):
    def load(self, code: str) -> dict:
        resource = files("pw_midi_volume").joinpath(f"locales/{code}.yaml")
        return yaml.safe_load(resource.read_text(encoding="utf-8"))

    def test_default_locales_have_the_same_keys(self) -> None:
        english = self.load("en")
        russian = self.load("ru")
        self.assertEqual(english["language"], "en")
        self.assertEqual(russian["language"], "ru")
        self.assertEqual(set(english["strings"]), set(russian["strings"]))

    def test_english_locale_has_all_used_keys(self) -> None:
        import re

        source = files("pw_midi_volume").joinpath("gui.py").read_text(encoding="utf-8")
        used = set(re.findall(r"self\._t\(\s*[\"']([^\"']+)[\"']", source))
        used.update(re.findall(r"\(CHOICE_[A-Z_]+, \"([^\"]+)\"\)", source))
        used.update(re.findall(r"self\._t\(\s*[\"']([^\"']+)[\"']\s*if", source))
        used.update(re.findall(r"else \"([^\"]+)\"\s*\n\s*\)", source))
        strings = self.load("en")["strings"]
        self.assertEqual(sorted(used - set(strings)), [])


if __name__ == "__main__":
    unittest.main()
