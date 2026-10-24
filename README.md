# pw-midi-volume

Управление громкостью **PipeWire sinks** с MIDI-контроллера.

Проект состоит из двух приложений:

* `pw-midi-volume` — фоновый демон. Слушает MIDI CC и меняет громкость выбранных sink через `wpctl`. Может работать как systemd user service.
* `pw-midi-volume-gui` — GUI на PySide6 для настройки привязок «MIDI CC → sink». Умеет работать в системном трее и сохраняет всё в тот же YAML-файл.

Демон автоматически перечитывает YAML при изменении (интервал проверки — 0,5 секунды) и по `SIGHUP`. Если в файле временно оказалась ошибка, продолжает работать с последней корректной конфигурацией.

## Возможности

* Абсолютные MIDI Control Change (CC 0–127).
* Любой канал MIDI или конкретный канал 1–16.
* Один CC может управлять несколькими sink одновременно.
* Несколько привязок на один sink с разными CC.
* Настраиваемый диапазон громкости в процентах для каждой привязки.
* Фильтр MIDI-входов: все устройства или часть имени конкретного порта.
* Горячее подключение MIDI-устройств.
* MIDI Learn в GUI.
* Сворачивание GUI в системный трей (кнопка минимизации и закрытие окна).
* Готовая иконка приложения, desktop entry и systemd user unit.

## Требования

* Linux с PipeWire и WirePlumber (`wpctl`, `pw-dump`).
* Python 3.10+.
* MIDI-контроллер, доступный через ALSA sequencer.
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

bindings:
  - sink: "alsa_output.pci-0000_00_1f.3.analog-stereo"
    cc: 7
    channel: 0          # 0–15; null — любой канал
    min_percent: 0
    max_percent: 100
    enabled: true

  - sink: "alsa_output.usb-Logitech_G933-00.analog-stereo"
    cc: 7
    channel: 0
    min_percent: 0
    max_percent: 80
    enabled: true
```

Поля:

| Поле | Описание |
| --- | --- |
| `sink` | `node.name` PipeWire sink. GUI подставляет его автоматически. |
| `cc` | Номер Control Change, 0–127. |
| `channel` | MIDI-канал в конфиге: 0–15. В GUI каналы показаны как 1–16. `null` — любой. |
| `min_percent` / `max_percent` | Диапазон громкости, соответствующий значениям CC 0 и 127. |
| `enabled` | Включает или временно отключает привязку. |
| `midi.port` | `null` — все входы; иначе подстрока имени MIDI-порта. |

Значение MIDI CC отображается в громкость линейно. Например, `CC=64` при
диапазоне 0–100% даёт примерно 50%. Диапазон задаётся от меньшего значения к
большему; обратное направление (высокий CC — тише) пока не поддерживается.

Поддерживаются именно абсолютные CC. Если регулятор MIDI-контроллера
настроен как relative/endless, переключите его в absolute mode средствами
контроллера.

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
* Настраивать CC, канал, минимальную и максимальную громкость.
* **MIDI Learn**: нажмите кнопку в строке, затем подвиньте нужный регулятор.
  GUI подставит CC и канал.
* Сохранять изменения в YAML (Ctrl+S) и перечитывать файл (Ctrl+R).
* Сворачиваться в системный трей по кнопке минимизации или при закрытии окна.
* Работать из трея: открыть, сохранить, перечитать, выйти.

Иконка трея и окна встроена в пакет. Если SVG-плагин Qt недоступен, GUI
рисует запасную иконку программно.

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
  pipewire.py     pw-dump/wpctl, кэш списка sink, установка громкости
  midi.py         rtmidi, hot-plug, MIDI Learn sniffer
  engine.py       маршрутизация CC и очередь обновлений громкости
  daemon.py       основной цикл сервиса и SIGHUP
  gui.py          PySide6 GUI + system tray
  cli.py          CLI
contrib/          systemd unit и desktop entries
scripts/          установка для пользователя
tests/            unit-тесты
```

## Ограничения

* Управляется общая громкость sink (все каналы), а не отдельные каналы.
* Используются абсолютные CC; relative-энкодеры не поддерживаются.
* Для работы нужен `wpctl` из WirePlumber; PulseAudio не является целевой
  платформой.
