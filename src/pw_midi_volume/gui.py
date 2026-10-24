"""PySide6 configuration GUI with system tray support."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    AppConfig,
    Binding,
    ConfigError,
    MidiConfig,
    default_config_path,
    ensure_config,
    load_config,
    save_config,
)
from .midi import MidiSniffer, MidiUnavailable, list_input_ports
from .pipewire import PipeWireError, Sink, list_sinks

LOG = logging.getLogger(__name__)

COL_SINK = 0
COL_CC = 1
COL_CHANNEL = 2
COL_MIN = 3
COL_MAX = 4
COL_ENABLED = 5
COL_LEARN = 6
COL_DELETE = 7


def _painted_icon() -> QIcon:
    """Draw a fallback icon so the GUI works without installed assets."""

    icon = QIcon()
    for size in (16, 22, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = size / 128.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#172033"))
        painter.drawRoundedRect(
            QRectF(4 * scale, 4 * scale, 120 * scale, 120 * scale),
            26 * scale,
            26 * scale,
        )

        speaker = QPainterPath()
        speaker.moveTo(29 * scale, 51 * scale)
        speaker.lineTo(43 * scale, 51 * scale)
        speaker.lineTo(61 * scale, 33 * scale)
        speaker.lineTo(61 * scale, 95 * scale)
        speaker.lineTo(43 * scale, 77 * scale)
        speaker.lineTo(29 * scale, 77 * scale)
        speaker.closeSubpath()
        painter.setBrush(QColor("#f8fafc"))
        painter.drawPath(speaker)

        pen = QPen(QColor("#38bdf8"))
        pen.setWidthF(max(1.5, 7 * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(
            QRectF(55 * scale, 43 * scale, 27 * scale, 42 * scale),
            -60 * 16,
            120 * 16,
        )
        painter.drawArc(
            QRectF(54 * scale, 32 * scale, 49 * scale, 64 * scale),
            -55 * 16,
            110 * 16,
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def app_icon() -> QIcon:
    """Load the packaged SVG icon, falling back to a painted icon."""

    try:
        resource = files("pw_midi_volume").joinpath("assets/pw-midi-volume.svg")
        if resource.is_file():
            icon = QIcon(str(resource))
            if not icon.isNull():
                return icon
    except Exception:  # the icon must never prevent startup
        LOG.debug("cannot load SVG icon", exc_info=True)
    return _painted_icon()


def _combo_index_for_data(combo: QComboBox, value: Any) -> int:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            return index
    return -1


class MainWindow(QMainWindow):
    """Edit MIDI CC -> sink bindings and watch the tray lifecycle."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        super().__init__()
        self.config_path = Path(config_path or default_config_path()).expanduser()
        self._icon = app_icon()
        self._sinks: list[Sink] = []
        self._dirty = False
        self._suspend_dirty = False
        self._learn_sniffer: MidiSniffer | None = None
        self._learn_row: int | None = None
        self._learn_button: QPushButton | None = None
        self._learn_scan_ticks = 0
        self._tray_hint_shown = False
        self._force_quit = False
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle("pw-midi-volume — MIDI-настройки")
        self.setWindowIcon(self._icon)
        self.setMinimumSize(980, 520)
        self.resize(1120, 650)

        self._learn_poll_timer = QTimer(self)
        self._learn_poll_timer.setInterval(50)
        self._learn_poll_timer.timeout.connect(self._poll_learn)

        self._learn_timeout_timer = QTimer(self)
        self._learn_timeout_timer.setSingleShot(True)
        self._learn_timeout_timer.timeout.connect(self._learn_timeout)

        self._build_ui()
        self._setup_shortcuts()
        self._setup_tray()

        self._refresh_sinks(notify=False)
        self._refresh_midi_ports(notify=False)
        self._load_config(initial=True)
        self.statusBar().showMessage(
            f"Выберите MIDI-вход и привязки. Файл настроек: {self.config_path}",
            8000,
        )

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(12)

        title = QLabel("MIDI → PipeWire")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel(
            "Громкость audio-sink по MIDI Control Change. "
            "Изменения сохраняются в YAML и подхватываются демоном автоматически."
        )
        subtitle.setWordWrap(True)

        config_label = QLabel(str(self.config_path))
        config_label.setToolTip(str(self.config_path))
        config_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        config_font = config_label.font()
        config_font.setPointSize(max(8, config_font.pointSize() - 1))
        config_label.setFont(config_font)
        config_label.setWordWrap(True)

        outer.addWidget(title)
        outer.addWidget(subtitle)
        outer.addWidget(config_label)

        midi_row = QHBoxLayout()
        midi_row.setSpacing(8)
        midi_row.addWidget(QLabel("MIDI-вход:"))
        self._midi_combo = QComboBox()
        self._midi_combo.setMinimumWidth(360)
        self._midi_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._midi_combo.currentIndexChanged.connect(self._on_midi_filter_changed)
        midi_row.addWidget(self._midi_combo, 1)
        self._refresh_midi_button = QPushButton("Обновить MIDI")
        self._refresh_midi_button.setToolTip("Перечитать список MIDI-устройств")
        self._refresh_midi_button.clicked.connect(
            lambda: self._refresh_midi_ports(notify=True)
        )
        midi_row.addWidget(self._refresh_midi_button)

        self._refresh_sinks_button = QPushButton("Обновить sinks")
        self._refresh_sinks_button.setToolTip("Перечитать список PipeWire audio-sink")
        self._refresh_sinks_button.clicked.connect(
            lambda: self._refresh_sinks(notify=True)
        )
        midi_row.addWidget(self._refresh_sinks_button)
        outer.addLayout(midi_row)

        group = QGroupBox("Привязки")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 14, 10, 10)
        group_layout.setSpacing(8)

        self._table = QTableWidget(0, 8, self)
        self._table.setHorizontalHeaderLabels(
            [
                "Sink (PipeWire)",
                "CC",
                "Канал",
                "Мин.",
                "Макс.",
                "Вкл.",
                "",
                "",
            ]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setCornerButtonEnabled(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_SINK, QHeaderView.ResizeMode.Stretch)
        for column in (
            COL_CC,
            COL_CHANNEL,
            COL_MIN,
            COL_MAX,
            COL_ENABLED,
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_LEARN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_DELETE, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_LEARN, 106)
        self._table.setColumnWidth(COL_DELETE, 38)
        group_layout.addWidget(self._table)

        button_row = QHBoxLayout()
        self._add_button = QPushButton("Добавить привязку")
        self._add_button.clicked.connect(self._add_binding)
        button_row.addWidget(self._add_button)

        self._cancel_learn_button = QPushButton("Отменить обучение")
        self._cancel_learn_button.setVisible(False)
        self._cancel_learn_button.clicked.connect(self._cancel_learn)
        button_row.addWidget(self._cancel_learn_button)

        hint = QLabel(
            "MIDI Learn слушает выбранный вход и подставляет номер CC и канал "
            "в соответствующую строку."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        button_row.addSpacing(12)
        button_row.addWidget(hint, 1)

        self._reload_button = QPushButton("Перечитать")
        self._reload_button.setToolTip("Перечитать YAML с диска (Ctrl+R)")
        self._reload_button.clicked.connect(self._reload_config)
        button_row.addWidget(self._reload_button)

        self._save_button = QPushButton("Сохранить")
        self._save_button.setToolTip("Сохранить YAML (Ctrl+S)")
        self._save_button.clicked.connect(self._save)
        button_row.addWidget(self._save_button)
        group_layout.addLayout(button_row)

        outer.addWidget(group, 1)
        self.setCentralWidget(central)

    def _setup_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+S", self._save),
            ("Ctrl+R", self._reload_config),
            ("Ctrl+Q", self._quit),
            ("Escape", self._cancel_learn),
        ]
        for sequence, slot in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    # --------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._tray: QSystemTrayIcon | None = None
        if not self._tray_available:
            LOG.warning("system tray is not available")
            return

        tray = QSystemTrayIcon(self._icon, self)
        tray.setToolTip("pw-midi-volume — MIDI → PipeWire")

        menu = QMenu(self)
        show_action = QAction("Открыть настройки", self)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)

        save_action = QAction("Сохранить", self)
        save_action.triggered.connect(self._save)
        menu.addAction(save_action)

        reload_action = QAction("Перечитать конфигурацию", self)
        reload_action.triggered.connect(self._reload_config)
        menu.addAction(reload_action)

        menu.addSeparator()
        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

    @property
    def tray_available(self) -> bool:
        return self._tray_available

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_hidden(self) -> None:
        if self._tray is not None:
            self._hide_to_tray(show_hint=False)

    def _hide_to_tray(self, *, show_hint: bool = True) -> None:
        if self._tray is None:
            return
        self.hide()
        if show_hint and not self._tray_hint_shown:
            self._tray.showMessage(
                "pw-midi-volume",
                "Приложение свёрнуто в системный трей. "
                "Двойной клик по значку откроет настройки.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )
            self._tray_hint_shown = True

    def changeEvent(self, event: QEvent) -> None:  # Qt API naming
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._tray is not None
        ):
            QTimer.singleShot(0, self._hide_to_tray)
        super().changeEvent(event)

    def closeEvent(self, event: Any) -> None:  # Qt API naming
        if self._force_quit:
            event.accept()
            return
        if self._tray is not None:
            event.ignore()
            self._hide_to_tray()
            return
        if not self._confirm_unsaved():
            event.ignore()
            return
        event.accept()

    # -------------------------------------------------------------- config
    def _load_config(self, *, initial: bool = False) -> bool:
        try:
            ensure_config(self.config_path)
            config = load_config(self.config_path)
        except ConfigError as exc:
            if initial:
                QMessageBox.warning(
                    self,
                    "Ошибка конфигурации",
                    f"{exc}\n\n"
                    "Приложение запущено с пустыми настройками. "
                    "Исправьте файл вручную или сохраните новые настройки.",
                )
                config = AppConfig()
            else:
                QMessageBox.critical(self, "Ошибка конфигурации", str(exc))
                return False

        self._config = config
        self._suspend_dirty = True
        try:
            self._set_midi_filter(config.midi.port)
            self._table.setRowCount(0)
            for binding in config.bindings:
                self._append_binding(binding)
        finally:
            self._suspend_dirty = False

        self._dirty = False
        self._update_title()
        self.statusBar().showMessage(
            f"Загружено привязок: {len(config.bindings)}", 4000
        )
        return True

    def _reload_config(self) -> None:
        if self._learn_sniffer is not None:
            self._cancel_learn()
        had_unsaved_changes = self._dirty
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Перечитать конфигурацию?",
                "Несохранённые изменения будут потеряны. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._dirty = False
        if self._load_config(initial=False):
            self._refresh_sinks(notify=False)
            self._refresh_midi_ports(notify=False)
            self.statusBar().showMessage("Конфигурация перечитана", 4000)
        elif had_unsaved_changes:
            # The on-disk file is broken. The table still contains the old
            # data, so keep it marked as unsaved instead of pretending it is
            # in sync with the file.
            self._dirty = True
            self._update_title()

    def _save(self) -> bool:
        if self._learn_sniffer is not None:
            self._cancel_learn()
        try:
            config = self._collect_config()
            save_config(self.config_path, config)
        except (ValueError, ConfigError) as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))
            return False

        self._config = config
        self._dirty = False
        self._update_title()
        self._notify("Настройки сохранены. Демон перечитает файл автоматически.")
        return True

    def _collect_config(self) -> AppConfig:
        bindings: list[Binding] = []
        seen: set[tuple[str, int, int | None]] = set()

        for row in range(self._table.rowCount()):
            sink_combo = self._table.cellWidget(row, COL_SINK)
            cc_spin = self._table.cellWidget(row, COL_CC)
            channel_combo = self._table.cellWidget(row, COL_CHANNEL)
            min_spin = self._table.cellWidget(row, COL_MIN)
            max_spin = self._table.cellWidget(row, COL_MAX)
            enabled_box = self._table.cellWidget(row, COL_ENABLED)
            if not all(
                (
                    isinstance(sink_combo, QComboBox),
                    isinstance(cc_spin, QSpinBox),
                    isinstance(channel_combo, QComboBox),
                    isinstance(min_spin, QSpinBox),
                    isinstance(max_spin, QSpinBox),
                )
            ) or not isinstance(enabled_box, QCheckBox):
                continue

            sink = self._sink_value(sink_combo)
            if not sink:
                raise ValueError(
                    f"Строка {row + 1}: выберите sink или введите его node.name."
                )
            cc = cc_spin.value()
            channel = channel_combo.currentData()
            if channel is not None and not isinstance(channel, int):
                channel = None
            min_percent = float(min_spin.value())
            max_percent = float(max_spin.value())
            if min_percent > max_percent:
                raise ValueError(
                    f"Строка {row + 1}: минимальная громкость больше максимальной."
                )

            key = (sink, cc, channel)
            if key in seen:
                raise ValueError(
                    f"Строка {row + 1}: такая привязка уже существует "
                    f"(sink, CC {cc}, канал)."
                )
            seen.add(key)

            bindings.append(
                Binding(
                    sink=sink,
                    cc=cc,
                    channel=channel,
                    min_percent=min_percent,
                    max_percent=max_percent,
                    enabled=enabled_box.isChecked(),
                )
            )

        port = self._midi_combo.currentData()
        if not isinstance(port, str) or not port:
            port = None
        return AppConfig(midi=MidiConfig(port=port), bindings=tuple(bindings))

    def _update_title(self) -> None:
        marker = " *" if self._dirty else ""
        self.setWindowTitle(f"pw-midi-volume — MIDI-настройки{marker}")

    def _mark_dirty(self, *_args: Any) -> None:
        if self._suspend_dirty or self._dirty:
            return
        self._dirty = True
        self._update_title()

    # --------------------------------------------------------------- sinks
    def _refresh_sinks(self, *, notify: bool) -> None:
        try:
            self._sinks = list_sinks()
        except PipeWireError as exc:
            if notify:
                QMessageBox.critical(self, "PipeWire недоступен", str(exc))
            else:
                self.statusBar().showMessage(f"PipeWire недоступен: {exc}", 8000)
            return

        self._suspend_dirty = True
        try:
            for row in range(self._table.rowCount()):
                combo = self._table.cellWidget(row, COL_SINK)
                if isinstance(combo, QComboBox):
                    current = self._sink_value(combo) or ""
                    self._populate_sink_combo(combo, current)
        finally:
            self._suspend_dirty = False

        if notify:
            self.statusBar().showMessage(
                f"Найдено PipeWire sinks: {len(self._sinks)}", 4000
            )

    def _populate_sink_combo(self, combo: QComboBox, current: str) -> None:
        combo.blockSignals(True)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.blockSignals(True)
        try:
            combo.clear()
            for sink in self._sinks:
                combo.addItem(sink.display_name, sink.name)
                combo.setItemData(
                    combo.count() - 1,
                    sink.name,
                    Qt.ItemDataRole.ToolTipRole,
                )

            index = _combo_index_for_data(combo, current) if current else -1
            if current and index < 0:
                combo.addItem(f"{current} (не найден)", current)
                index = combo.count() - 1

            if index >= 0:
                combo.setCurrentIndex(index)
            elif combo.count():
                combo.setCurrentIndex(0)
            elif line_edit is not None:
                combo.setEditText("")
        finally:
            if line_edit is not None:
                line_edit.blockSignals(False)
            combo.blockSignals(False)

    def _sink_value(self, combo: QComboBox) -> str:
        text = combo.currentText().strip()
        index = combo.currentIndex()
        # An editable QComboBox keeps currentData() of the previously selected
        # item even after the user types custom text. Compare the visible text
        # with the item text to tell selection and manual input apart.
        if index >= 0 and text == combo.itemText(index):
            data = combo.itemData(index)
            if isinstance(data, str) and data:
                return data
        if not text:
            return ""
        for sink in self._sinks:
            if text in (sink.name, sink.display_name):
                return sink.name
        return text

    # --------------------------------------------------------------- MIDI
    def _refresh_midi_ports(self, *, notify: bool) -> None:
        current = self._midi_combo.currentData()
        if not isinstance(current, str) or not current:
            current = None
        try:
            ports = list_input_ports()
        except MidiUnavailable as exc:
            ports = []
            if notify:
                QMessageBox.critical(self, "MIDI недоступен", str(exc))
            else:
                self.statusBar().showMessage(f"MIDI недоступен: {exc}", 8000)

        self._suspend_dirty = True
        self._midi_combo.blockSignals(True)
        try:
            self._midi_combo.clear()
            self._midi_combo.addItem("Все MIDI-входы", None)
            for port in ports:
                self._midi_combo.addItem(port, port)
            if current and current not in ports:
                self._midi_combo.addItem(f"{current} (не найден)", current)

            index = _combo_index_for_data(self._midi_combo, current)
            self._midi_combo.setCurrentIndex(max(index, 0))
        finally:
            self._midi_combo.blockSignals(False)
            self._suspend_dirty = False

        if notify:
            self.statusBar().showMessage(f"Найдено MIDI-входов: {len(ports)}", 4000)

    def _set_midi_filter(self, port: str | None) -> None:
        index = _combo_index_for_data(self._midi_combo, port)
        if index < 0 and port:
            self._midi_combo.addItem(f"{port} (не найден)", port)
            index = self._midi_combo.count() - 1
        index = max(index, 0)
        self._midi_combo.blockSignals(True)
        try:
            self._midi_combo.setCurrentIndex(index)
        finally:
            self._midi_combo.blockSignals(False)

    def _on_midi_filter_changed(self) -> None:
        if self._suspend_dirty:
            return
        self._mark_dirty()
        port = self._midi_combo.currentData()
        self.statusBar().showMessage(f"MIDI-вход: {port or 'все доступные'}", 3000)

    # -------------------------------------------------------------- table
    @staticmethod
    def _channel_combo(channel: int | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Все", None)
        for value in range(16):
            combo.addItem(str(value + 1), value)
        index = _combo_index_for_data(combo, channel)
        combo.setCurrentIndex(max(index, 0))
        return combo

    @staticmethod
    def _percent_spin(value: float) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" %")
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setValue(max(0, min(100, round(value))))
        return spin

    def _append_binding(self, binding: Binding) -> None:
        previous_suspend = self._suspend_dirty
        self._suspend_dirty = True
        try:
            row = self._table.rowCount()
            self._table.insertRow(row)

            sink_combo = QComboBox()
            sink_combo.setEditable(True)
            sink_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            sink_combo.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            sink_combo.setToolTip(
                "Sink PipeWire. Можно выбрать из списка или ввести node.name вручную."
            )
            self._populate_sink_combo(sink_combo, binding.sink)
            sink_combo.currentTextChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_SINK, sink_combo)

            cc_spin = QSpinBox()
            cc_spin.setRange(0, 127)
            cc_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cc_spin.setValue(binding.cc)
            cc_spin.valueChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_CC, cc_spin)

            channel_combo = self._channel_combo(binding.channel)
            channel_combo.currentIndexChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_CHANNEL, channel_combo)

            min_spin = self._percent_spin(binding.min_percent)
            min_spin.valueChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_MIN, min_spin)

            max_spin = self._percent_spin(binding.max_percent)
            max_spin.valueChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_MAX, max_spin)

            enabled_holder = QWidget()
            enabled_layout = QHBoxLayout(enabled_holder)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            enabled_box = QCheckBox()
            enabled_box.setChecked(binding.enabled)
            enabled_box.setToolTip("Привязка активна")
            enabled_box.toggled.connect(self._mark_dirty)
            enabled_layout.addWidget(
                enabled_box, alignment=Qt.AlignmentFlag.AlignCenter
            )
            self._table.setCellWidget(row, COL_ENABLED, enabled_box)

            learn_button = QPushButton("MIDI Learn")
            learn_button.setToolTip(
                "Нажмите, затем подвиньте нужный регулятор на MIDI-контроллере."
            )
            learn_button.clicked.connect(
                lambda _checked=False, button=learn_button: self._start_learn(button)
            )
            self._table.setCellWidget(row, COL_LEARN, learn_button)

            delete_button = QPushButton("✕")
            delete_button.setToolTip("Удалить привязку")
            delete_button.setFixedWidth(30)
            delete_button.clicked.connect(
                lambda _checked=False, button=delete_button: self._delete_row(button)
            )
            self._table.setCellWidget(row, COL_DELETE, delete_button)
        finally:
            self._suspend_dirty = previous_suspend

    def _add_binding(self) -> None:
        default_sink = self._sinks[0].name if self._sinks else ""
        binding = Binding(
            sink=default_sink,
            cc=7,
            channel=None,
            min_percent=0,
            max_percent=100,
            enabled=True,
        )
        self._append_binding(binding)
        self._mark_dirty()
        row = self._table.rowCount() - 1
        self._table.setCurrentCell(row, COL_CC)
        self.statusBar().showMessage(
            "Привязка добавлена. Настройте CC/канал или используйте MIDI Learn.",
            5000,
        )

    def _row_for_widget(self, widget: QWidget) -> int:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, COL_DELETE) is widget:
                return row
            if self._table.cellWidget(row, COL_LEARN) is widget:
                return row
        return -1

    def _delete_row(self, button: QWidget) -> None:
        row = self._row_for_widget(button)
        if row < 0:
            return
        self._table.removeRow(row)
        self._mark_dirty()
        self.statusBar().showMessage("Привязка удалена", 3000)

    # -------------------------------------------------------------- learn
    def _start_learn(self, button: QPushButton) -> None:
        row = self._row_for_widget(button)
        if row < 0 or self._learn_sniffer is not None:
            return

        port = self._midi_combo.currentData()
        port_filter = port if isinstance(port, str) and port else None
        sniffer = MidiSniffer(port_filter)
        try:
            sniffer.start()
        except MidiUnavailable as exc:
            QMessageBox.critical(self, "MIDI недоступен", str(exc))
            return

        self._learn_sniffer = sniffer
        self._learn_row = row
        self._learn_button = button
        self._learn_scan_ticks = 0
        button.setText("Слушаю…")
        self._set_learning_controls(True)
        self._learn_poll_timer.start()
        self._learn_timeout_timer.start(20000)
        self.statusBar().showMessage(
            "Ожидание MIDI CC… Подвиньте регулятор. Esc — отмена.",
            20000,
        )

    def _set_learning_controls(self, learning: bool) -> None:
        self._add_button.setEnabled(not learning)
        self._cancel_learn_button.setVisible(learning)
        for row in range(self._table.rowCount()):
            learn = self._table.cellWidget(row, COL_LEARN)
            delete = self._table.cellWidget(row, COL_DELETE)
            if isinstance(learn, QPushButton):
                learn.setEnabled(not learning)
                if not learning:
                    learn.setText("MIDI Learn")
            if isinstance(delete, QPushButton):
                delete.setEnabled(not learning)
        if not learning:
            self._learn_button = None

    def _poll_learn(self) -> None:
        sniffer = self._learn_sniffer
        row = self._learn_row
        if sniffer is None or row is None:
            return
        self._learn_scan_ticks += 1
        if self._learn_scan_ticks >= 20:
            self._learn_scan_ticks = 0
            try:
                sniffer.scan()
            except MidiUnavailable as exc:
                self._stop_learn()
                self.statusBar().showMessage(f"MIDI недоступен: {exc}", 6000)
                return

        captured = sniffer.poll()
        if captured is None:
            return

        cc_spin = self._table.cellWidget(row, COL_CC)
        channel_combo = self._table.cellWidget(row, COL_CHANNEL)
        if isinstance(cc_spin, QSpinBox):
            cc_spin.setValue(captured.cc)
        if isinstance(channel_combo, QComboBox):
            index = _combo_index_for_data(channel_combo, captured.channel)
            if index >= 0:
                channel_combo.setCurrentIndex(index)

        self._stop_learn()
        self._mark_dirty()
        self.statusBar().showMessage(
            f"Получен CC {captured.cc}, канал {captured.channel + 1} "
            f"({captured.port}). Не забудьте сохранить.",
            10000,
        )

    def _cancel_learn(self) -> None:
        if self._learn_sniffer is None:
            return
        self._stop_learn()
        self.statusBar().showMessage("Обучение отменено", 3000)

    def _learn_timeout(self) -> None:
        if self._learn_sniffer is None:
            return
        self._stop_learn()
        self.statusBar().showMessage(
            "Время ожидания MIDI CC истекло. Попробуйте ещё раз.", 5000
        )

    def _stop_learn(self) -> None:
        self._learn_poll_timer.stop()
        self._learn_timeout_timer.stop()
        sniffer, self._learn_sniffer = self._learn_sniffer, None
        if sniffer is not None:
            sniffer.stop()
        self._learn_row = None
        self._set_learning_controls(False)

    # --------------------------------------------------------------- misc
    def _notify(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        if self._tray is not None and not self.isVisible():
            self._tray.showMessage(
                "pw-midi-volume",
                message,
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    def _confirm_unsaved(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "Сохранить изменения перед выходом?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        return answer == QMessageBox.StandardButton.Discard

    def _quit(self) -> None:
        if self._learn_sniffer is not None:
            self._cancel_learn()
        if not self._confirm_unsaved():
            return
        self._force_quit = True
        if self._tray is not None:
            self._tray.hide()
        QApplication.quit()


def main(argv: list[str] | None = None) -> int:
    """GUI entry point."""

    parser = argparse.ArgumentParser(
        prog="pw-midi-volume-gui",
        description="Configure pw-midi-volume MIDI CC bindings.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="start hidden in the system tray",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    app = QApplication([sys.argv[0]])
    app.setApplicationName("pw-midi-volume")
    app.setApplicationDisplayName("pw-midi-volume")
    app.setOrganizationName("pw-midi-volume")
    icon = app_icon()
    app.setWindowIcon(icon)

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    if tray_available:
        app.setQuitOnLastWindowClosed(False)

    window = MainWindow(args.config)
    if args.minimized and window.tray_available:
        window.start_hidden()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
