"""Locale discovery, translation and GUI language preferences."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
LOCALE_SUFFIXES = (".yaml", ".yml", ".json")
DEFAULT_LOCALE_FILES = ("en.yaml", "ru.yaml")


@dataclass(frozen=True, slots=True)
class LocaleInfo:
    code: str
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class LocaleData:
    info: LocaleInfo
    strings: dict[str, str]


class Translator:
    """Translate keys with an English fallback.

    Missing keys and formatting errors fall back to the fallback string or the
    key itself, so a partially translated file can never break the GUI.
    """

    def __init__(
        self,
        locale: LocaleData | None,
        fallback: dict[str, str],
        *,
        code: str = DEFAULT_LANGUAGE,
        name: str = "English",
    ) -> None:
        self.locale = locale
        self.code = locale.info.code if locale is not None else code
        self.name = locale.info.name if locale is not None else name
        self._fallback = dict(fallback)
        self._strings = dict(fallback)
        if locale is not None:
            self._strings.update(locale.strings)

    def __call__(self, key: str, **values: Any) -> str:
        template = self._strings.get(key)
        if template is None:
            LOG.debug("missing translation key %r for language %s", key, self.code)
            template = self._fallback.get(key, key)
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            LOG.warning(
                "invalid translation placeholders for key %r in language %s",
                key,
                self.code,
            )
            return template


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LocaleManager:
    """Discover locale files and provide translators for them."""

    def __init__(self, locale_dir: str | Path) -> None:
        self.locale_dir = Path(locale_dir).expanduser()
        self.fallback = self._load_packaged_fallback()
        self.available: tuple[LocaleData, ...] = ()

    @staticmethod
    def _load_packaged_fallback() -> dict[str, str]:
        try:
            resource = files("pw_midi_volume").joinpath("locales/en.yaml")
            raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
            strings = raw.get("strings") if isinstance(raw, dict) else None
            if isinstance(strings, dict):
                return {
                    str(key): str(value)
                    for key, value in strings.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
        except Exception:
            LOG.exception("cannot load the packaged English locale")
        return {}

    def _files(self) -> list[Path]:
        if not self.locale_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.locale_dir.iterdir()
            if path.is_file() and path.suffix.lower() in LOCALE_SUFFIXES
        )

    @staticmethod
    def _load_file(path: Path) -> LocaleData | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            LOG.warning("cannot read locale %s: %s", path, exc)
            return None

        if not isinstance(raw, dict):
            LOG.warning("locale %s must contain a mapping", path)
            return None
        code = raw.get("language")
        name = raw.get("name")
        strings = raw.get("strings")
        if not isinstance(code, str) or not code.strip():
            LOG.warning("locale %s has no language code", path)
            return None
        if not isinstance(name, str) or not name.strip():
            LOG.warning("locale %s has no display name", path)
            return None
        if not isinstance(strings, dict):
            LOG.warning("locale %s has no strings mapping", path)
            return None

        normalized_strings = {
            str(key): str(value)
            for key, value in strings.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return LocaleData(
            info=LocaleInfo(
                code=code.strip(),
                name=name.strip(),
                path=path,
            ),
            strings=normalized_strings,
        )

    @staticmethod
    def _packaged_locale_text(filename: str) -> str:
        return (
            files("pw_midi_volume")
            .joinpath("locales", filename)
            .read_text(encoding="utf-8")
        )

    def _seed_defaults(self) -> None:
        try:
            self.locale_dir.mkdir(parents=True, exist_ok=True)
            for filename in DEFAULT_LOCALE_FILES:
                target = self.locale_dir / filename
                if target.exists():
                    continue
                _atomic_write(target, self._packaged_locale_text(filename))
                LOG.info("installed default locale: %s", target)
        except Exception as exc:
            LOG.warning(
                "cannot install default locales in %s: %s", self.locale_dir, exc
            )

    def _upgrade_default_locales(self) -> None:
        """Add newly introduced keys to the bundled English/Russian files.

        Existing translations are preserved. This keeps locale files installed
        by an older version usable after an application update.
        """

        for filename in DEFAULT_LOCALE_FILES:
            target = self.locale_dir / filename
            if not target.exists():
                continue
            try:
                packaged = yaml.safe_load(self._packaged_locale_text(filename))
                current = yaml.safe_load(target.read_text(encoding="utf-8"))
                if not isinstance(packaged, dict) or not isinstance(current, dict):
                    continue
                packaged_strings = packaged.get("strings")
                current_strings = current.get("strings")
                if not isinstance(packaged_strings, dict) or not isinstance(
                    current_strings, dict
                ):
                    continue
                missing = {
                    key: value
                    for key, value in packaged_strings.items()
                    if key not in current_strings
                }
                if not missing:
                    continue
                current_strings.update(missing)
                text = (
                    "# pw-midi-volume locale file\n"
                    "# Place additional locale files in this folder as "
                    ".yaml, .yml or .json.\n"
                    "# See README.md for the format.\n\n"
                    + yaml.safe_dump(
                        current,
                        allow_unicode=True,
                        sort_keys=False,
                        width=120,
                    )
                )
                _atomic_write(target, text)
                LOG.info(
                    "updated locale %s with %d new key(s)",
                    target,
                    len(missing),
                )
            except Exception as exc:
                LOG.warning("cannot update locale %s: %s", target, exc)

    def refresh(self) -> tuple[LocaleData, ...]:
        """Rescan locale files, seeding defaults when no valid locale exists."""

        self.locale_dir.mkdir(parents=True, exist_ok=True)
        self._upgrade_default_locales()
        locales = self._discover()
        if not locales:
            self._seed_defaults()
            locales = self._discover()

        locales.sort(key=lambda item: (item.info.name.casefold(), item.info.code))
        self.available = tuple(locales)
        return self.available

    def _discover(self) -> list[LocaleData]:
        locales: list[LocaleData] = []
        seen_codes: set[str] = set()
        for path in self._files():
            data = self._load_file(path)
            if data is None:
                continue
            if data.info.code in seen_codes:
                LOG.warning(
                    "ignoring duplicate locale code %r in %s",
                    data.info.code,
                    path,
                )
                continue
            seen_codes.add(data.info.code)
            locales.append(data)
        return locales

    def translator(self, code: str | None) -> Translator:
        if code:
            for data in self.available:
                if data.info.code == code:
                    return Translator(data, self.fallback)
            for data in self.available:
                if data.info.code.split("-")[0] == code.split("-")[0]:
                    return Translator(data, self.fallback)
        if self.available:
            return Translator(self.available[0], self.fallback)
        return Translator(None, self.fallback)

    def choose_initial_code(self, preferred: str | None = None) -> str:
        codes = {data.info.code for data in self.available}
        if preferred in codes:
            return preferred or DEFAULT_LANGUAGE
        system_code = system_language_code()
        if system_code:
            for code in codes:
                if code == system_code or code.split("-")[0] == system_code:
                    return code
        if DEFAULT_LANGUAGE in codes:
            return DEFAULT_LANGUAGE
        if self.available:
            return self.available[0].info.code
        return DEFAULT_LANGUAGE


def system_language_code() -> str | None:
    raw = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or ""
    ).strip()
    if not raw:
        return None
    code = raw.split(".", 1)[0].replace("_", "-")
    if not code or code in {"C", "POSIX"}:
        return None
    return code.split("-", 1)[0].lower()


def load_language_preference(path: str | Path) -> str | None:
    """Read the GUI language from a small YAML file."""

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        LOG.warning("cannot read GUI settings %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    language = raw.get("language")
    return language if isinstance(language, str) and language.strip() else None


def save_language_preference(path: str | Path, code: str) -> None:
    """Persist the selected GUI language next to the main configuration."""

    target = Path(path).expanduser()
    text = f"# pw-midi-volume GUI settings\nlanguage: {code}\n"
    try:
        _atomic_write(target, text)
    except OSError as exc:
        LOG.warning("cannot save GUI settings to %s: %s", target, exc)
