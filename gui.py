# -*- coding: utf-8 -*-
"""GUI портативного транскрибера (Faster-Whisper XXL).

Две вкладки:
  «Транскрибация»          — обёртка над transcriber.py
  «Файл для прослушивания» — обёртка над listening.py

Настройки сохраняются в settings.json рядом с программой.
"""

import json
import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
PARAMS_HELP_PATH = os.path.join(APP_DIR, "purfview FastWhisper parameters.txt")
MODELS_DIR = os.path.join(APP_DIR, "xxl", "_models")
FFPROBE = os.path.join(APP_DIR, "ffmpeg", "ffprobe.exe")
ICON_PATH = os.path.join(APP_DIR, "T.ico")

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QListWidget, QComboBox, QCheckBox,
    QPlainTextEdit, QFileDialog, QLineEdit, QMessageBox, QGroupBox, QSpinBox,
    QRadioButton, QDialog, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView,
)

# Встраиваемый Python не добавляет папку скрипта в sys.path сам
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
import converter as md_converter

AUDIO_EXTS = {".wav", ".mp3", ".opus", ".ogg", ".m4a", ".aac", ".flac", ".wma",
              ".webm", ".mp4", ".mkv", ".mka", ".avi", ".mov", ".amr", ".3gp",
              ".ts", ".wmv"}
TXT_EXTS = {".txt", ".srt", ".vtt", ".md"}

MODELS = ["large-v3-turbo", "large-v3"]
MODEL_SIZES = {"large-v3-turbo": "1.6 ГБ", "large-v3": "3 ГБ"}


# ── настройки ────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def probe_summary(path):
    """Короткая сводка по файлу: '2 дорожки (aac/1ch, aac/1ch)'."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
        streams = [s for s in json.loads(out or "{}").get("streams", [])
                   if s.get("codec_type") == "audio"]
        if not streams:
            return "нет аудио!"
        parts = []
        for s in streams:
            t = s.get("tags", {}).get("title", "")
            parts.append(f"{s.get('codec_name')}/{s.get('channels')}ch"
                         + (f" «{t}»" if t else ""))
        word = "дорожка" if len(streams) == 1 else "дорожки"
        return f"{len(streams)} {word}: " + ", ".join(parts)
    except Exception:
        return "?"


def model_label(name):
    on_disk = os.path.isdir(os.path.join(MODELS_DIR, f"faster-whisper-{name}"))
    return (f"whisper-{name} (на диске)" if on_disk
            else f"whisper-{name} (скачается, ~{MODEL_SIZES.get(name, '?')})")


def info_icon(text):
    """Маленькая буква i с подсказкой при наведении."""
    lbl = QLabel("ⓘ")
    lbl.setToolTip(text)
    lbl.setToolTipDuration(60000)
    lbl.setStyleSheet("color: #d9c27a; font-weight: bold; padding: 0 2px;")
    lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
    return lbl


TIP_MODE = (
    "Диалог (авто) — сам выбирает: 2+ аудиодорожки в файле -> режим «Дорожки»,\n"
    "    иначе -> «Диаризация».\n"
    "Дорожки (OBS) — каждая дорожка = свой спикер (запись из OBS).\n"
    "    Самый точный вариант: угадывать голоса не нужно.\n"
    "Стерео: разделить каналы — левый канал = один голос, правый = другой\n"
    "    (записи с рекордеров звонков).\n"
    "Диаризация — обычная запись (диктофон): кто говорит, алгоритм определяет\n"
    "    по тембрам голосов в процессе обработки. Точность на чистой записи\n"
    "    двух собеседников — обычно 95–98% реплик; ошибки чаще на коротких\n"
    "    «да»/«угу» и местах, где говорят одновременно. Если число говорящих\n"
    "    известно — обязательно укажите его в «Спикеров», это повышает точность.\n"
    "Без спикеров (монолог) — подкаст, лекция: только текст, без разметки,\n"
    "    быстрее (диаризация не запускается).")

TIP_TRACKS = (
    "Какие аудиодорожки брать в режиме «Дорожки».\n"
    "Все отмечены = все дорожки файла. Снимите лишние\n"
    "(например, дорожку с системными звуками).\n"
    "Номера сверх имеющихся в файле игнорируются.")

TIP_COMPUTE = (
    "Точность вычислений нейросети (квантизация).\n"
    "Компромисс «скорость/память ↔ качество». За эталон качества\n"
    "принят float32 = 100%.\n"
    "\n"
    "авто — int8_float32 на CPU, float16 на CUDA (рекомендуется).\n"
    "\n"
    "float32 — полная точность, эталон (100%). Самый медленный\n"
    "    и прожорливый по памяти. Практического смысла обычно нет.\n"
    "float16 — половинная точность, стандарт для GPU (~100%,\n"
    "    отличия от эталона на уровне случайности). На CPU медленный.\n"
    "int8_float16 — веса int8, расчёт в float16. Режим для видеокарт\n"
    "    с небольшим объёмом памяти (~4 ГБ): почти качество float16,\n"
    "    но заметно меньше VRAM. Только для CUDA.\n"
    "int8_float32 — веса сжаты до int8, расчёт в float32.\n"
    "    Лучший режим для CPU: ~99-100% качества, в ~2 раза быстрее\n"
    "    float32 и вдвое меньше памяти. На часовой записи отличия —\n"
    "    единичные слова, чаще их нет вовсе.\n"
    "int8 — всё в int8. Самый быстрый и лёгкий: ~98-99% качества,\n"
    "    возможны редкие ошибки в тихих/смазанных местах.\n"
    "    Вариант для слабых машин или когда важна скорость.")


# ── рабочий поток: запуск скрипта с живым логом ──────────────────────
def fmt_elapsed(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class ScriptWorker(QThread):
    line = pyqtSignal(str)
    file_started = pyqtSignal(str)
    file_done = pyqtSignal(str, bool, float)
    all_done = pyqtSignal(int, int)

    def __init__(self, script, files, args_builder, log_path=None):
        super().__init__()
        self.script = script
        self.files = files
        self.args_builder = args_builder  # file -> [args]
        self.log_path = log_path
        self._logf = None
        self.proc = None
        self.stopped = False

    def say(self, text, stamp=True):
        if stamp:
            text = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.line.emit(text)
        if self._logf:
            try:
                self._logf.write(text + "\n")
                self._logf.flush()
            except OSError:
                pass

    def run(self):
        if self.log_path:
            try:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                self._logf = open(self.log_path, "w", encoding="utf-8")
            except OSError:
                self._logf = None
        ok = fail = 0
        for i, f in enumerate(self.files, 1):
            if self.stopped:
                break
            self.file_started.emit(f)
            t0 = time.time()
            cmd = [sys.executable, os.path.join(APP_DIR, self.script), f]
            cmd += self.args_builder(f)
            self.say("=" * 60, stamp=False)
            self.say(f"Файл {i}/{len(self.files)}: {os.path.basename(f)}")
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", cwd=APP_DIR,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                for out_line in self.proc.stdout:
                    out_line = out_line.rstrip()
                    if out_line:
                        self.say(out_line)
                self.proc.wait()
                good = (self.proc.returncode == 0) and not self.stopped
            except Exception as e:
                self.say(f"ОШИБКА запуска: {e}")
                good = False
            status = "УСПЕШНО" if good else ("ОСТАНОВЛЕНО" if self.stopped
                                             else "ОШИБКА")
            self.say(f"--- {status}, файл занял "
                     f"{fmt_elapsed(time.time() - t0)} ---")
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
            self.file_done.emit(f, good, time.time() - t0)
        self.say("")
        self.say(f"===== ИТОГ: успешно {ok}, с ошибками {fail}, "
                 f"всего {len(self.files)} =====", stamp=False)
        if self._logf:
            try:
                self._logf.close()
            except OSError:
                pass
        self.all_done.emit(ok, fail)

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)


# ── окно справки по параметрам ───────────────────────────────────────
class ParamsHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справка по параметрам Faster-Whisper XXL")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Поиск:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("например: beam_size")
        row.addWidget(self.search)
        btn = QPushButton("Найти далее")
        row.addWidget(btn)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 9))
        try:
            self.text.setPlainText(
                open(PARAMS_HELP_PATH, encoding="utf-8").read())
        except OSError:
            self.text.setPlainText("Файл справки не найден: "
                                   + PARAMS_HELP_PATH)
        lay.addWidget(self.text)
        btn.clicked.connect(self.find_next)
        self.search.returnPressed.connect(self.find_next)

    def find_next(self):
        q = self.search.text().strip()
        if q and not self.text.find(q):
            self.text.moveCursor(self.text.textCursor().MoveOperation.Start)
            self.text.find(q)


# ── общие элементы вкладок ───────────────────────────────────────────
class FileListWidget(QListWidget):
    """Список файлов: drag&drop извне, перестановка мышью, нумерация.

    Роли данных: UserRole = путь; +1 = базовый текст; +2 = флаг «просканирован»
    (вкладка MD); +3 = суффикс статуса (✓/✗/► ...).
    """

    def __init__(self, with_probe=True, exts=None, on_changed=None):
        super().__init__()
        self.with_probe = with_probe
        self.exts = exts or AUDIO_EXTS
        self.on_changed = on_changed
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)  # внутренняя перестановка

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                self.add_path(url.toLocalFile())
        else:
            super().dropEvent(e)  # завершение перестановки
        self.renumber()
        if self.on_changed:
            self.on_changed()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.remove_selected()
        else:
            super().keyPressEvent(e)

    def remove_selected(self):
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.renumber()

    def add_path(self, path):
        if not os.path.isfile(path):
            return
        if os.path.splitext(path)[1].lower() not in self.exts:
            return
        if path in self.paths():
            return
        info = probe_summary(path) if self.with_probe else ""
        base = os.path.basename(path) + (f"   [{info}]" if info else "")
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setData(Qt.ItemDataRole.UserRole + 1, base)
        item.setToolTip(path)
        self.addItem(item)
        self._refresh_item(item)

    def _refresh_item(self, item):
        base = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        suffix = item.data(Qt.ItemDataRole.UserRole + 3) or ""
        item.setText(f"{self.row(item) + 1}.  {base}{suffix}")

    def renumber(self):
        for i in range(self.count()):
            self._refresh_item(self.item(i))

    def paths(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.count())]

    def _item_by_path(self, path):
        for i in range(self.count()):
            if self.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return self.item(i)
        return None

    def set_status(self, path, suffix, color=None):
        item = self._item_by_path(path)
        if not item:
            return
        item.setData(Qt.ItemDataRole.UserRole + 3, suffix)
        item.setForeground(QBrush(QColor(color)) if color else QBrush())
        self._refresh_item(item)

    def reset_statuses(self):
        for i in range(self.count()):
            item = self.item(i)
            item.setData(Qt.ItemDataRole.UserRole + 3, "")
            item.setForeground(QBrush())
            self._refresh_item(item)


def make_output_row(settings, key):
    """Радио «рядом с исходником» / «в папку...» + браузер."""
    box = QHBoxLayout()
    r_near = QRadioButton("Рядом с исходником")
    r_dir = QRadioButton("В папку:")
    edit = QLineEdit(settings.get(key + "_dir", ""))
    edit.setPlaceholderText("выберите папку...")
    btn = QPushButton("...")
    btn.setFixedWidth(30)
    if settings.get(key + "_mode") == "dir" and edit.text():
        r_dir.setChecked(True)
    else:
        r_near.setChecked(True)

    def browse():
        d = QFileDialog.getExistingDirectory(None, "Папка для результатов",
                                             edit.text() or APP_DIR)
        if d:
            edit.setText(d)
            r_dir.setChecked(True)

    btn.clicked.connect(browse)
    box.addWidget(r_near)
    box.addWidget(r_dir)
    box.addWidget(edit, stretch=1)
    box.addWidget(btn)

    def value():
        return edit.text() if (r_dir.isChecked() and edit.text()) else None

    def dump(into):
        into[key + "_mode"] = "dir" if r_dir.isChecked() else "near"
        into[key + "_dir"] = edit.text()

    return box, value, dump


# ── вкладка «Транскрибация» ─────────────────────────────────────────
class TranscribeTab(QWidget):
    def __init__(self, settings, log_font):
        super().__init__()
        self.settings = settings
        self.worker = None
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("Файлы (перетащите или добавьте) — любой аудио/видео формат:"))
        self.files = FileListWidget(with_probe=True)
        lay.addWidget(self.files, stretch=2)

        row = QHBoxLayout()
        b_add = QPushButton("Добавить файлы...")
        b_add.clicked.connect(self.add_files)
        b_del = QPushButton("Удалить выбранное")
        b_del.clicked.connect(self.files.remove_selected)
        b_clear = QPushButton("Очистить всё")
        b_clear.clicked.connect(self.files.clear)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addWidget(b_clear)
        row.addStretch()
        lay.addLayout(row)

        grid = QGridLayout()
        mode_lbl = QHBoxLayout()
        mode_lbl.addWidget(QLabel("Режим:"))
        mode_lbl.addWidget(info_icon(TIP_MODE))
        mode_lbl.addStretch()
        grid.addLayout(mode_lbl, 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["Диалог (авто)", "Дорожки (OBS)",
                            "Стерео: разделить каналы", "Диаризация",
                            "Без спикеров (монолог)"])
        grid.addWidget(self.mode, 0, 1)
        grid.addWidget(QLabel("Спикеров:"), 0, 2)
        self.speakers = QComboBox()
        self.speakers.addItems(["Авто", "1", "2", "3", "4", "5", "6"])
        self.speakers.setCurrentText(settings.get("speakers", "2"))
        grid.addWidget(self.speakers, 0, 3)
        model_lbl = QHBoxLayout()
        model_lbl.addWidget(QLabel("Модель:"))
        model_lbl.addWidget(info_icon(
            "Встроенные модели Whisper докачиваются автоматически при\n"
            "первом использовании (в папку xxl\\_models, остаются в сборке).\n"
            "\n"
            "«Своя модель (папка)...» — подключение стороннего файнтюна\n"
            "(например, дообученного под русский). Требования:\n"
            "  - формат CTranslate2 / faster-whisper: папка, внутри которой\n"
            "    лежат model.bin, config.json и tokenizer.json (или\n"
            "    vocabulary.*). На HuggingFace такие варианты помечают\n"
            "    «ct2», «faster-whisper» или кладут в подпапку ct2*;\n"
            "  - архитектура Whisper (large/turbo и т.п.);\n"
            "  - «обычные» модели transformers (model.safetensors) в этом\n"
            "    виде НЕ подойдут — их нужно сначала конвертировать\n"
            "    утилитой ct2-transformers-converter.\n"
            "\n"
            "Выбранная папка запоминается. Диаризация от модели не зависит."))
        model_lbl.addStretch()
        grid.addLayout(model_lbl, 0, 4)
        self.model = QComboBox()
        for m in MODELS:
            self.model.addItem(model_label(m), m)
        self.model.addItem("Своя модель (папка)...", "custom")
        self.custom_model_path = settings.get("custom_model", "")
        saved_model = settings.get("model", "large-v3-turbo")
        if saved_model == "custom" and os.path.isdir(self.custom_model_path):
            i = self.model.count() - 1
            self.model.setItemText(
                i, f"Своя: {os.path.basename(self.custom_model_path)}")
            self.model.setCurrentIndex(i)
        else:
            for i in range(self.model.count()):
                if self.model.itemData(i) == saved_model:
                    self.model.setCurrentIndex(i)
        self._prev_model_index = self.model.currentIndex()
        self.model.activated.connect(self._on_model_activated)
        grid.addWidget(self.model, 0, 5)

        grid.addWidget(QLabel("Устройство:"), 1, 0)
        self.device = QComboBox()
        self.device.addItems(["Авто", "CPU", "CUDA"])
        self.device.setCurrentText(settings.get("device", "Авто"))
        grid.addWidget(self.device, 1, 1)
        grid.addWidget(QLabel("Язык:"), 1, 2)
        self.language = QComboBox()
        self.language.addItems(["ru", "en", "auto"])
        self.language.setCurrentText(settings.get("language", "ru"))
        grid.addWidget(self.language, 1, 3)

        # Дорожки — кнопки-флажки 1..6 (все включены = брать все дорожки)
        tr_box = QHBoxLayout()
        tr_box.addWidget(QLabel("Дорожки:"))
        tr_box.addWidget(info_icon(TIP_TRACKS))
        saved_tracks = set(settings.get("tracks_sel", [1, 2, 3, 4, 5, 6]))
        self.track_checks = []
        for n in range(1, 7):
            cb = QCheckBox(str(n))
            cb.setChecked(n in saved_tracks)
            self.track_checks.append(cb)
            tr_box.addWidget(cb)
        tr_box.addStretch()
        grid.addLayout(tr_box, 2, 0, 1, 6)

        fmt_box = QHBoxLayout()
        fmt_box.addWidget(QLabel("Форматы:"))
        self.f_time = QCheckBox("timecodes.txt")
        self.f_txt = QCheckBox("txt")
        self.f_md = QCheckBox("md")
        self.f_srt = QCheckBox("srt")
        for cb, key in ((self.f_time, "f_time"), (self.f_txt, "f_txt"),
                        (self.f_md, "f_md"), (self.f_srt, "f_srt")):
            cb.setChecked(settings.get(key, True))
            fmt_box.addWidget(cb)
        fmt_box.addStretch()
        grid.addLayout(fmt_box, 1, 4, 1, 2)
        lay.addLayout(grid)

        out_row, self.out_value, self.out_dump = make_output_row(settings, "tr_out")
        self.overwrite = QCheckBox("Перезаписывать готовые")
        self.overwrite.setChecked(settings.get("tr_overwrite", False))
        self.overwrite.setToolTip(
            "Выключено: файлы, у которых все выбранные форматы уже созданы,\n"
            "пропускаются — удобно перезапускать прерванную очередь.\n"
            "Включено: обрабатывать заново и перезаписывать.")
        out_row.addWidget(self.overwrite)
        lay.addLayout(out_row)

        # — Дополнительно —
        self.adv = QGroupBox("Дополнительно")
        self.adv.setCheckable(True)
        self.adv.setChecked(settings.get("adv_open", False))
        ag = QGridLayout(self.adv)
        ag.addWidget(QLabel("Потоки CPU (0=авто):"), 0, 0)
        self.threads = QSpinBox()
        self.threads.setRange(0, 64)
        self.threads.setValue(settings.get("threads", 0))
        ag.addWidget(self.threads, 0, 1)
        compute_lbl = QHBoxLayout()
        compute_lbl.addWidget(QLabel("compute_type:"))
        compute_lbl.addWidget(info_icon(TIP_COMPUTE))
        compute_lbl.addStretch()
        ag.addLayout(compute_lbl, 0, 2)
        self.compute = QComboBox()
        self.compute.addItems(["авто", "int8", "int8_float16",
                               "int8_float32", "float16", "float32"])
        self.compute.setCurrentText(settings.get("compute", "авто"))
        ag.addWidget(self.compute, 0, 3)
        self.vad = QCheckBox("VAD-фильтр (пропуск тишины)")
        self.vad.setChecked(settings.get("vad", True))
        ag.addWidget(self.vad, 0, 4)
        ag.addWidget(QLabel("Движок диаризации:"), 1, 0)
        self.engine = QComboBox()
        self.engine.addItems(["pyannote_v3.1", "pyannote_v3.0",
                              "reverb_v1", "reverb_v2"])
        self.engine.setCurrentText(settings.get("engine", "pyannote_v3.1"))
        ag.addWidget(self.engine, 1, 1)
        ag.addWidget(QLabel("Устройство диаризации:"), 1, 2)
        self.diar_dev = QComboBox()
        self.diar_dev.addItems(["как основное", "cpu", "cuda"])
        self.diar_dev.setCurrentText(settings.get("diar_dev", "как основное"))
        ag.addWidget(self.diar_dev, 1, 3)
        ag.addWidget(QLabel("Доп. аргументы XXL:"), 2, 0)
        self.extra = QLineEdit(settings.get("extra", ""))
        self.extra.setPlaceholderText("например: --beam_size 8")
        ag.addWidget(self.extra, 2, 1, 1, 3)
        b_help = QPushButton("Справка по параметрам")
        b_help.clicked.connect(lambda: ParamsHelpDialog(self).exec())
        ag.addWidget(b_help, 2, 4)
        lay.addWidget(self.adv)

        run_row = QHBoxLayout()
        self.b_start = QPushButton("Старт")
        self.b_start.setMinimumHeight(34)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("Стоп")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        self.elapsed = QLabel("")
        self.b_log = QPushButton("Открыть лог")
        self.b_log.setEnabled(False)
        self.b_log.clicked.connect(self._open_log)
        self.b_clear_console = QPushButton("Очистить консоль")
        self.b_clear_console.setToolTip(
            "Стирает текст в окне ниже. Файл лога не трогается.")
        self.b_clear_console.clicked.connect(
            lambda: self.console.clear())
        run_row.addWidget(self.b_start, stretch=2)
        run_row.addWidget(self.b_stop, stretch=1)
        run_row.addWidget(self.b_log)
        run_row.addWidget(self.b_clear_console)
        run_row.addWidget(self.elapsed)
        lay.addLayout(run_row)

        # Неприменимые к режиму настройки затеняются
        self.mode.currentIndexChanged.connect(self._update_mode_ui)
        self._update_mode_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.t0 = None
        self.t_file = None

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(log_font)
        lay.addWidget(self.console, stretch=3)

    def _tick(self):
        if self.t0:
            text = "Всего: " + fmt_elapsed(time.time() - self.t0)
            if self.t_file:
                text += " | текущий: " + fmt_elapsed(time.time() - self.t_file)
            self.elapsed.setText(text)

    def _open_log(self):
        if getattr(self, "log_path", None) and os.path.isfile(self.log_path):
            os.startfile(self.log_path)

    def _on_file_started(self, path):
        self.t_file = time.time()
        self.files.set_status(path, "   ► в работе...", "#d9c27a")

    def _on_file_done(self, path, good, dur):
        if good:
            self.files.set_status(path, f"   ✓ готово ({fmt_elapsed(dur)})",
                                  "#7fbf7f")
        else:
            self.files.set_status(path, "   ✗ ошибка", "#e08080")

    def _on_model_activated(self, index):
        if self.model.itemData(index) != "custom":
            self._prev_model_index = index
            return
        start_dir = (self.custom_model_path
                     if os.path.isdir(self.custom_model_path) else APP_DIR)
        d = QFileDialog.getExistingDirectory(
            self, "Папка модели (формат CTranslate2 / faster-whisper)",
            start_dir)
        if not d:
            self.model.setCurrentIndex(self._prev_model_index)
            return
        missing = [f for f in ("model.bin", "config.json")
                   if not os.path.isfile(os.path.join(d, f))]
        if missing:
            QMessageBox.warning(
                self, "Не похоже на модель",
                "В папке не найдено: " + ", ".join(missing) +
                ".\nНужна модель в формате CTranslate2 / faster-whisper\n"
                "(подробности — в подсказке ⓘ рядом со списком моделей).")
            self.model.setCurrentIndex(self._prev_model_index)
            return
        self.custom_model_path = d
        self.model.setItemText(index, f"Своя: {os.path.basename(d)}")
        self._prev_model_index = index

    def _update_mode_ui(self):
        i = self.mode.currentIndex()
        # 0 Диалог(авто), 1 Дорожки, 2 Стерео, 3 Диаризация, 4 Монолог
        self.speakers.setEnabled(i in (0, 3))
        for cb in self.track_checks:
            cb.setEnabled(i in (0, 1))
        self.engine.setEnabled(i in (0, 3))
        self.diar_dev.setEnabled(i in (0, 3))

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Аудио или видео", "", f"Аудио/видео ({exts});;Все файлы (*)")
        for f in files:
            self.files.add_path(f)

    def build_args(self, _file):
        a = []
        mode_map = {0: "auto", 1: "tracks", 2: "stereo", 3: "diarize",
                    4: "plain"}
        a += ["--mode", mode_map[self.mode.currentIndex()]]
        if self.speakers.currentText() != "Авто":
            a += ["--speakers", self.speakers.currentText()]
        if self.model.currentData() == "custom":
            a += ["--model", self.custom_model_path]
        else:
            a += ["--model", self.model.currentData()]
        dev = {"Авто": "auto", "CPU": "cpu", "CUDA": "cuda"}[self.device.currentText()]
        a += ["--device", dev]
        a += ["--language", self.language.currentText()]
        sel = [str(n + 1) for n, cb in enumerate(self.track_checks)
               if cb.isChecked()]
        if sel and len(sel) < 6:
            a += ["--tracks", ",".join(sel)]
        if self.threads.value() > 0:
            a += ["--threads", str(self.threads.value())]
        if self.compute.currentText() != "авто":
            a += ["--compute-type", self.compute.currentText()]
        a += ["--vad", "true" if self.vad.isChecked() else "false"]
        a += ["--diarize-engine", self.engine.currentText()]
        if self.diar_dev.currentText() != "как основное":
            a += ["--diarize-device", self.diar_dev.currentText()]
        formats = [name for cb, name in ((self.f_time, "timecodes"),
                                         (self.f_txt, "txt"),
                                         (self.f_md, "md"),
                                         (self.f_srt, "srt"))
                   if cb.isChecked()]
        a += ["--formats", ",".join(formats)]
        out = self.out_value()
        if out:
            a += ["--output-dir", out]
        if self.overwrite.isChecked():
            a.append("--overwrite")
        if self.extra.text().strip():
            a += ["--xxl-args", self.extra.text().strip()]
        return a

    def start(self):
        files = self.files.paths()
        if not files:
            QMessageBox.warning(self, "Нет файлов", "Добавьте хотя бы один файл.")
            return
        if not any(cb.isChecked() for cb in
                   (self.f_time, self.f_txt, self.f_md, self.f_srt)):
            QMessageBox.warning(self, "Нет форматов",
                                "Отметьте хотя бы один выходной формат.")
            return
        if (self.model.currentData() == "custom"
                and not os.path.isdir(self.custom_model_path)):
            QMessageBox.warning(self, "Модель не найдена",
                                "Папка своей модели не существует:\n"
                                + (self.custom_model_path or "(не выбрана)")
                                + "\nВыберите её заново в списке моделей.")
            return
        self.console.clear()
        self.b_start.setEnabled(False)
        self.b_stop.setEnabled(True)
        # Снимок настроек в момент старта: вся очередь идёт с одними параметрами
        args_snapshot = self.build_args(None)
        log_path = os.path.join(APP_DIR, "logs",
                                time.strftime("транскрибация_%Y-%m-%d_%H-%M-%S.txt"))
        self.files.reset_statuses()
        self.log_path = log_path
        self.b_log.setEnabled(True)
        self.console.appendPlainText("Лог пишется в: " + log_path)
        self.worker = ScriptWorker("transcriber.py", files,
                                   lambda _f, a=args_snapshot: a, log_path)
        self.worker.line.connect(self.console.appendPlainText)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self.done)
        self.t0 = time.time()
        self.t_file = None
        self.timer.start()
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.console.appendPlainText(">>> Остановлено пользователем.")

    def done(self, _ok, _fail):
        self.b_start.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.timer.stop()
        self.t_file = None
        self._tick()

    def dump_settings(self, s):
        s["tracks_sel"] = [n + 1 for n, cb in enumerate(self.track_checks)
                           if cb.isChecked()]
        s["speakers"] = self.speakers.currentText()
        s["model"] = self.model.currentData()
        s["custom_model"] = self.custom_model_path
        s["device"] = self.device.currentText()
        s["language"] = self.language.currentText()
        for cb, key in ((self.f_time, "f_time"), (self.f_txt, "f_txt"),
                        (self.f_md, "f_md"), (self.f_srt, "f_srt")):
            s[key] = cb.isChecked()
        s["tr_overwrite"] = self.overwrite.isChecked()
        s["adv_open"] = self.adv.isChecked()
        s["threads"] = self.threads.value()
        s["compute"] = self.compute.currentText()
        s["vad"] = self.vad.isChecked()
        s["engine"] = self.engine.currentText()
        s["diar_dev"] = self.diar_dev.currentText()
        s["extra"] = self.extra.text()
        self.out_dump(s)


# ── вкладка «Файл для прослушивания» ─────────────────────────────────
class ListenTab(QWidget):
    def __init__(self, settings, log_font):
        super().__init__()
        self.worker = None
        lay = QVBoxLayout(self)
        top_lbl = QHBoxLayout()
        top_lbl.addWidget(QLabel(
            "Файлы (любой аудио/видео формат; из видео берётся звук):"))
        top_lbl.addWidget(info_icon(
            "Переводит аудиозаписи подкастов, диалогов, бесед в самый\n"
            "экономичный и качественный формат: часовая запись из сотен МБ\n"
            "превращается в файл на десятки МБ практически без потери качества.\n"
            "\n"
            "Из любого видео (лекция, вебинар, запись экрана) делает\n"
            "небольшой аудиофайл — видеоряд отбрасывается, остаётся звук.\n"
            "\n"
            "Форматы:\n"
            "  opus — самый экономичный, лучшее качество на килобайт\n"
            "         (идеален для архива голосовых записей);\n"
            "  mp3  — крупнее, но открывается вообще везде\n"
            "         (плееры, автомагнитолы, старые устройства).\n"
            "\n"
            "Пресеты «голос» подобраны по битрейту под сжатие речи —\n"
            "минимальный размер при идеальном качестве. Для музыки есть\n"
            "отдельный пресет «Максимум» (opus 256 / mp3 320).\n"
            "\n"
            "Также умеет: выровнять громкость (нормализация), свести\n"
            "многодорожечную запись OBS в один файл или развести голоса\n"
            "по ушам.\n"
            "На результат транскрибации не влияет — это отдельный инструмент."))
        top_lbl.addStretch()
        lay.addLayout(top_lbl)
        self.files = FileListWidget(with_probe=True)
        lay.addWidget(self.files, stretch=2)

        row = QHBoxLayout()
        b_add = QPushButton("Добавить файлы...")
        b_add.clicked.connect(self.add_files)
        b_del = QPushButton("Удалить выбранное")
        b_del.clicked.connect(self.files.remove_selected)
        b_clear = QPushButton("Очистить всё")
        b_clear.clicked.connect(self.files.clear)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addWidget(b_clear)
        row.addStretch()
        lay.addLayout(row)

        grid = QGridLayout()
        grid.addWidget(QLabel("Формат:"), 0, 0)
        self.fmt = QComboBox()
        self.fmt.addItems(["opus", "mp3"])
        self.fmt.setCurrentText(settings.get("li_fmt", "opus"))
        grid.addWidget(self.fmt, 0, 1)
        grid.addWidget(QLabel("Качество:"), 0, 2)
        self.quality = QComboBox()
        self.quality.addItem("Компактное — голос (opus 32 / mp3 64)", "compact")
        self.quality.addItem("Стандарт — голос (opus 64 / mp3 128)", "standard")
        self.quality.addItem("Высокое — голос (opus 96 / mp3 192)", "high")
        self.quality.addItem("Максимум — музыка (opus 256 / mp3 320)", "music")
        idx = {"compact": 0, "standard": 1, "high": 2, "music": 3}.get(
            settings.get("li_quality", "standard"), 1)
        self.quality.setCurrentIndex(idx)
        grid.addWidget(self.quality, 0, 3)
        ch_lbl = QHBoxLayout()
        ch_lbl.addWidget(QLabel("Каналы:"))
        ch_lbl.addWidget(info_icon(
            "Как в исходнике — только смена формата/качества, каналы не\n"
            "    трогаются. Для обычных файлов с одной дорожкой.\n"
            "Моно (сведение) — все выбранные дорожки смешиваются в один\n"
            "    канал. Стандарт для переслушивания записей: минимальный\n"
            "    размер, привычное звучание.\n"
            "Стерео: голоса по ушам — для мультитрека (OBS): одна дорожка\n"
            "    в левое ухо, другая в правое. Удобно разбирать, кто что\n"
            "    сказал; какая дорожка в какое ухо — выбирается ниже."))
        ch_lbl.addStretch()
        grid.addLayout(ch_lbl, 1, 0)
        self.channels = QComboBox()
        self.channels.addItem("Как в исходнике (только конвертация)", "original")
        self.channels.addItem("Моно (сведение)", "mono")
        self.channels.addItem("Стерео: голоса по ушам", "stereo")
        saved_ch = settings.get("li_channels", "original")
        for i in range(self.channels.count()):
            if self.channels.itemData(i) == saved_ch:
                self.channels.setCurrentIndex(i)
        grid.addWidget(self.channels, 1, 1)
        opts_box = QHBoxLayout()
        self.norm = QCheckBox("Нормализация громкости")
        self.norm.setChecked(settings.get("li_norm", True))
        opts_box.addWidget(self.norm)
        self.verbose = QCheckBox("Подробный вывод ffmpeg")
        self.verbose.setChecked(settings.get("li_verbose", False))
        opts_box.addWidget(self.verbose)
        opts_box.addWidget(info_icon(
            "Нормализация громкости — выравнивание воспринимаемой громкости\n"
            "по вещательному стандарту EBU R128 (так делают YouTube и\n"
            "подкаст-платформы): тихое подтягивается, громкое приглушается.\n"
            "В мультитреке каждая дорожка выравнивается ОТДЕЛЬНО до сведения —\n"
            "голоса перестают «шептать и орать» вразнобой. Отключайте, только\n"
            "если нужна точная копия исходного звука.\n"
            "\n"
            "Подробный вывод ffmpeg — вместо аккуратных процентов в лог идёт\n"
            "полный «живой» вывод ffmpeg (версии, параметры, скорость).\n"
            "На результат не влияет — чисто понаблюдать за процессом."))
        opts_box.addStretch()
        grid.addLayout(opts_box, 1, 2, 1, 2)

        # При выборе пресета «музыка» нормализация снимается автоматически
        # (её цель — выровнять голос под −16 LUFS, музыке это вредит)
        self.quality.currentIndexChanged.connect(self._on_quality_changed)

        # Дорожки: в моно — флажки «что сводим», в стерео — выбор L/R
        self.mono_tracks_w = QWidget()
        mb = QHBoxLayout(self.mono_tracks_w)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.addWidget(QLabel("Дорожки:"))
        mb.addWidget(info_icon(
            "Какие аудиодорожки сводить в общий моно-файл.\n"
            "При переключении на «Моно» отмечаются все; снимите лишние\n"
            "(например, дорожку с системными звуками).\n"
            "Номера сверх имеющихся в файле игнорируются."))
        self.track_checks = []
        for n in range(1, 7):
            cb = QCheckBox(str(n))
            cb.setChecked(True)
            self.track_checks.append(cb)
            mb.addWidget(cb)
        mb.addStretch()

        self.stereo_tracks_w = QWidget()
        sb = QHBoxLayout(self.stereo_tracks_w)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addWidget(QLabel("Левое ухо:"))
        self.left_track = QComboBox()
        sb.addWidget(self.left_track)
        sb.addSpacing(16)
        sb.addWidget(QLabel("Правое ухо:"))
        self.right_track = QComboBox()
        sb.addWidget(self.right_track)
        for n in range(1, 7):
            self.left_track.addItem(f"дорожка {n}", n)
            self.right_track.addItem(f"дорожка {n}", n)
        self.left_track.setCurrentIndex(int(settings.get("li_left", 1)) - 1)
        self.right_track.setCurrentIndex(int(settings.get("li_right", 2)) - 1)
        sb.addStretch()

        grid.addWidget(self.mono_tracks_w, 2, 0, 1, 4)
        grid.addWidget(self.stereo_tracks_w, 2, 0, 1, 4)
        lay.addLayout(grid)
        self.channels.currentIndexChanged.connect(self._update_channels_ui)
        self._update_channels_ui()

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Имя файла:"))
        name_row.addWidget(info_icon(
            "Шаблон имени результата. Теги:\n"
            "  {имя}        — имя исходного файла (без расширения)\n"
            "  {дата}       — дата исходного файла, ГГГГ-ММ-ДД\n"
            "  {время_чм}   — время исходного файла, ЧЧ-ММ\n"
            "  {время_чмс}  — время исходного файла, ЧЧ-ММ-СС\n"
            "  {сегодня}    — сегодняшняя дата, ГГГГ-ММ-ДД\n"
            "  {#} {##} {###} — номер файла в пачке (7 / 07 / 007)\n"
            "(время через дефисы: двоеточия в именах файлов запрещены)\n"
            "\n"
            "Примеры:\n"
            "  {имя} (прослушивание)  ->  запись (прослушивание).opus\n"
            "  {имя}                  ->  запись.opus (массовая конвертация)\n"
            "  {##} {имя}             ->  01 выпуск.opus, 02 выпуск.opus...\n"
            "  {дата}[{время_чм}]     ->  2026-07-18[15-30].opus\n"
            "\n"
            "Если результат совпал бы с исходным файлом — конвертация\n"
            "остановится с предупреждением, исходник не затрётся."))
        self.name_tpl = QLineEdit(settings.get("li_name_tpl",
                                               "{имя} (прослушивание)"))
        name_row.addWidget(self.name_tpl, stretch=1)
        name_row.addWidget(QLabel("+ .opus / .mp3"))
        lay.addLayout(name_row)

        out_row, self.out_value, self.out_dump = make_output_row(settings, "li_out")
        self.overwrite = QCheckBox("Перезаписывать готовые")
        self.overwrite.setChecked(settings.get("li_overwrite", False))
        self.overwrite.setToolTip(
            "Выключено: файлы, для которых результат уже создан,\n"
            "пропускаются — удобно перезапускать прерванную очередь.\n"
            "Включено: конвертировать заново и перезаписывать.")
        out_row.addWidget(self.overwrite)
        lay.addLayout(out_row)

        run_row = QHBoxLayout()
        self.b_start = QPushButton("Создать")
        self.b_start.setMinimumHeight(34)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("Стоп")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        self.elapsed = QLabel("")
        self.b_log = QPushButton("Открыть лог")
        self.b_log.setEnabled(False)
        self.b_log.clicked.connect(self._open_log)
        self.b_clear_console = QPushButton("Очистить консоль")
        self.b_clear_console.setToolTip(
            "Стирает текст в окне ниже. Файл лога не трогается.")
        self.b_clear_console.clicked.connect(
            lambda: self.console.clear())
        run_row.addWidget(self.b_start, stretch=2)
        run_row.addWidget(self.b_stop, stretch=1)
        run_row.addWidget(self.b_log)
        run_row.addWidget(self.b_clear_console)
        run_row.addWidget(self.elapsed)
        lay.addLayout(run_row)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.t0 = None
        self.t_file = None

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(log_font)
        lay.addWidget(self.console, stretch=2)

    def _tick(self):
        if self.t0:
            text = "Всего: " + fmt_elapsed(time.time() - self.t0)
            if self.t_file:
                text += " | текущий: " + fmt_elapsed(time.time() - self.t_file)
            self.elapsed.setText(text)

    def _open_log(self):
        if getattr(self, "log_path", None) and os.path.isfile(self.log_path):
            os.startfile(self.log_path)

    def _on_file_started(self, path):
        self.t_file = time.time()
        self.files.set_status(path, "   ► в работе...", "#d9c27a")

    def _on_file_done(self, path, good, dur):
        if good:
            self.files.set_status(path, f"   ✓ готово ({fmt_elapsed(dur)})",
                                  "#7fbf7f")
        else:
            self.files.set_status(path, "   ✗ ошибка", "#e08080")

    def _update_channels_ui(self):
        ch = self.channels.currentData()
        self.mono_tracks_w.setVisible(ch == "mono")
        self.stereo_tracks_w.setVisible(ch == "stereo")
        if ch == "mono":
            for cb in self.track_checks:
                cb.setChecked(True)

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Аудио или видео", "", f"Аудио/видео ({exts});;Все файлы (*)")
        for f in files:
            self.files.add_path(f)

    def _on_quality_changed(self):
        if self.quality.currentData() == "music" and self.norm.isChecked():
            self.norm.setChecked(False)

    def build_args(self, _file):
        a = ["--format", self.fmt.currentText(),
             "--quality", self.quality.currentData(),
             "--channels", self.channels.currentData()]
        if not self.norm.isChecked():
            a.append("--no-normalize")
        if self.verbose.isChecked():
            a.append("--verbose")
        if self.overwrite.isChecked():
            a.append("--overwrite")
        tpl = self.name_tpl.text().strip() or "{имя} (прослушивание)"
        a += ["--name-template", tpl]
        ch = self.channels.currentData()
        if ch == "stereo":
            a += ["--tracks", f"{self.left_track.currentData()},"
                              f"{self.right_track.currentData()}"]
        elif ch == "mono":
            sel = [str(n + 1) for n, cb in enumerate(self.track_checks)
                   if cb.isChecked()]
            if sel and len(sel) < 6:
                a += ["--tracks", ",".join(sel)]
        out = self.out_value()
        if out:
            a += ["--output-dir", out]
        return a

    def start(self):
        files = self.files.paths()
        if not files:
            QMessageBox.warning(self, "Нет файлов", "Добавьте хотя бы один файл.")
            return
        if (self.channels.currentData() == "stereo"
                and self.left_track.currentData() == self.right_track.currentData()):
            QMessageBox.warning(self, "Одна и та же дорожка",
                                "В левое и правое ухо выбрана одна дорожка.\n"
                                "Выберите разные.")
            return
        self.console.clear()
        self.b_start.setEnabled(False)
        self.b_stop.setEnabled(True)
        args_snapshot = self.build_args(None)
        log_path = os.path.join(APP_DIR, "logs",
                                time.strftime("аудио_%Y-%m-%d_%H-%M-%S.txt"))
        self.files.reset_statuses()
        self.log_path = log_path
        self.b_log.setEnabled(True)
        self.console.appendPlainText("Лог пишется в: " + log_path)
        self.worker = ScriptWorker(
            "listening.py", files,
            lambda f, a=args_snapshot, ff=files:
                a + ["--index", str(ff.index(f) + 1)],
            log_path)
        self.worker.line.connect(self.console.appendPlainText)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self.done)
        self.t0 = time.time()
        self.t_file = None
        self.timer.start()
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.console.appendPlainText(">>> Остановлено пользователем.")

    def done(self, _ok, _fail):
        self.b_start.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.timer.stop()
        self.t_file = None
        self._tick()

    def dump_settings(self, s):
        s["li_left"] = self.left_track.currentData()
        s["li_right"] = self.right_track.currentData()
        s["li_verbose"] = self.verbose.isChecked()
        s["li_overwrite"] = self.overwrite.isChecked()
        s["li_name_tpl"] = self.name_tpl.text().strip() or "{имя} (прослушивание)"
        s["li_fmt"] = self.fmt.currentText()
        s["li_quality"] = self.quality.currentData()
        s["li_channels"] = self.channels.currentData()
        s["li_norm"] = self.norm.isChecked()
        self.out_dump(s)


# ── вкладка «Конвертер MD» ───────────────────────────────────────────
class MdTab(QWidget):
    def __init__(self, settings, log_font):
        super().__init__()
        self.worker = None
        self.log_path = None
        self.saved_map = dict(settings.get("md_map", {}))
        self.saved_excluded = set(settings.get("md_excluded", []))
        lay = QVBoxLayout(self)

        top_lbl = QHBoxLayout()
        top_lbl.addWidget(QLabel("Транскрипты (txt / srt / vtt) или готовые MD "
                                 "для переименования — формат распознаётся сам:"))
        top_lbl.addWidget(info_icon(
            "Зачем эта вкладка: превращает «сырой» транскрипт (файл с\n"
            "таймкодами и метками «Спикер 1/2») в читабельный MD-документ:\n"
            "шапка со статистикой (кто сколько говорил) + диалог абзацами\n"
            "«**Имя:** текст» по ходам говорящих.\n"
            "\n"
            "Что умеет:\n"
            "- понимает форматы: наш транскрибер, Subtitle Edit / XXL,\n"
            "  GigaAMGUI, обычные srt/vtt, диалог без таймкодов;\n"
            "- переименовывает спикеров («Спикер 1» -> «Психолог») —\n"
            "  имена задаются в таблице ниже и запоминаются;\n"
            "- может исключить речь спикера (снятая галочка);\n"
            "- готовый MD можно добавить сюда же, чтобы переименовать\n"
            "  спикеров в нём (шапка и цифры сохраняются).\n"
            "\n"
            "При добавлении файла в лог внизу выводятся первые фразы\n"
            "каждого спикера — по ним видно, кто есть кто."))
        top_lbl.addStretch()
        lay.addLayout(top_lbl)
        self.files = FileListWidget(with_probe=False, exts=TXT_EXTS,
                                    on_changed=self.scan_new)
        lay.addWidget(self.files, stretch=2)

        row = QHBoxLayout()
        b_add = QPushButton("Добавить файлы...")
        b_add.clicked.connect(self.add_files)
        b_del = QPushButton("Удалить выбранное")
        b_del.clicked.connect(self.files.remove_selected)
        b_clear = QPushButton("Очистить всё")
        b_clear.clicked.connect(self.files.clear)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addWidget(b_clear)
        row.addStretch()
        lay.addLayout(row)

        spk_lbl = QHBoxLayout()
        spk_lbl.addWidget(QLabel("Спикеры:"))
        spk_lbl.addWidget(info_icon(
            "Таблица заполняется автоматически при добавлении файлов.\n"
            "Галочка — включить речь спикера в MD (снятая = реплики\n"
            "этого спикера не попадут в расшифровку, в шапке появится\n"
            "пометка об исключении).\n"
            "Правая колонка — как назвать спикера в MD (например,\n"
            "«Спикер 1» -> «Психолог»). Имена запоминаются."))
        spk_lbl.addStretch()
        lay.addLayout(spk_lbl)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Вкл.", "Как в файле", "Как в MD"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(160)
        lay.addWidget(self.table)

        opts = QHBoxLayout()
        self.stats = QCheckBox("Шапка со статистикой")
        self.stats.setChecked(settings.get("md_stats", True))
        opts.addWidget(self.stats)
        self.turn_tc = QCheckBox("Таймкод начала хода")
        self.turn_tc.setChecked(settings.get("md_tc", False))
        opts.addWidget(self.turn_tc)
        opts.addStretch()
        lay.addLayout(opts)

        out_row, self.out_value, self.out_dump = make_output_row(settings, "md_out")
        self.overwrite = QCheckBox("Перезаписывать готовые")
        self.overwrite.setChecked(settings.get("md_overwrite", False))
        out_row.addWidget(self.overwrite)
        lay.addLayout(out_row)

        run_row = QHBoxLayout()
        self.b_start = QPushButton("Конвертировать выбранный файл")
        self.b_start.setMinimumHeight(34)
        self.b_start.clicked.connect(self.start)
        self.b_log = QPushButton("Открыть лог")
        self.b_log.setEnabled(False)
        self.b_log.clicked.connect(self.open_log)
        self.b_clear_console = QPushButton("Очистить консоль")
        self.b_clear_console.setToolTip(
            "Стирает текст в окне ниже. Файл лога не трогается.")
        self.b_clear_console.clicked.connect(
            lambda: self.console.clear())
        run_row.addWidget(self.b_start, stretch=2)
        run_row.addWidget(self.b_log)
        run_row.addWidget(self.b_clear_console)
        lay.addLayout(run_row)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(log_font)
        lay.addWidget(self.console, stretch=2)

        # Работа пофайловая: таблица спикеров и предпросмотр относятся
        # к ВЫБРАННОМУ файлу (номера спикеров в разных файлах — разные люди)
        self._cache = {}          # path -> (speakers, dialect, preview)
        self._table_path = None   # чей срез сейчас в таблице
        self.files.currentItemChanged.connect(self._show_selected)

    # — файлы и сканирование спикеров —
    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(TXT_EXTS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Транскрипты", "", f"Транскрипты ({exts});;Все файлы (*)")
        for f in files:
            self.files.add_path(f)
        self.scan_new()

    def scan_new(self):
        last_new = None
        for path in self.files.paths():
            item = self.files._item_by_path(path)
            base = item.data(Qt.ItemDataRole.UserRole + 1)
            if item.data(Qt.ItemDataRole.UserRole + 2):  # уже просканирован
                continue
            item.setData(Qt.ItemDataRole.UserRole + 2, True)
            try:
                phrases, dialect, _bad = md_converter.parse_file(path)
                speakers = sorted({p[2] for p in phrases if p[2]})
            except Exception:
                phrases, dialect, speakers = [], "ошибка чтения", []
            note = (f"   [{dialect}; спикеров: {len(speakers)}]"
                    if phrases else f"   [{dialect}]")
            item.setData(Qt.ItemDataRole.UserRole + 1, base + note)
            self.files._refresh_item(item)
            # Предпросмотр в хронологическом порядке (как в файле),
            # не более 4 реплик на каждого спикера
            per_cap = 4
            shown = {}
            preview = []
            for p in phrases:
                name = p[2]
                if not name:
                    continue
                cnt = shown.get(name, 0)
                if cnt >= per_cap:
                    continue
                shown[name] = cnt + 1
                t = p[3] if len(p[3]) <= 110 else p[3][:110] + "..."
                preview.append(f"    {name}: {t}")
                if speakers and all(shown.get(s, 0) >= per_cap
                                    for s in speakers):
                    break
            self._cache[path] = (speakers, dialect, preview)
            last_new = item
        if last_new is not None:
            self.files.setCurrentItem(last_new)  # вызовет _show_selected

    def _harvest_table(self):
        """Запомнить правки таблицы (имена/галочки) перед её перезаполнением."""
        for r in range(self.table.rowCount()):
            orig = self.table.item(r, 1).text()
            new = (self.table.item(r, 2).text() or orig).strip()
            self.saved_map[orig] = new
            if self.table.item(r, 0).checkState() != Qt.CheckState.Checked:
                self.saved_excluded.add(orig)
            else:
                self.saved_excluded.discard(orig)

    def _show_selected(self, *_args):
        item = self.files.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path not in self._cache:
            return
        self._harvest_table()
        speakers, dialect, preview = self._cache[path]
        # таблица — только спикеры выбранного файла
        self._table_path = path
        self.table.setRowCount(0)
        for name in speakers:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                         | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked
                              if name in self.saved_excluded
                              else Qt.CheckState.Checked)
            self.table.setItem(r, 0, chk)
            orig = QTableWidgetItem(name)
            orig.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, 1, orig)
            self.table.setItem(r, 2, QTableWidgetItem(
                self.saved_map.get(name, name)))
        # консоль — предпросмотр только выбранного файла
        self.console.clear()
        self.console.appendPlainText(
            f"— {os.path.basename(path)}  [{dialect}]")
        for line in preview:
            self.console.appendPlainText(line)

    # — запуск —
    def build_args(self, _file):
        a = []
        for r in range(self.table.rowCount()):
            orig = self.table.item(r, 1).text()
            new = (self.table.item(r, 2).text() or orig).strip()
            if self.table.item(r, 0).checkState() != Qt.CheckState.Checked:
                a += ["--exclude", orig]
            elif new and new != orig:
                a += ["--map", f"{orig}={new}"]
        if not self.stats.isChecked():
            a.append("--no-stats")
        if self.turn_tc.isChecked():
            a.append("--turn-timecodes")
        out = self.out_value()
        if out:
            a += ["--output-dir", out]
        if self.overwrite.isChecked():
            a.append("--overwrite")
        return a

    def start(self):
        self.scan_new()
        item = self.files.currentItem()
        if item is None and self.files.count() == 1:
            item = self.files.item(0)
            self.files.setCurrentItem(item)
        if item is None:
            QMessageBox.warning(self, "Нет файла",
                                "Выберите файл в списке (конвертация идёт "
                                "по одному файлу — спикеры в разных файлах "
                                "нумеруются по-разному).")
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        self.b_start.setEnabled(False)
        args_snapshot = self.build_args(None)
        log_path = os.path.join(APP_DIR, "logs",
                                time.strftime("конвертер_%Y-%m-%d_%H-%M-%S.txt"))
        self.log_path = log_path
        self.b_log.setEnabled(True)
        self.console.appendPlainText("")
        self.worker = ScriptWorker("converter.py", [path],
                                   lambda _f, a=args_snapshot: a, log_path)
        self.worker.line.connect(self.console.appendPlainText)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self.done)
        self.worker.start()

    def open_log(self):
        if self.log_path and os.path.exists(self.log_path):
            os.startfile(self.log_path)

    def _on_file_started(self, path):
        self.files.set_status(path, "   ► в работе...", "#d9c27a")

    def _on_file_done(self, path, good, _dur):
        if good:
            self.files.set_status(path, "   ✓ готово", "#7fbf7f")
        else:
            self.files.set_status(path, "   ✗ ошибка", "#e08080")

    def done(self, _ok, _fail):
        self.b_start.setEnabled(True)

    def dump_settings(self, s):
        self._harvest_table()
        s["md_map"] = {k: v for k, v in self.saved_map.items() if k != v}
        s["md_excluded"] = sorted(self.saved_excluded)
        s["md_stats"] = self.stats.isChecked()
        s["md_tc"] = self.turn_tc.isChecked()
        s["md_overwrite"] = self.overwrite.isChecked()
        self.out_dump(s)


# ── главное окно ─────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Транскрибер (Faster-Whisper XXL) — портативный")
        self.resize(860, 720)
        self.settings = load_settings()
        log_font = QFont("Consolas", 9)

        tabs = QTabWidget()
        self.tab_tr = TranscribeTab(self.settings, log_font)
        self.tab_li = ListenTab(self.settings, log_font)
        self.tab_md = MdTab(self.settings, log_font)
        tabs.addTab(self.tab_tr, "Транскрибация")
        tabs.addTab(self.tab_md, "Конвертер в MD")
        tabs.addTab(self.tab_li, "Файл для прослушивания")
        self.setCentralWidget(tabs)

    def closeEvent(self, e):
        for tab in (self.tab_tr, self.tab_md, self.tab_li):
            if tab.worker and tab.worker.isRunning():
                if QMessageBox.question(
                        self, "Идёт обработка",
                        "Обработка не завершена. Прервать и выйти?") \
                        != QMessageBox.StandardButton.Yes:
                    e.ignore()
                    return
                tab.worker.stop()
        s = {}
        self.tab_tr.dump_settings(s)
        self.tab_li.dump_settings(s)
        save_settings(s)
        e.accept()


def main():
    # Без своего AppUserModelID Windows группирует окно с python.exe
    # и показывает в панели задач иконку Python, а не нашу.
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "TranscriberXXL.Portable")
    app = QApplication(sys.argv)
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Аварийный лог: при запуске через pythonw ошибки не видны,
        # поэтому пишем их в файл рядом с программой.
        import traceback
        with open(os.path.join(APP_DIR, "crash.log"), "w",
                  encoding="utf-8") as _f:
            _f.write(traceback.format_exc())
        raise
