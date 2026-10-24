"""PySide6 configuration GUI with system tray support."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFontDatabase,
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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    ACTION_MEDIA_FAST_FORWARD,
    ACTION_MEDIA_NEXT,
    ACTION_MEDIA_PAUSE,
    ACTION_MEDIA_PLAY,
    ACTION_MEDIA_PLAY_PAUSE,
    ACTION_MEDIA_PREVIOUS,
    ACTION_MEDIA_REWIND,
    ACTION_MEDIA_STOP,
    ACTION_MUTE,
    ACTION_MUTE_TOGGLE,
    ACTION_VOLUME,
    DEFAULT_MUTE_MODE,
    MEDIA_ACTIONS,
    MUTE_MODE_ANY,
    MUTE_MODE_EDGE,
    MUTE_MODE_PRESS,
    MUTE_MODES,
    AppConfig,
    Binding,
    ConfigError,
    MidiConfig,
    default_config_path,
    ensure_config,
    load_config,
    save_config,
)
from .i18n import (
    LocaleManager,
    Translator,
    load_language_preference,
    save_language_preference,
)
from .midi import (
    MidiEvent,
    MidiEventStream,
    MidiUnavailable,
    describe_midi_message,
    list_input_ports,
    parse_cc,
)
from .pipewire import PipeWireError, Sink, list_sinks

LOG = logging.getLogger(__name__)

COL_SINK = 0
COL_ACTION = 1
COL_CC = 2
COL_CHANNEL = 3
COL_MIN = 4
COL_MAX = 5
COL_ENABLED = 6
COL_LEARN = 7
COL_DELETE = 8

CHOICE_VOLUME = "volume"
CHOICE_MUTE = "mute"
CHOICE_TOGGLE_PRESS = "mute_toggle:press"
CHOICE_TOGGLE_ANY = "mute_toggle:any"
CHOICE_TOGGLE_EDGE = "mute_toggle:edge"
CHOICE_MEDIA_PLAY_PAUSE = ACTION_MEDIA_PLAY_PAUSE
CHOICE_MEDIA_PLAY = ACTION_MEDIA_PLAY
CHOICE_MEDIA_PAUSE = ACTION_MEDIA_PAUSE
CHOICE_MEDIA_STOP = ACTION_MEDIA_STOP
CHOICE_MEDIA_NEXT = ACTION_MEDIA_NEXT
CHOICE_MEDIA_PREVIOUS = ACTION_MEDIA_PREVIOUS
CHOICE_MEDIA_REWIND = ACTION_MEDIA_REWIND
CHOICE_MEDIA_FAST_FORWARD = ACTION_MEDIA_FAST_FORWARD

ACTION_CHOICES = (
    (CHOICE_VOLUME, "action.volume"),
    (CHOICE_MUTE, "action.mute"),
    (CHOICE_TOGGLE_PRESS, "action.toggle_press"),
    (CHOICE_TOGGLE_ANY, "action.toggle_any"),
    (CHOICE_TOGGLE_EDGE, "action.toggle_edge"),
    (CHOICE_MEDIA_PLAY_PAUSE, "action.media_play_pause"),
    (CHOICE_MEDIA_PLAY, "action.media_play"),
    (CHOICE_MEDIA_PAUSE, "action.media_pause"),
    (CHOICE_MEDIA_STOP, "action.media_stop"),
    (CHOICE_MEDIA_NEXT, "action.media_next"),
    (CHOICE_MEDIA_PREVIOUS, "action.media_previous"),
    (CHOICE_MEDIA_REWIND, "action.media_rewind"),
    (CHOICE_MEDIA_FAST_FORWARD, "action.media_fast_forward"),
)

ACTION_CHOICE_KEYS = dict(ACTION_CHOICES)

CHOICE_TO_BINDING = {
    CHOICE_VOLUME: (ACTION_VOLUME, DEFAULT_MUTE_MODE),
    CHOICE_MUTE: (ACTION_MUTE, DEFAULT_MUTE_MODE),
    CHOICE_TOGGLE_PRESS: (ACTION_MUTE_TOGGLE, MUTE_MODE_PRESS),
    CHOICE_TOGGLE_ANY: (ACTION_MUTE_TOGGLE, MUTE_MODE_ANY),
    CHOICE_TOGGLE_EDGE: (ACTION_MUTE_TOGGLE, MUTE_MODE_EDGE),
    CHOICE_MEDIA_PLAY_PAUSE: (ACTION_MEDIA_PLAY_PAUSE, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_PLAY: (ACTION_MEDIA_PLAY, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_PAUSE: (ACTION_MEDIA_PAUSE, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_STOP: (ACTION_MEDIA_STOP, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_NEXT: (ACTION_MEDIA_NEXT, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_PREVIOUS: (ACTION_MEDIA_PREVIOUS, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_REWIND: (ACTION_MEDIA_REWIND, DEFAULT_MUTE_MODE),
    CHOICE_MEDIA_FAST_FORWARD: (
        ACTION_MEDIA_FAST_FORWARD,
        DEFAULT_MUTE_MODE,
    ),
}


def _choice_for_binding(action: str, mute_mode: str) -> str:
    if action == ACTION_VOLUME:
        return CHOICE_VOLUME
    if action == ACTION_MUTE:
        return CHOICE_MUTE
    if action in MEDIA_ACTIONS:
        return action
    mode = mute_mode if mute_mode in MUTE_MODES else DEFAULT_MUTE_MODE
    return f"{ACTION_MUTE_TOGGLE}:{mode}"


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
        self._gui_settings_path = self.config_path.parent / "gui.yaml"
        self._locale_manager = LocaleManager(self.config_path.parent / "locales")
        self._locale_manager.refresh()
        preferred_language = load_language_preference(self._gui_settings_path)
        self._language_code = self._locale_manager.choose_initial_code(
            preferred_language
        )
        self._translator: Translator = self._locale_manager.translator(
            self._language_code
        )
        self._language_code = self._translator.code
        self._language_change_guard = False

        self._icon = app_icon()
        self._sinks: list[Sink] = []
        self._dirty = False
        self._suspend_dirty = False

        self._midi_stream: MidiEventStream | None = None
        self._midi_monitor_ports: list[str] = []
        self._midi_ports: list[str] = []
        self._midi_monitor_error: str | None = None
        self._midi_scan_ticks = 0
        self._midi_scan_error_logged = False
        self._midi_log_paused = False

        self._learn_row: int | None = None
        self._learn_button: QPushButton | None = None
        self._tray_hint_shown = False
        self._force_quit = False
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle(self._t("window.title"))
        self.setWindowIcon(self._icon)
        self.setMinimumSize(980, 620)
        self.resize(1180, 800)

        self._midi_poll_timer = QTimer(self)
        self._midi_poll_timer.setInterval(50)
        self._midi_poll_timer.timeout.connect(self._poll_midi)

        self._learn_timeout_timer = QTimer(self)
        self._learn_timeout_timer.setSingleShot(True)
        self._learn_timeout_timer.timeout.connect(self._learn_timeout)

        self._build_ui()
        self._setup_shortcuts()
        self._setup_tray()

        self._refresh_sinks(notify=False)
        self._refresh_midi_ports(notify=False)
        self._load_config(initial=True)
        self._start_midi_monitor()
        self._midi_poll_timer.start()
        self.statusBar().showMessage(
            self._t("status.startup", path=self.config_path),
            8000,
        )

    def _t(self, key: str, **values: Any) -> str:
        return self._translator(key, **values)

    # ------------------------------------------------------------ language
    def _populate_language_combo(self) -> None:
        self._language_change_guard = True
        self._language_combo.blockSignals(True)
        try:
            self._language_combo.clear()
            if self._locale_manager.available:
                for locale in self._locale_manager.available:
                    self._language_combo.addItem(
                        f"{locale.info.name} ({locale.info.code})",
                        locale.info.code,
                    )
            else:
                self._language_combo.addItem("English (en)", "en")
            index = _combo_index_for_data(self._language_combo, self._language_code)
            self._language_combo.setCurrentIndex(max(index, 0))
        finally:
            self._language_combo.blockSignals(False)
            self._language_change_guard = False

    def _on_language_changed(self, _index: int) -> None:
        if self._language_change_guard:
            return
        code = self._language_combo.currentData()
        if not isinstance(code, str) or not code or code == self._language_code:
            return

        self._apply_language(code, save=True)

    def _apply_language(self, code: str, *, save: bool) -> None:
        self._translator = self._locale_manager.translator(code)
        self._language_code = self._translator.code
        if save:
            save_language_preference(self._gui_settings_path, self._language_code)
        self._retranslate_ui()
        self.statusBar().showMessage(
            self._t("status.language_changed", name=self._translator.name),
            5000,
        )

    def _refresh_languages(self) -> None:
        current = self._language_code
        self._locale_manager.refresh()
        self._populate_language_combo()

        codes = {locale.info.code for locale in self._locale_manager.available}
        if current not in codes:
            current = self._locale_manager.choose_initial_code(current)
            self._apply_language(current, save=True)
        else:
            self._apply_language(current, save=False)

    def _retranslate_ui(self) -> None:
        self._suspend_dirty = True
        try:
            self._update_title()
            self._language_label.setText(self._t("language.label"))
            self._refresh_languages_button.setToolTip(
                self._t("tooltip.refresh_languages")
            )
            self._subtitle_label.setText(self._t("app.subtitle"))
            self._midi_input_label.setText(self._t("midi.input_label"))
            self._refresh_midi_button.setText(self._t("button.refresh_midi"))
            self._refresh_midi_button.setToolTip(self._t("tooltip.refresh_midi"))
            self._refresh_sinks_button.setText(self._t("button.refresh_sinks"))
            self._refresh_sinks_button.setToolTip(self._t("tooltip.refresh_sinks"))
            self._bindings_group.setTitle(self._t("group.bindings"))
            self._table.setHorizontalHeaderLabels(
                [
                    self._t("table.sink"),
                    self._t("table.action"),
                    self._t("table.cc"),
                    self._t("table.channel"),
                    self._t("table.min"),
                    self._t("table.max"),
                    self._t("table.enabled"),
                    "",
                    "",
                ]
            )
            self._add_button.setText(self._t("button.add_binding"))
            self._cancel_learn_button.setText(self._t("button.cancel_learn"))
            self._hint_label.setText(self._t("hint.learn"))
            self._reload_button.setText(self._t("button.reload"))
            self._reload_button.setToolTip(self._t("tooltip.reload"))
            self._save_button.setText(self._t("button.save"))
            self._save_button.setToolTip(self._t("tooltip.save"))
            self._log_group.setTitle(self._t("group.midi_log"))
            self._clear_log_button.setText(self._t("button.clear_log"))
            self._clear_log_button.setToolTip(self._t("tooltip.clear_log"))
            self._pause_log_button.setText(
                self._t("button.resume" if self._midi_log_paused else "button.pause")
            )
            self._pause_log_button.setToolTip(self._t("tooltip.pause_log"))
            self._midi_log.setPlaceholderText(self._t("log.placeholder"))
            self._retranslate_midi_combo()
            self._retranslate_rows()
            self._update_midi_monitor_status(announce=False)
            self._retranslate_tray()
        finally:
            self._suspend_dirty = False

    def _retranslate_midi_combo(self) -> None:
        self._midi_combo.blockSignals(True)
        try:
            for index in range(self._midi_combo.count()):
                data = self._midi_combo.itemData(index)
                if data is None:
                    text = self._t("midi.all_inputs")
                elif isinstance(data, str) and data in self._midi_ports:
                    text = data
                else:
                    text = self._t("common.not_found", name=str(data))
                self._midi_combo.setItemText(index, text)
        finally:
            self._midi_combo.blockSignals(False)

    def _retranslate_rows(self) -> None:
        for row in range(self._table.rowCount()):
            sink_combo = self._table.cellWidget(row, COL_SINK)
            action_combo = self._table.cellWidget(row, COL_ACTION)
            channel_combo = self._table.cellWidget(row, COL_CHANNEL)
            min_spin = self._table.cellWidget(row, COL_MIN)
            max_spin = self._table.cellWidget(row, COL_MAX)
            enabled_box = self._table.cellWidget(row, COL_ENABLED)
            learn_button = self._table.cellWidget(row, COL_LEARN)
            delete_button = self._table.cellWidget(row, COL_DELETE)

            if isinstance(sink_combo, QComboBox):
                current = self._sink_value(sink_combo) or ""
                sink_combo.setToolTip(self._t("tooltip.sink"))
                self._populate_sink_combo(sink_combo, current)

            if isinstance(action_combo, QComboBox):
                action_combo.blockSignals(True)
                try:
                    for index in range(action_combo.count()):
                        choice = action_combo.itemData(index)
                        key = ACTION_CHOICE_KEYS.get(choice)
                        if key:
                            action_combo.setItemText(index, self._t(key))
                finally:
                    action_combo.blockSignals(False)
                action_combo.setToolTip(self._t("tooltip.action"))

            if isinstance(channel_combo, QComboBox):
                channel_combo.blockSignals(True)
                try:
                    channel_combo.setItemText(0, self._t("channel.all"))
                finally:
                    channel_combo.blockSignals(False)

            binding_choice = (
                CHOICE_TO_BINDING.get(action_combo.currentData())
                if isinstance(action_combo, QComboBox)
                else (ACTION_VOLUME, DEFAULT_MUTE_MODE)
            )
            row_action = binding_choice[0] if binding_choice else ACTION_VOLUME
            is_volume = row_action == ACTION_VOLUME
            is_media = row_action in MEDIA_ACTIONS

            if isinstance(sink_combo, QComboBox):
                sink_combo.setEnabled(not is_media)
                if is_media:
                    sink_combo.blockSignals(True)
                    try:
                        sink_combo.setCurrentIndex(-1)
                        sink_combo.setEditText("")
                    finally:
                        sink_combo.blockSignals(False)
                    sink_combo.setToolTip(self._t("tooltip.not_used_for_multimedia"))
                else:
                    sink_combo.setToolTip(self._t("tooltip.sink"))

            if is_volume:
                range_key = "tooltip.volume_range"
            elif is_media:
                range_key = "tooltip.not_used_for_multimedia"
            else:
                range_key = "tooltip.not_used_for_mute"
            range_tooltip = self._t(range_key)
            if isinstance(min_spin, QSpinBox):
                min_spin.setToolTip(range_tooltip)
            if isinstance(max_spin, QSpinBox):
                max_spin.setToolTip(range_tooltip)
            if isinstance(enabled_box, QCheckBox):
                enabled_box.setToolTip(self._t("tooltip.binding_enabled"))
            if isinstance(learn_button, QPushButton):
                learn_button.setToolTip(self._t("learn.tooltip"))
                learn_button.setText(
                    self._t(
                        "learn.listening" if row == self._learn_row else "learn.button"
                    )
                )
            if isinstance(delete_button, QPushButton):
                delete_button.setToolTip(self._t("delete.tooltip"))

    def _retranslate_tray(self) -> None:
        if self._tray is None:
            return
        self._tray.setToolTip(self._t("tray.tooltip"))
        self._tray_show_action.setText(self._t("tray.show"))
        self._tray_save_action.setText(self._t("tray.save"))
        self._tray_reload_action.setText(self._t("tray.reload"))
        self._tray_quit_action.setText(self._t("tray.quit"))

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

        header_row = QHBoxLayout()
        header_row.addWidget(title)
        header_row.addStretch(1)

        self._language_label = QLabel(self._t("language.label"))
        header_row.addWidget(self._language_label)

        self._language_combo = QComboBox()
        self._language_combo.setMinimumWidth(190)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        header_row.addWidget(self._language_combo)

        self._refresh_languages_button = QPushButton("⟳")
        self._refresh_languages_button.setFixedWidth(30)
        self._refresh_languages_button.setToolTip(self._t("tooltip.refresh_languages"))
        self._refresh_languages_button.clicked.connect(self._refresh_languages)
        header_row.addWidget(self._refresh_languages_button)

        self._subtitle_label = QLabel(self._t("app.subtitle"))
        self._subtitle_label.setWordWrap(True)

        config_label = QLabel(str(self.config_path))
        config_label.setToolTip(str(self.config_path))
        config_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        config_font = config_label.font()
        config_font.setPointSize(max(8, config_font.pointSize() - 1))
        config_label.setFont(config_font)
        config_label.setWordWrap(True)

        outer.addLayout(header_row)
        outer.addWidget(self._subtitle_label)
        outer.addWidget(config_label)

        midi_row = QHBoxLayout()
        midi_row.setSpacing(8)
        self._midi_input_label = QLabel(self._t("midi.input_label"))
        midi_row.addWidget(self._midi_input_label)
        self._midi_combo = QComboBox()
        self._midi_combo.setMinimumWidth(360)
        self._midi_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._midi_combo.currentIndexChanged.connect(self._on_midi_filter_changed)
        midi_row.addWidget(self._midi_combo, 1)
        self._refresh_midi_button = QPushButton(self._t("button.refresh_midi"))
        self._refresh_midi_button.setToolTip(self._t("tooltip.refresh_midi"))
        self._refresh_midi_button.clicked.connect(
            lambda: self._refresh_midi_ports(notify=True)
        )
        midi_row.addWidget(self._refresh_midi_button)

        self._refresh_sinks_button = QPushButton(self._t("button.refresh_sinks"))
        self._refresh_sinks_button.setToolTip(self._t("tooltip.refresh_sinks"))
        self._refresh_sinks_button.clicked.connect(
            lambda: self._refresh_sinks(notify=True)
        )
        midi_row.addWidget(self._refresh_sinks_button)
        outer.addLayout(midi_row)

        self._bindings_group = QGroupBox(self._t("group.bindings"))
        group = self._bindings_group
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 14, 10, 10)
        group_layout.setSpacing(8)

        self._table = QTableWidget(0, 9, self)
        self._table.setHorizontalHeaderLabels(
            [
                self._t("table.sink"),
                self._t("table.action"),
                self._t("table.cc"),
                self._t("table.channel"),
                self._t("table.min"),
                self._t("table.max"),
                self._t("table.enabled"),
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
            COL_ACTION,
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
        self._add_button = QPushButton(self._t("button.add_binding"))
        self._add_button.clicked.connect(self._add_binding)
        button_row.addWidget(self._add_button)

        self._cancel_learn_button = QPushButton(self._t("button.cancel_learn"))
        self._cancel_learn_button.setVisible(False)
        self._cancel_learn_button.clicked.connect(self._cancel_learn)
        button_row.addWidget(self._cancel_learn_button)

        self._hint_label = QLabel(self._t("hint.learn"))
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: palette(mid);")
        button_row.addSpacing(12)
        button_row.addWidget(self._hint_label, 1)

        self._reload_button = QPushButton(self._t("button.reload"))
        self._reload_button.setToolTip(self._t("tooltip.reload"))
        self._reload_button.clicked.connect(self._reload_config)
        button_row.addWidget(self._reload_button)

        self._save_button = QPushButton(self._t("button.save"))
        self._save_button.setToolTip(self._t("tooltip.save"))
        self._save_button.clicked.connect(self._save)
        button_row.addWidget(self._save_button)
        group_layout.addLayout(button_row)

        self._log_group = QGroupBox(self._t("group.midi_log"))
        log_group = self._log_group
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(10, 14, 10, 10)
        log_layout.setSpacing(8)

        log_toolbar = QHBoxLayout()
        self._midi_log_status = QLabel(self._t("midi.monitor_starting"))
        self._midi_log_status.setWordWrap(True)
        log_toolbar.addWidget(self._midi_log_status, 1)

        self._clear_log_button = QPushButton(self._t("button.clear_log"))
        self._clear_log_button.setToolTip(self._t("tooltip.clear_log"))
        self._clear_log_button.clicked.connect(self._clear_midi_log)
        log_toolbar.addWidget(self._clear_log_button)

        self._pause_log_button = QPushButton(self._t("button.pause"))
        self._pause_log_button.setCheckable(True)
        self._pause_log_button.setToolTip(self._t("tooltip.pause_log"))
        self._pause_log_button.toggled.connect(self._set_midi_log_paused)
        log_toolbar.addWidget(self._pause_log_button)
        log_layout.addLayout(log_toolbar)

        self._midi_log = QPlainTextEdit()
        self._midi_log.setReadOnly(True)
        self._midi_log.setPlaceholderText(self._t("log.placeholder"))
        self._midi_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._midi_log.document().setMaximumBlockCount(1000)
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(max(8, fixed_font.pointSize()))
        self._midi_log.setFont(fixed_font)
        self._midi_log.setMinimumHeight(130)
        log_layout.addWidget(self._midi_log, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(group)
        splitter.addWidget(log_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([540, 210])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self._populate_language_combo()

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
        tray.setToolTip(self._t("tray.tooltip"))

        menu = QMenu(self)
        self._tray_show_action = QAction(self._t("tray.show"), self)
        self._tray_show_action.triggered.connect(self.show_window)
        menu.addAction(self._tray_show_action)

        self._tray_save_action = QAction(self._t("tray.save"), self)
        self._tray_save_action.triggered.connect(self._save)
        menu.addAction(self._tray_save_action)

        self._tray_reload_action = QAction(self._t("tray.reload"), self)
        self._tray_reload_action.triggered.connect(self._reload_config)
        menu.addAction(self._tray_reload_action)

        menu.addSeparator()
        self._tray_quit_action = QAction(self._t("tray.quit"), self)
        self._tray_quit_action.triggered.connect(self._quit)
        menu.addAction(self._tray_quit_action)

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
                self._t("tray.minimized"),
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
        self.shutdown()
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
                    self._t("dialog.config_error.title"),
                    self._t("dialog.config_error.initial", error=exc),
                )
                config = AppConfig()
            else:
                QMessageBox.critical(
                    self,
                    self._t("dialog.config_error.title"),
                    str(exc),
                )
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
            self._t("status.loaded", count=len(config.bindings)), 4000
        )
        return True

    def _reload_config(self) -> None:
        if self._learn_row is not None:
            self._cancel_learn()
        had_unsaved_changes = self._dirty
        if self._dirty:
            answer = QMessageBox.question(
                self,
                self._t("dialog.reload.title"),
                self._t("dialog.reload.message"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._dirty = False
        if self._load_config(initial=False):
            self._refresh_sinks(notify=False)
            self._refresh_midi_ports(notify=False)
            self.statusBar().showMessage(self._t("status.config_reloaded"), 4000)
        elif had_unsaved_changes:
            # The on-disk file is broken. The table still contains the old
            # data, so keep it marked as unsaved instead of pretending it is
            # in sync with the file.
            self._dirty = True
            self._update_title()

    def _save(self) -> bool:
        if self._learn_row is not None:
            self._cancel_learn()
        try:
            config = self._collect_config()
            save_config(self.config_path, config)
        except (ValueError, ConfigError) as exc:
            QMessageBox.critical(
                self,
                self._t("dialog.save_error.title"),
                str(exc),
            )
            return False

        self._config = config
        self._dirty = False
        self._update_title()
        self._notify(self._t("status.saved"))
        return True

    def _collect_config(self) -> AppConfig:
        bindings: list[Binding] = []
        seen: set[tuple[str, int, int | None]] = set()

        for row in range(self._table.rowCount()):
            sink_combo = self._table.cellWidget(row, COL_SINK)
            action_combo = self._table.cellWidget(row, COL_ACTION)
            cc_spin = self._table.cellWidget(row, COL_CC)
            channel_combo = self._table.cellWidget(row, COL_CHANNEL)
            min_spin = self._table.cellWidget(row, COL_MIN)
            max_spin = self._table.cellWidget(row, COL_MAX)
            enabled_box = self._table.cellWidget(row, COL_ENABLED)
            if not all(
                (
                    isinstance(sink_combo, QComboBox),
                    isinstance(action_combo, QComboBox),
                    isinstance(cc_spin, QSpinBox),
                    isinstance(channel_combo, QComboBox),
                    isinstance(min_spin, QSpinBox),
                    isinstance(max_spin, QSpinBox),
                )
            ) or not isinstance(enabled_box, QCheckBox):
                continue

            choice = action_combo.currentData()
            binding_action = CHOICE_TO_BINDING.get(choice)
            if binding_action is None:
                raise ValueError(self._t("error.row_action", row=row + 1))
            action, mute_mode = binding_action

            if action in MEDIA_ACTIONS:
                sink = ""
            else:
                sink = self._sink_value(sink_combo)
                if not sink:
                    raise ValueError(self._t("error.row_sink", row=row + 1))
            cc = cc_spin.value()
            channel = channel_combo.currentData()
            if channel is not None and not isinstance(channel, int):
                channel = None

            min_percent = 0.0
            max_percent = 100.0
            if action == ACTION_VOLUME:
                min_percent = float(min_spin.value())
                max_percent = float(max_spin.value())
                if min_percent > max_percent:
                    raise ValueError(self._t("error.row_range", row=row + 1))

            key = (sink, cc, channel)
            if key in seen:
                raise ValueError(
                    self._t(
                        "error.row_duplicate",
                        row=row + 1,
                        cc=cc,
                    )
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
                    action=action,
                    mute_mode=mute_mode,
                )
            )

        port = self._midi_combo.currentData()
        if not isinstance(port, str) or not port:
            port = None
        return AppConfig(
            midi=MidiConfig(port=port),
            media=self._config.media,
            bindings=tuple(bindings),
        )

    def _update_title(self) -> None:
        marker = " *" if self._dirty else ""
        self.setWindowTitle(f"{self._t('window.title')}{marker}")

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
                QMessageBox.critical(
                    self,
                    self._t("dialog.pipewire_error.title"),
                    str(exc),
                )
            else:
                self.statusBar().showMessage(
                    f"{self._t('dialog.pipewire_error.title')}: {exc}",
                    8000,
                )
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
                self._t("status.scan_sinks", count=len(self._sinks)),
                4000,
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
                combo.addItem(self._t("common.not_found", name=current), current)
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
                QMessageBox.critical(
                    self,
                    self._t("dialog.midi_error.title"),
                    str(exc),
                )
            else:
                self.statusBar().showMessage(
                    f"{self._t('dialog.midi_error.title')}: {exc}",
                    8000,
                )

        self._midi_ports = list(ports)
        self._suspend_dirty = True
        self._midi_combo.blockSignals(True)
        try:
            self._midi_combo.clear()
            self._midi_combo.addItem(self._t("midi.all_inputs"), None)
            for port in ports:
                self._midi_combo.addItem(port, port)
            if current and current not in ports:
                self._midi_combo.addItem(
                    self._t("common.not_found", name=current), current
                )

            index = _combo_index_for_data(self._midi_combo, current)
            self._midi_combo.setCurrentIndex(max(index, 0))
        finally:
            self._midi_combo.blockSignals(False)
            self._suspend_dirty = False

        if notify:
            self.statusBar().showMessage(
                self._t("status.scan_midi", count=len(ports)), 4000
            )

        if notify or self._midi_stream is not None:
            self._start_midi_monitor()

    def _set_midi_filter(self, port: str | None) -> None:
        index = _combo_index_for_data(self._midi_combo, port)
        if index < 0 and port:
            self._midi_combo.addItem(self._t("common.not_found", name=port), port)
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
        if self._learn_row is not None:
            self._cancel_learn()
        self._start_midi_monitor()
        port = self._midi_combo.currentData()
        source = port if isinstance(port, str) and port else self._t("midi.source_all")
        self.statusBar().showMessage(self._t("status.midi_input", source=source), 3000)

    # -------------------------------------------------------- MIDI monitor
    def _start_midi_monitor(self) -> None:
        if self._learn_row is not None:
            self._cancel_learn()
        self._stop_midi_monitor()
        self._midi_monitor_error = None

        port = self._midi_combo.currentData()
        port_filter = port if isinstance(port, str) and port else None
        stream = MidiEventStream(port_filter)
        try:
            stream.start()
        except MidiUnavailable as exc:
            self._midi_monitor_error = str(exc)
            self._midi_log_status.setText(self._t("midi.monitor_unavailable"))
            self._midi_log_status.setToolTip(str(exc))
            self._append_midi_log(self._t("log.monitor_not_started", error=exc))
            LOG.warning("MIDI monitor is unavailable: %s", exc)
            return

        self._midi_stream = stream
        self._midi_monitor_ports = []
        self._midi_scan_ticks = 0
        self._midi_scan_error_logged = False
        self._update_midi_monitor_status(announce=True)

    def _stop_midi_monitor(self) -> None:
        stream, self._midi_stream = self._midi_stream, None
        if stream is not None:
            stream.stop()
        self._midi_monitor_ports = []

    def _update_midi_monitor_status(self, *, announce: bool = False) -> None:
        stream = self._midi_stream
        if stream is None:
            if self._midi_monitor_error:
                self._midi_log_status.setText(self._t("midi.monitor_unavailable"))
                self._midi_log_status.setToolTip(self._midi_monitor_error)
            else:
                self._midi_log_status.setText(self._t("midi.monitor_stopped"))
                self._midi_log_status.setToolTip("")
            return

        ports = stream.open_port_names
        port = self._midi_combo.currentData()
        source = port if isinstance(port, str) and port else self._t("midi.source_all")
        if ports:
            self._midi_log_status.setText(
                self._t(
                    "midi.monitor_active",
                    count=len(ports),
                    source=source,
                )
            )
        else:
            self._midi_log_status.setText(
                self._t("midi.monitor_no_inputs", source=source)
            )
        self._midi_log_status.setToolTip(
            "\n".join(ports) if ports else self._t("midi.no_ports")
        )

        if ports != self._midi_monitor_ports:
            self._midi_monitor_ports = list(ports)
            if announce:
                names = ", ".join(ports) if ports else self._t("log.no_inputs")
                self._append_midi_log(self._t("log.monitor_started", names=names))

    def _poll_midi(self) -> None:
        stream = self._midi_stream
        if stream is None:
            return

        self._midi_scan_ticks += 1
        if self._midi_scan_ticks >= 20:
            self._midi_scan_ticks = 0
            try:
                stream.scan()
            except MidiUnavailable as exc:
                if not self._midi_scan_error_logged:
                    self._append_midi_log(self._t("log.scan_error", error=exc))
                    self._midi_scan_error_logged = True
                self._midi_log_status.setText(self._t("midi.monitor_error"))
                self._midi_log_status.setToolTip(str(exc))
            else:
                self._midi_scan_error_logged = False
                self._update_midi_monitor_status(announce=True)

        for _ in range(200):
            event = stream.poll()
            if event is None:
                break
            self._handle_midi_event(event)

    def _handle_midi_event(self, event: MidiEvent) -> None:
        if not self._midi_log_paused:
            timestamp = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[
                :-3
            ]
            self._append_midi_log(
                f"{timestamp}  {event.port}  {describe_midi_message(event.message)}"
            )

        if self._learn_row is not None:
            parsed = parse_cc(event.message)
            if parsed is not None:
                self._complete_learn(event, parsed)

    def _complete_learn(
        self,
        event: MidiEvent,
        parsed: tuple[int, int, int],
    ) -> None:
        row = self._learn_row
        if row is None:
            return
        channel, cc, _value = parsed
        cc_spin = self._table.cellWidget(row, COL_CC)
        channel_combo = self._table.cellWidget(row, COL_CHANNEL)
        if isinstance(cc_spin, QSpinBox):
            cc_spin.setValue(cc)
        if isinstance(channel_combo, QComboBox):
            index = _combo_index_for_data(channel_combo, channel)
            if index >= 0:
                channel_combo.setCurrentIndex(index)

        self._stop_learn()
        self._mark_dirty()
        self.statusBar().showMessage(
            self._t(
                "status.learn_captured",
                cc=cc,
                channel=channel + 1,
                port=event.port,
            ),
            10000,
        )

    def _append_midi_log(self, text: str) -> None:
        scrollbar = self._midi_log.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 2
        self._midi_log.appendPlainText(text)
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def _clear_midi_log(self) -> None:
        self._midi_log.clear()
        self.statusBar().showMessage(self._t("status.log_cleared"), 2500)

    def _set_midi_log_paused(self, paused: bool) -> None:
        self._midi_log_paused = paused
        self._pause_log_button.setText(
            self._t("button.resume" if paused else "button.pause")
        )

    # -------------------------------------------------------------- table
    def _action_combo(self, action: str, mute_mode: str) -> QComboBox:
        combo = QComboBox()
        for choice, key in ACTION_CHOICES:
            combo.addItem(self._t(key), choice)
        index = _combo_index_for_data(combo, _choice_for_binding(action, mute_mode))
        combo.setCurrentIndex(max(index, 0))
        combo.setToolTip(self._t("tooltip.action"))
        return combo

    def _channel_combo(self, channel: int | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem(self._t("channel.all"), None)
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
            sink_combo.setToolTip(self._t("tooltip.sink"))
            self._populate_sink_combo(sink_combo, binding.sink)
            sink_combo.currentTextChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_SINK, sink_combo)

            action_combo = self._action_combo(binding.action, binding.mute_mode)
            action_combo.currentIndexChanged.connect(self._mark_dirty)
            self._table.setCellWidget(row, COL_ACTION, action_combo)

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

            def update_volume_controls(*_args: object) -> None:
                binding_choice = CHOICE_TO_BINDING.get(action_combo.currentData())
                action = binding_choice[0] if binding_choice else ACTION_VOLUME
                is_volume = action == ACTION_VOLUME
                is_media = action in MEDIA_ACTIONS
                min_spin.setEnabled(is_volume)
                max_spin.setEnabled(is_volume)
                tooltip = self._t(
                    "tooltip.volume_range" if is_volume else "tooltip.not_used_for_mute"
                )
                min_spin.setToolTip(tooltip)
                max_spin.setToolTip(tooltip)

                sink_combo.setEnabled(not is_media)
                if is_media:
                    sink_combo.blockSignals(True)
                    try:
                        sink_combo.setCurrentIndex(-1)
                        sink_combo.setEditText("")
                    finally:
                        sink_combo.blockSignals(False)
                    sink_combo.setToolTip(self._t("tooltip.not_used_for_multimedia"))
                else:
                    sink_combo.setToolTip(self._t("tooltip.sink"))
                    if not self._sink_value(sink_combo):
                        self._populate_sink_combo(sink_combo, "")

            action_combo.currentIndexChanged.connect(update_volume_controls)
            update_volume_controls()

            enabled_holder = QWidget()
            enabled_layout = QHBoxLayout(enabled_holder)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            enabled_box = QCheckBox()
            enabled_box.setChecked(binding.enabled)
            enabled_box.setToolTip(self._t("tooltip.binding_enabled"))
            enabled_box.toggled.connect(self._mark_dirty)
            enabled_layout.addWidget(
                enabled_box, alignment=Qt.AlignmentFlag.AlignCenter
            )
            self._table.setCellWidget(row, COL_ENABLED, enabled_box)

            learn_button = QPushButton(self._t("learn.button"))
            learn_button.setToolTip(self._t("learn.tooltip"))
            learn_button.clicked.connect(
                lambda _checked=False, button=learn_button: self._start_learn(button)
            )
            self._table.setCellWidget(row, COL_LEARN, learn_button)

            delete_button = QPushButton("✕")
            delete_button.setToolTip(self._t("delete.tooltip"))
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
            action=ACTION_VOLUME,
        )
        self._append_binding(binding)
        self._mark_dirty()
        row = self._table.rowCount() - 1
        self._table.setCurrentCell(row, COL_CC)
        self.statusBar().showMessage(self._t("status.binding_added"), 5000)

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
        self.statusBar().showMessage(self._t("status.binding_deleted"), 3000)

    # -------------------------------------------------------------- learn
    def _start_learn(self, button: QPushButton) -> None:
        row = self._row_for_widget(button)
        if row < 0 or self._learn_row is not None:
            return

        if self._midi_stream is None:
            self._start_midi_monitor()
        if self._midi_stream is None:
            detail = self._midi_log_status.toolTip()
            QMessageBox.critical(
                self,
                self._t("dialog.midi_error.title"),
                detail or self._t("dialog.midi_unavailable_detail"),
            )
            return

        try:
            self._midi_stream.scan()
            self._update_midi_monitor_status(announce=True)
        except MidiUnavailable as exc:
            QMessageBox.critical(
                self,
                self._t("dialog.midi_error.title"),
                str(exc),
            )
            return

        self._learn_row = row
        self._learn_button = button
        button.setText(self._t("learn.listening"))
        self._set_learning_controls(True)
        self._learn_timeout_timer.start(20000)
        self.statusBar().showMessage(self._t("status.waiting_cc"), 20000)

    def _set_learning_controls(self, learning: bool) -> None:
        self._add_button.setEnabled(not learning)
        self._cancel_learn_button.setVisible(learning)
        for row in range(self._table.rowCount()):
            learn = self._table.cellWidget(row, COL_LEARN)
            delete = self._table.cellWidget(row, COL_DELETE)
            if isinstance(learn, QPushButton):
                learn.setEnabled(not learning)
                if not learning:
                    learn.setText(self._t("learn.button"))
            if isinstance(delete, QPushButton):
                delete.setEnabled(not learning)
        if not learning:
            self._learn_button = None

    def _cancel_learn(self) -> None:
        if self._learn_row is None:
            return
        self._stop_learn()
        self.statusBar().showMessage(self._t("status.learn_cancelled"), 3000)

    def _learn_timeout(self) -> None:
        if self._learn_row is None:
            return
        self._stop_learn()
        self.statusBar().showMessage(self._t("status.learn_timeout"), 5000)

    def _stop_learn(self) -> None:
        self._learn_timeout_timer.stop()
        self._learn_row = None
        self._set_learning_controls(False)

    # --------------------------------------------------------------- misc
    def shutdown(self) -> None:
        """Stop timers and close MIDI ports before exiting."""

        self._midi_poll_timer.stop()
        self._stop_learn()
        self._stop_midi_monitor()

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
            self._t("dialog.unsaved.title"),
            self._t("dialog.unsaved.message"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        return answer == QMessageBox.StandardButton.Discard

    def _quit(self) -> None:
        if self._learn_row is not None:
            self._cancel_learn()
        if not self._confirm_unsaved():
            return
        self._force_quit = True
        self.shutdown()
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
    app.aboutToQuit.connect(window.shutdown)
    if args.minimized and window.tray_available:
        window.start_hidden()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
