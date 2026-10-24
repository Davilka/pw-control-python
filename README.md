# pw-midi-volume

Управление громкостью и mute-состоянием **PipeWire sinks**, а также мультимедиа-плеером по MPRIS с MIDI-контроллера.

Проект состоит из двух приложений:

* `pw-midi-volume` — фоновый демон. Слушает MIDI CC, меняет громкость и включает/выключает mute выбранных sink через `wpctl`, а также управляет мультимедиа-плеером через MPRIS. Может работать как systemd user service.
* `pw-midi-volume-gui` — GUI на PySide6 для настройки привязок MIDI CC к sink и мультимедиа-действиям. Умеет работать в системном трее и сохраняет всё в тот же YAML-файл.

Демон автоматически перечитывает YAML при изменении (интервал проверки — 0,5 секунды) и по `SIGHUP`. Если в файле временно оказалась ошибка, продолжает работать с последней корректной конфигурацией.

## Возможности

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

## Требования

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

## Быстрая установка

Скрипт создаёт отдельное виртуальное окружение, устанавливает пакет, иконку,
.desktop-файл и systemd user unit:

```bash
./scripts/install-user.sh
systemctl --user enable --now pw-midi-volume.service
pw-midi-volume-gui
```

Скрипт не требует root. По умолчанию всё ставится в `~/.local` и
`~/.config`.

### Автозапуск GUI в трее

```bash
mkdir -p ~/.config/autostart
sed "s|^Exec=.*|Exec=$HOME/.local/bin/pw-midi-volume-gui --minimized|" \
    contrib/pw-midi-volume-gui-autostart.desktop \
    > ~/.config/autostart/pw-midi-volume-gui.desktop
```

## Удаление

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

## Ручная установка для разработки

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

## Конфигурация

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

### Мультимедиа (MPRIS)

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

### Горячая перезагрузка

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

## GUI

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

### Лог MIDI

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

## Локализация

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

### Формат файла локализации

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

## systemd user service

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

## Диагностика

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

## Тесты

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Если пакет уже установлен в окружение (`pip install -e .`), достаточно
`python3 -m unittest discover -s tests -v`.

## Структура проекта

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

## Ограничения

* Управляется общая громкость и mute sink (все каналы), а не отдельные каналы.
* Используются абсолютные CC; relative-энкодеры не поддерживаются.
* Мультимедиа работает через MPRIS и требует `gdbus`; не все плееры
  поддерживают относительный `Seek` для перемотки.
* Для работы нужен `wpctl` из WirePlumber; PulseAudio не является целевой
  платформой.
