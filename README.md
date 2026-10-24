# pw-midi-volume

[English](#english) · [Русский](#russian)

<a id="english"></a>
## English

### Overview

Control **PipeWire sink volume and mute**, and manage multimedia players over
**MPRIS**, from a MIDI controller.

The project contains two applications:

* `pw-midi-volume` — background daemon. It listens for MIDI CC messages, changes
  sink volume and mute state through `wpctl`, and controls MPRIS media players.
  It can run as a systemd user service.
* `pw-midi-volume-gui` — PySide6 settings application for binding MIDI CC
  controls to sinks and multimedia actions. It supports a system tray icon and
  saves everything to the same YAML file.

The daemon watches the YAML file and reloads it automatically (poll interval
0.5 s), and also supports `SIGHUP`. If the file temporarily contains an error,
the last valid configuration remains active.

### Features

* Absolute MIDI Control Change messages (CC 0–127).
* Binding actions: volume, Mute/Unmute by CC level, and mute toggle.
* Multimedia over MPRIS: Play/Pause, Play, Pause, Stop, next/previous track,
  rewind and fast forward.
* Any MIDI channel or a specific channel 1–16.
* One CC can control several sinks at once.
* Several bindings can target the same sink with different CC controls.
* Configurable volume range in percent for volume bindings.
* MIDI input filter: all devices or a substring of a specific port name.
* MIDI device hot-plug support.
* MIDI Learn in the GUI.
* Minimize/close the GUI to the system tray.
* Built-in MIDI monitor for incoming CC, Note On/Off, Program Change, Pitch Bend
  and other messages, with timestamp, port and channel.
* Multilingual GUI with a language selector; English and Russian are included.
* Application icon, desktop entry and systemd user unit.

### Requirements

* Linux with PipeWire and WirePlumber (`wpctl`, `pw-dump`).
* Python 3.10+.
* A MIDI controller available through the ALSA sequencer.
* For multimedia: an MPRIS-compatible player and the `gdbus` utility (GLib).
* For the GUI: PySide6.

Check the PipeWire tools:

```bash
wpctl status
pw-dump >/dev/null
```

### Quick installation

The script creates a private virtual environment and installs the package,
icon, desktop entry and systemd user unit:

```bash
./scripts/install-user.sh
systemctl --user enable --now pw-midi-volume.service
pw-midi-volume-gui
```

No root access is required. By default, files are installed under `~/.local`
and `~/.config`.

#### Start the GUI in the tray at login

```bash
mkdir -p ~/.config/autostart
sed "s|^Exec=.*|Exec=$HOME/.local/bin/pw-midi-volume-gui --minimized|" \
    contrib/pw-midi-volume-gui-autostart.desktop \
    > ~/.config/autostart/pw-midi-volume-gui.desktop
```

### Uninstallation

The uninstall script stops the service and running processes, then removes the
virtual environment, launchers, systemd unit, desktop entry, icon and autostart
entry. The YAML configuration is preserved by default.

```bash
# remove the application, keep the configuration
./scripts/uninstall-user.sh

# remove the application and its configuration
./scripts/uninstall-user.sh --purge

# non-interactive mode
./scripts/uninstall-user.sh --yes
```

To remove a preserved configuration manually:

```bash
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/pw-midi-volume"
```

### Manual development installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[gui]'

pw-midi-volume check
pw-midi-volume midi-ports
pw-midi-volume sinks
pw-midi-volume daemon
```

Run the daemon in the background:

```bash
systemctl --user enable --now pw-midi-volume.service
journalctl --user -u pw-midi-volume -f
```

For a manual installation, update `ExecStart` in
`contrib/pw-midi-volume.service` to the absolute path of `pw-midi-volume`, copy
the unit to `~/.config/systemd/user/`, and run
`systemctl --user daemon-reload`.

### Configuration

Default configuration file:

```text
~/.config/pw-midi-volume/config.yaml
```

It is created automatically on the first daemon or GUI start. Use `--config` on
either application to override the path.

Example:

```yaml
version: 1

midi:
  # null = all MIDI inputs; otherwise a case-insensitive substring of the port name.
  port: null

media:
  # null = automatically choose the active MPRIS player.
  # Otherwise a substring of the D-Bus name, for example "spotify" or "chromium".
  player: null
  # Rewind/fast-forward step for media_rewind and media_fast_forward, in seconds.
  seek_seconds: 10

bindings:
  - sink: "alsa_output.pci-0000_00_1f.3.analog-stereo"
    action: volume
    cc: 7
    channel: 0          # 0–15; null = any channel
    min_percent: 0
    max_percent: 100
    enabled: true

  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: volume
    cc: 7
    channel: 0
    min_percent: 0
    max_percent: 80
    enabled: true

  # Mute button: CC < 64 unmutes, CC >= 64 mutes.
  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: mute
    cc: 20
    channel: 0
    enabled: true

  # Mute button: toggle on each press.
  # mute_mode: press — one toggle per press; repeated 127 messages within ~250 ms
  #                      are treated as one press.
  #            any   — the button alternates 127 and 0.
  #            edge  — toggle only on the 0–63 → 64–127 transition.
  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: mute_toggle
    mute_mode: press
    cc: 21
    channel: 0
    enabled: true

  # Multimedia: no sink is required; controls the active MPRIS player.
  - action: media_play_pause
    cc: 50
    channel: 0
    enabled: true

  - action: media_next
    cc: 51
    channel: 0
    enabled: true

  - action: media_rewind
    cc: 52
    channel: 0
    enabled: true
```

Fields:

| Field | Description |
| --- | --- |
| `sink` | PipeWire sink `node.name`. Required for volume/mute, unused for multimedia. |
| `action` | `volume`, `mute`, `mute_toggle`, or a multimedia action. Optional; defaults to `volume`. |
| `mute_mode` | Mode for `action: mute_toggle`: `press`, `any` or `edge`. Defaults to `press`. |
| `cc` | Control Change number, 0–127. |
| `channel` | MIDI channel in the config: 0–15. The GUI displays channels as 1–16. `null` = any. |
| `min_percent` / `max_percent` | Volume range for `action: volume`, mapped from CC 0 and 127. |
| `enabled` | Enables or temporarily disables a binding. |
| `midi.port` | `null` = all inputs; otherwise a substring of the MIDI port name. |
| `media.player` | `null` = choose the active MPRIS player automatically; otherwise a substring of its D-Bus name. |
| `media.seek_seconds` | Seek step for `media_rewind` / `media_fast_forward`, from 1 to 600 seconds. |

If `action` is omitted (for example in an older configuration), `volume` is
used.

MIDI CC values map linearly to volume. For example, `CC=64` with a 0–100% range
gives approximately 50%. The range must go from a lower to a higher value;
inverted ranges (high CC = quieter) are not supported.

#### Mute actions

* `action: mute` — CC below 64 unmutes, CC 64 or above mutes. Suitable for
  faders, absolute controls, and buttons that send state rather than impulses.
* `action: mute_toggle` — toggles mute on a button. The behaviour is selected by
  `mute_mode`:
  * `press` — one toggle per press (CC `>= 64`). Repeated 127 messages within
    approximately 250 ms are treated as one press. Works when release sends
    nothing **or** sends 0. This is the default.
  * `any` — toggle on **every** CC message. Suitable for buttons that alternate
    127 and 0 on successive presses.
  * `edge` — toggle only on a transition from 0–63 to 64–127. Protects against
    repeated 127 messages while a button is held, but requires release to send 0.

The same `127, 0` stream can mean "press and release" or "two consecutive
presses", so the mode is explicit rather than guessed.

| Button behaviour | Mode |
| --- | --- |
| Press → 127, release → nothing | `press` or `any` |
| Press → 127, release → 0 | `press` or `edge` |
| Successive presses → 127, 0, 127, 0 | `any` |

`mute` and `mute_toggle` use `wpctl set-mute`, so mute applies to the whole sink
(all channels).

Only absolute CC messages are supported. If a controller knob is configured as
relative/endless, switch it to absolute mode on the controller itself.

#### Multimedia (MPRIS)

Multimedia bindings do not use the `sink` field:

* `media_play_pause` — Play/Pause;
* `media_play` — Play;
* `media_pause` — Pause;
* `media_stop` — Stop;
* `media_next` — next track;
* `media_previous` — previous track;
* `media_rewind` — seek back by `media.seek_seconds`;
* `media_fast_forward` — seek forward by `media.seek_seconds`.

Control is performed through the standard MPRIS D-Bus interface. When
`media.player` is `null`, the active player is selected: first a player with
`Playing` status, then `Paused`. Restrict selection with a D-Bus name substring:

```yaml
media:
  player: spotify
```

List available MPRIS players:

```bash
busctl --user list | grep org.mpris.MediaPlayer2
# or
gdbus call --session --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ListNames | grep -o "org\.mpris\.MediaPlayer2[^']*"
```

Multimedia buttons are treated as one press: repeated 127 messages within about
250 ms are ignored, and a 0 message resets the press state.

#### Live reload

The daemon checks the file every 0.5 seconds. A reload can also be forced with
`SIGHUP`:

```bash
systemctl --user reload pw-midi-volume.service
# or
kill -HUP "$(pgrep -f 'pw-midi-volume daemon')"
```

The GUI writes YAML atomically, so the daemon never sees a partially written
file. If the YAML is invalid, the daemon logs an error to the journal and keeps
the previous working bindings. Saving from the GUI canonicalizes the file, so
hand-written comments are not preserved.

### GUI

```bash
pw-midi-volume-gui
# start hidden in the tray
pw-midi-volume-gui --minimized
```

The GUI can:

* show available PipeWire sinks and MIDI inputs;
* add and remove bindings;
* select a sink or enter its `node.name` manually;
* choose an action: volume, mute by CC level, one of three mute-toggle modes,
  or a multimedia action (Play/Pause, Next, Rewind, etc.) — the sink field is
  disabled automatically for multimedia;
* configure CC, channel, minimum and maximum volume;
* **MIDI Learn**: click the button in a row, then move a control or press a
  button on the controller; the GUI fills in CC and channel;
* save changes to YAML (Ctrl+S) and reload the file (Ctrl+R);
* minimize or close the window to the system tray;
* work from the tray: open, save, reload, quit;
* display incoming MIDI messages in the bottom panel, including timestamp, port,
  channel, CC/note/program change/pitch bend;
* pause and clear the log without interrupting MIDI Learn;
* switch the interface language from a drop-down list built from files in the
  `locales` folder next to the configuration file.

The tray and window icon is bundled. If the Qt SVG plugin is unavailable, the
GUI draws a fallback icon programmatically.

#### MIDI log

The bottom panel shows a continuous log of incoming MIDI messages. It listens to
the MIDI input selected in the settings and detects new devices automatically.
Each line includes a millisecond timestamp, port name, channel and a readable
message description:

```text
12:34:56.123  Launch Control MIDI 1  ch 01  CC 7 = 64
12:34:56.300  Launch Control MIDI 1  ch 01  Note On C4 velocity 100
```

**Pause** stops only the log output; MIDI Learn keeps working. **Clear** empties
the log. History is limited to 1000 lines.

### Localization

The GUI supports multiple languages. The language selector is in the top-right
corner of the settings window. The list is built dynamically from localization
files:

```text
<configuration directory>/locales/
```

By default:

```text
~/.config/pw-midi-volume/locales/
```

On first start, `en.yaml` (English) and `ru.yaml` (Russian) are installed there.
The selected language is saved to `gui.yaml` next to `config.yaml` and restored
on the next start.

Files with the `.yaml`, `.yml` and `.json` extensions are supported. After
adding or editing a file, click the **⟳** button next to the language selector;
a restart is not required.

#### Localization file format

```yaml
language: de
name: Deutsch

strings:
  window.title: "pw-midi-volume — MIDI-Einstellungen"
  app.subtitle: "Lautstärke und Mute für PipeWire-Sinks per MIDI."
  button.save: "Speichern"
  button.reload: "Neu laden"
  status.loaded: "Geladene Bindungen: {count}"
```

Required fields:

| Field | Description |
| --- | --- |
| `language` | Language code, for example `de`, `fr` or `pt-BR`. Saved to `gui.yaml`. |
| `name` | Display name in the language list, normally in the language itself: `Deutsch`, `Français`. |
| `strings` | Flat `key: translation` mapping. |

Rules:

* Files must be UTF-8.
* Do not translate or rename keys — translate only the values.
* Keep placeholders in curly braces (`{count}`, `{path}`, `{name}`, `{error}`).
* Missing keys fall back to English.
* To add a language, copy `en.yaml`, change `language` and `name`, translate the
  values, and place the file in the `locales` folder.
* If the `locales` folder is empty or contains no valid file, English and
  Russian are installed again.
* When the application is upgraded, new keys are added automatically to the
  installed `en.yaml` and `ru.yaml`; existing translations are preserved.

### systemd user service

Ready-to-use unit: [`contrib/pw-midi-volume.service`](contrib/pw-midi-volume.service).

```bash
cp contrib/pw-midi-volume.service ~/.config/systemd/user/
# update ExecStart to an absolute path if necessary
systemctl --user daemon-reload
systemctl --user enable --now pw-midi-volume.service
systemctl --user status pw-midi-volume.service
journalctl --user -u pw-midi-volume -f
```

The service runs in the user session and has access to the user's PipeWire and
MIDI devices. Root access is not required and not recommended.

### Diagnostics

```bash
# validate YAML without starting the daemon
pw-midi-volume check

# list sinks with PipeWire IDs
pw-midi-volume sinks

# list MIDI inputs
pw-midi-volume midi-ports

# verbose daemon log
pw-midi-volume --log-level DEBUG daemon
journalctl --user -u pw-midi-volume -f
```

Common causes:

* **`python-rtmidi is not available`** — the dependency is missing, or ALSA
  development headers were unavailable while building. Install
  `python-rtmidi` in the same environment as the daemon.
* **MIDI port is not visible** — check `pw-midi-volume midi-ports` and
  `aconnect -l`. The daemon listens to all ports, but `midi.port` can filter
  out the needed one.
* **Sink not found** — check `pw-midi-volume sinks`; the configuration must use
  the `node.name`, not the device description.
* **Volume does not change** — check `wpctl status`, the user's PipeWire
  permissions, and the log with `--log-level DEBUG`.
* **Multimedia actions do not work** — check that `gdbus` is installed and that
  an MPRIS player is visible in `busctl --user list`.

### Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

If the package is already installed in the environment (`pip install -e .`),
`python3 -m unittest discover -s tests -v` is sufficient.

### Project structure

```text
src/pw_midi_volume/
  config.py       YAML model, validation, atomic writes, file watcher
  pipewire.py     pw-dump/wpctl, sink cache, volume and mute
  midi.py         rtmidi, hot-plug, MIDI monitor and MIDI Learn
  i18n.py         locale discovery, translations, GUI language selection
  engine.py       CC routing, volume/mute queue
  media.py        MPRIS players, Play/Pause/Next/Seek actions
  daemon.py       service main loop and SIGHUP
  gui.py          PySide6 GUI, system tray, language selection
  locales/        bundled English and Russian locales
  cli.py          CLI
contrib/          systemd unit and desktop entries
scripts/          per-user installation and uninstallation
tests/            unit tests
```

### Limitations

* Controls overall sink volume and mute (all channels), not individual channels.
* Absolute CC only; relative encoders are not supported.
* Multimedia uses MPRIS and requires `gdbus`; not every player supports relative
  `Seek` for rewind/fast-forward.
* Requires `wpctl` from WirePlumber; PulseAudio is not a target platform.

---

<a id="russian"></a>
## Русский

Управление громкостью и mute-состоянием **PipeWire sinks**, а также мультимедиа-плеером по MPRIS с MIDI-контроллера.

Проект состоит из двух приложений:

* `pw-midi-volume` — фоновый демон. Слушает MIDI CC, меняет громкость и включает/выключает mute выбранных sink через `wpctl`, а также управляет мультимедиа-плеером через MPRIS. Может работать как systemd user service.
* `pw-midi-volume-gui` — GUI на PySide6 для настройки привязок MIDI CC к sink и мультимедиа-действиям. Умеет работать в системном трее и сохраняет всё в тот же YAML-файл.

Демон автоматически перечитывает YAML при изменении (интервал проверки — 0,5 секунды) и по `SIGHUP`. Если в файле временно оказалась ошибка, продолжает работать с последней корректной конфигурацией.

### Возможности

* Абсолютные MIDI Control Change (CC 0–127).
* Действия привязки: громкость, Mute/Unmute по уровню CC, переключение mute кнопкой.
* Управление мультимедиа через MPRIS: Play/Pause, Play, Pause, Stop, следующий/предыдущий трек, перемотка назад/вперёд.
* Любой канал MIDI или конкретный канал 1–16.
* Один CC может управлять несколькими sink одновременно.
* Несколько привязок на один sink с разными CC.
* Настраиваемый диапазон громкости в процентах для volume-привязок.
* Фильтр MIDI-входов: все устройства или часть имени конкретного порта.
* Горячее подключение MIDI-устройств.
* MIDI Learn в GUI.
* Сворачивание GUI в системный трей (кнопка минимизации и закрытие окна).
* Встроенный MIDI-монитор: входящие CC, Note On/Off, Program Change, Pitch Bend
  и другие сообщения с временем, портом и каналом.
* Многоязычный интерфейс с выбором языка в окне настроек; English и Русский
  входят в поставку.
* Готовая иконка приложения, desktop entry и systemd user unit.

### Требования

* Linux с PipeWire и WirePlumber (`wpctl`, `pw-dump`).
* Python 3.10+.
* MIDI-контроллер, доступный через ALSA sequencer.
* Для мультимедиа: MPRIS-совместимый плеер и утилита `gdbus` (пакет GLib).
* Для GUI: PySide6.

Проверить наличие PipeWire-инструментов:

```bash
wpctl status
pw-dump >/dev/null
```

### Быстрая установка

Скрипт создаёт отдельное виртуальное окружение, устанавливает пакет, иконку,
.desktop-файл и systemd user unit:

```bash
./scripts/install-user.sh
systemctl --user enable --now pw-midi-volume.service
pw-midi-volume-gui
```

Скрипт не требует root. По умолчанию всё ставится в `~/.local` и
`~/.config`.

#### Автозапуск GUI в трее

```bash
mkdir -p ~/.config/autostart
sed "s|^Exec=.*|Exec=$HOME/.local/bin/pw-midi-volume-gui --minimized|" \
    contrib/pw-midi-volume-gui-autostart.desktop \
    > ~/.config/autostart/pw-midi-volume-gui.desktop
```

### Удаление

Скрипт удаления останавливает сервис и запущенные процессы, удаляет
виртуальное окружение, ярлыки, systemd unit, desktop entry, иконку и запись
автозапуска. YAML-конфигурация по умолчанию сохраняется.

```bash
# удалить приложение, оставить конфигурацию
./scripts/uninstall-user.sh

# удалить приложение вместе с конфигурацией
./scripts/uninstall-user.sh --purge

# без интерактивного подтверждения
./scripts/uninstall-user.sh --yes
```

Если конфигурация была сохранена, её можно удалить вручную:

```bash
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/pw-midi-volume"
```

### Ручная установка для разработки

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[gui]'

pw-midi-volume check
pw-midi-volume midi-ports
pw-midi-volume sinks
pw-midi-volume daemon
```

Для запуска демона в фоне:

```bash
systemctl --user enable --now pw-midi-volume.service
journalctl --user -u pw-midi-volume -f
```

При ручной установке поправьте `ExecStart` в `contrib/pw-midi-volume.service`
на абсолютный путь к `pw-midi-volume`, затем скопируйте unit в
`~/.config/systemd/user/` и выполните `systemctl --user daemon-reload`.

### Конфигурация

Файл по умолчанию:

```text
~/.config/pw-midi-volume/config.yaml
```

Он создаётся автоматически при первом запуске демона или GUI. Путь можно
переопределить параметром `--config` у обоих приложений.

Пример:

```yaml
version: 1

midi:
  # null — все MIDI-входы.
  # Иначе подстрока имени порта без учёта регистра, например "Launch Control".
  port: null

media:
  # null — автоматически выбирать активный MPRIS-плеер.
  # Иначе подстрока D-Bus имени, например "spotify" или "chromium".
  player: null
  # Шаг перемотки для media_rewind и media_fast_forward, секунды.
  seek_seconds: 10

bindings:
  - sink: "alsa_output.pci-0000_00_1f.3.analog-stereo"
    action: volume
    cc: 7
    channel: 0          # 0–15; null — любой канал
    min_percent: 0
    max_percent: 100
    enabled: true

  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: volume
    cc: 7
    channel: 0
    min_percent: 0
    max_percent: 80
    enabled: true

  # Кнопка mute: CC < 64 — звук включён, CC >= 64 — mute.
  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: mute
    cc: 20
    channel: 0
    enabled: true

  # Кнопка mute: переключение на каждое нажатие.
  # mute_mode: press — одно переключение на нажатие; повторы 127 в пределах ~250 мс считаются одним нажатием.
  #            any   — кнопка чередует 127 и 0.
  #            edge  — переключение только на переход 0–63 → 64–127.
  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    action: mute_toggle
    mute_mode: press
    cc: 21
    channel: 0
    enabled: true

  # Мультимедиа: не требует sink, управляет активным MPRIS-плеером.
  - action: media_play_pause
    cc: 50
    channel: 0
    enabled: true

  - action: media_next
    cc: 51
    channel: 0
    enabled: true

  - action: media_rewind
    cc: 52
    channel: 0
    enabled: true
```

Поля:

| Поле | Описание |
| --- | --- |
| `sink` | `node.name` PipeWire sink. Обязателен для volume/mute, не используется для мультимедиа. |
| `action` | `volume`, `mute`, `mute_toggle` или одно из мультимедиа-действий. Необязательное, по умолчанию `volume`. |
| `mute_mode` | Режим для `action: mute_toggle`: `press`, `any` или `edge`. По умолчанию `press`. |
| `cc` | Номер Control Change, 0–127. |
| `channel` | MIDI-канал в конфиге: 0–15. В GUI каналы показаны как 1–16. `null` — любой. |
| `min_percent` / `max_percent` | Диапазон громкости для `action: volume`, соответствующий значениям CC 0 и 127. |
| `enabled` | Включает или временно отключает привязку. |
| `midi.port` | `null` — все входы; иначе подстрока имени MIDI-порта. |
| `media.player` | `null` — автоматически выбирать активный MPRIS-плеер; иначе подстрока D-Bus имени. |
| `media.seek_seconds` | Шаг перемотки для `media_rewind` / `media_fast_forward`, от 1 до 600 секунд. |

Если поле `action` отсутствует (например, в конфиге старой версии), используется
`volume`.

Значение MIDI CC отображается в громкость линейно. Например, `CC=64` при
диапазоне 0–100% даёт примерно 50%. Диапазон задаётся от меньшего значения к
большему; обратное направление (высокий CC — тише) пока не поддерживается.

Действия для mute:

* `action: mute` — значение CC ниже 64 включает звук, 64 и выше выключает
  (mute). Подходит для фейдеров, абсолютных регуляторов и кнопок, которые
  передают состояние, а не импульс.
* `action: mute_toggle` — переключает mute кнопкой. Режим задаётся полем
  `mute_mode`:
  * `press` — одно переключение на нажатие (CC `>= 64`). Повторные 127 в
    пределах примерно 250 мс считаются одним нажатием. Подходит, когда
    нажатие отправляет 127, а отпускание не отправляет ничего **или**
    отправляет 0. Это режим по умолчанию.
  * `any` — переключение на **каждое** сообщение CC. Подходит для кнопок,
    которые поочерёдно отправляют 127 и 0 при последовательных нажатиях.
  * `edge` — переключение только при переходе из 0–63 в 64–127. Защищает от
    повторных 127 при удержании, но требует, чтобы отпускание присылало 0.

Один и тот же поток `127, 0` может означать «нажатие и отпускание» или «два
последовательных нажатия», поэтому режим выбирается явно, а не угадывается.

Соответствие поведениям кнопок:

| Поведение кнопки | Режим |
| --- | --- |
| Нажатие → 127, отпускание → ничего | `press` или `any` |
| Нажатие → 127, отпускание → 0 | `press` или `edge` |
| Нажатия поочерёдно → 127, 0, 127, 0 | `any` |

`mute` и `mute_toggle` используют `wpctl set-mute`, поэтому состояние mute
меняется для всего sink (все каналы).

Поддерживаются именно абсолютные CC. Если регулятор MIDI-контроллера
настроен как relative/endless, переключите его в absolute mode средствами
контроллера.

#### Мультимедиа (MPRIS)

Мультимедиа-привязки не используют поле `sink`:

* `media_play_pause` — Play/Pause;
* `media_play` — Play;
* `media_pause` — Pause;
* `media_stop` — Stop;
* `media_next` — следующий трек;
* `media_previous` — предыдущий трек;
* `media_rewind` — перемотка назад на `media.seek_seconds`;
* `media_fast_forward` — перемотка вперёд на `media.seek_seconds`.

Для управления используется стандартный D-Bus-интерфейс MPRIS. Если поле
`media.player` равно `null`, выбирается активный плеер: сначала со статусом
`Playing`, затем `Paused`. Можно ограничить выбор подстрокой D-Bus имени,
например:

```yaml
media:
  player: spotify
```

Посмотреть доступные MPRIS-плееры:

```bash
busctl --user list | grep org.mpris.MediaPlayer2
# или
gdbus call --session --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ListNames | grep -o "org\.mpris\.MediaPlayer2[^']*"
```

Кнопки мультимедиа обрабатываются как одно нажатие: повторные `127` в пределах
примерно 250 мс игнорируются, а сообщение `0` сбрасывает состояние нажатия.

#### Горячая перезагрузка

Демон проверяет файл каждые 0,5 секунды. Дополнительно можно отправить
`SIGHUP`:

```bash
systemctl --user reload pw-midi-volume.service
# или
kill -HUP "$(pgrep -f 'pw-midi-volume daemon')"
```

GUI сохраняет YAML атомарно, поэтому демон никогда не увидит частично
записанный файл. Если YAML некорректен, демон пишет ошибку в journal и
сохраняет предыдущие рабочие привязки. При сохранении из GUI файл приводится
к каноническому виду, поэтому ручные комментарии в нём не сохраняются.

### GUI

```bash
pw-midi-volume-gui
# запустить сразу скрытым в трее
pw-midi-volume-gui --minimized
```

Что умеет GUI:

* Показывать список доступных PipeWire sinks и MIDI-входов.
* Добавлять и удалять привязки.
* Выбирать sink (или ввести `node.name` вручную).
* Выбирать действие: громкость, mute по уровню CC, один из трёх режимов
  mute-переключения или мультимедиа-действие (Play/Pause, Next, Rewind и т.д.).
  Для мультимедиа поле sink автоматически отключается.
* Настраивать CC, канал, минимальную и максимальную громкость.
* **MIDI Learn**: нажмите кнопку в строке, затем подвиньте регулятор или
  нажмите кнопку на контроллере. GUI подставит CC и канал.
* Сохранять изменения в YAML (Ctrl+S) и перечитывать файл (Ctrl+R).
* Сворачиваться в системный трей по кнопке минимизации или при закрытии окна.
* Работать из трея: открыть, сохранить, перечитать, выйти.
* Показывать лог входящих MIDI-сообщений в нижней панели: время, порт,
  канал, CC/нота/программное изменение/pitch bend.
* Приостанавливать и очищать лог, не прерывая MIDI Learn.
* Переключать язык интерфейса через выпадающий список. Список строится по
  файлам в папке `locales` рядом с конфигом.

Иконка трея и окна встроена в пакет. Если SVG-плагин Qt недоступен, GUI
рисует запасную иконку программно.

#### Лог MIDI

В нижней части окна отображается непрерывный лог входящих MIDI-сообщений.
Он слушает тот же MIDI-вход, что выбран в настройках, и автоматически
подхватывает новые устройства. Лог показывает время с точностью до
миллисекунд, имя порта, канал и расшифровку сообщения, например:

```text
12:34:56.123  Launch Control MIDI 1  ch 01  CC 7 = 64
12:34:56.300  Launch Control MIDI 1  ch 01  Note On C4 velocity 100
```

Кнопка **«Пауза»** приостанавливает только вывод в окно; MIDI Learn при этом
продолжает работать. Кнопка **«Очистить»** очищает лог. История ограничена
1000 строками.

### Локализация

Интерфейс GUI поддерживает несколько языков. Выпадающий список языков
находится в правом верхнем углу окна настроек. Список строится динамически по
файлам локализаций:

```text
<папка конфигурации>/locales/
```

По умолчанию это:

```text
~/.config/pw-midi-volume/locales/
```

При первом запуске туда устанавливаются `en.yaml` (English) и `ru.yaml`
(Русский). Выбранный язык сохраняется в `gui.yaml` рядом с основным
`config.yaml` и восстанавливается при следующем запуске.

Поддерживаются файлы `.yaml`, `.yml` и `.json`. После добавления или изменения
файла нажмите кнопку **⟳** рядом с выбором языка; перезапуск не обязателен.

#### Формат файла локализации

```yaml
language: de
name: Deutsch

strings:
  window.title: "pw-midi-volume — MIDI-Einstellungen"
  app.subtitle: "Lautstärke und Mute für PipeWire-Sinks per MIDI."
  button.save: "Speichern"
  button.reload: "Neu laden"
  status.loaded: "Geladene Bindungen: {count}"
```

Обязательные поля:

| Поле | Описание |
| --- | --- |
| `language` | Код языка, например `de`, `fr` или `pt-BR`. Он сохраняется в `gui.yaml`. |
| `name` | Название языка в выпадающем списке, обычно на самом языке: `Deutsch`, `Français`. |
| `strings` | Плоский словарь `ключ: перевод`. |

Правила:

* Файл должен быть в кодировке UTF-8.
* Ключи нельзя переводить или переименовывать — переводите только значения.
* Плейсхолдеры в фигурных скобках (`{count}`, `{path}`, `{name}`, `{error}`)
  нужно сохранять.
* Если ключ отсутствует в выбранном языке, используется английский вариант.
* Для нового языка скопируйте `en.yaml`, измените `language` и `name`,
  переведите значения и положите файл в папку `locales`.
* Если папка `locales` пуста или не содержит ни одного корректного файла,
  English и Русский будут установлены заново.
* При обновлении приложения новые ключи автоматически добавляются в
  установленные `en.yaml` и `ru.yaml`; существующие переводы сохраняются.

### systemd user service

Готовый unit: [`contrib/pw-midi-volume.service`](contrib/pw-midi-volume.service).

```bash
cp contrib/pw-midi-volume.service ~/.config/systemd/user/
# при необходимости исправьте ExecStart на абсолютный путь
systemctl --user daemon-reload
systemctl --user enable --now pw-midi-volume.service
systemctl --user status pw-midi-volume.service
journalctl --user -u pw-midi-volume -f
```

Служба запускается в пользовательской сессии и имеет доступ к PipeWire и
MIDI-устройствам пользователя. Root не нужен и не рекомендуется.

### Диагностика

```bash
# Проверить YAML без запуска демона
pw-midi-volume check

# Список sink с PipeWire ID
pw-midi-volume sinks

# Список MIDI-входов
pw-midi-volume midi-ports

# Подробный лог демона
pw-midi-volume --log-level DEBUG daemon
journalctl --user -u pw-midi-volume -f
```

Частые причины:

* **`python-rtmidi is not available`** — не установлена зависимость или
  отсутствуют ALSA-заголовки при сборке. Установите `python-rtmidi` в то же
  окружение, где запущен демон.
* **MIDI-порт не виден** — проверьте `pw-midi-volume midi-ports` и
  `aconnect -l`. Демон слушает все порты, но фильтр `midi.port` может
  исключать нужный.
* **Sink не найден** — проверьте `pw-midi-volume sinks`; в конфиге должен быть
  именно `node.name`, а не описание устройства.
* **Громкость не меняется** — проверьте `wpctl status`, права пользователя на
  PipeWire и лог с `--log-level DEBUG`.

### Тесты

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Если пакет уже установлен в окружение (`pip install -e .`), достаточно
`python3 -m unittest discover -s tests -v`.

### Структура проекта

```text
src/pw_midi_volume/
  config.py       YAML-модель, валидация, атомарная запись, watcher
  pipewire.py     pw-dump/wpctl, кэш списка sink, громкость и mute
  midi.py         rtmidi, hot-plug, MIDI-монитор и MIDI Learn
  i18n.py         поиск локалей, переводы, выбор языка GUI
  engine.py       маршрутизация CC, очередь громкости и mute
  media.py        MPRIS-плееры, действия Play/Pause/Next/Seek
  daemon.py       основной цикл сервиса и SIGHUP
  gui.py          PySide6 GUI + system tray + выбор языка
  locales/        встроенные English и Русский
  cli.py          CLI
contrib/          systemd unit и desktop entries
scripts/          установка и удаление для пользователя
tests/            unit-тесты
```

### Ограничения

* Управляется общая громкость и mute sink (все каналы), а не отдельные каналы.
* Используются абсолютные CC; relative-энкодеры не поддерживаются.
* Мультимедиа работает через MPRIS и требует `gdbus`; не все плееры
  поддерживают относительный `Seek` для перемотки.
* Для работы нужен `wpctl` из WirePlumber; PulseAudio не является целевой
  платформой.
