# -*- coding: utf-8 -*-
"""Оркестратор транскрибации: ffprobe -> ffmpeg -> Faster-Whisper XXL -> склейка.

Режимы:
  auto    - 2+ аудиодорожки -> tracks, иначе diarize
  tracks  - каждая дорожка = свой спикер (OBS-запись), диаризация не нужна
  stereo  - разведённое стерео: L/R -> два спикера (ручной режим)
  diarize - обычная запись, спикеры через --diarize pyannote_v3.1

Выходные форматы (полные таймкоды ЧЧ:ММ:СС):
  timecodes  ->  <имя>_diarize_timecodes.txt   [ЧЧ:ММ:СС - ЧЧ:ММ:СС] Спикер №N: текст
  txt        ->  <имя>_diarize.txt             блоки [Спикер №N] + строки фраз
  md         ->  <имя>.md                      ### Спикер №N — `ЧЧ:ММ:СС` + абзац хода
  srt        ->  <имя>.srt                     субтитры «Спикер №N: текст»

Только стандартная библиотека Python. Все инструменты — внутри папки проекта.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
XXL_EXE = os.path.join(APP_DIR, "xxl", "faster-whisper-xxl.exe")
FFMPEG = os.path.join(APP_DIR, "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(APP_DIR, "ffmpeg", "ffprobe.exe")
TEMP_ROOT = os.path.join(APP_DIR, "temp")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SPEAKER_RE = re.compile(r"^\[(SPEAKER_\d+)\]:\s*(.*)$", re.S)
SRT_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def log(msg):
    print(msg, flush=True)


def run(cmd, **kw):
    """Запуск процесса с живым выводом stdout построчно."""
    log("> " + " ".join(str(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW, **kw)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log("  " + line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Команда завершилась с кодом {proc.returncode}")


def fmt_hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fmt_srt_time(sec):
    ms = int(round((sec - int(sec)) * 1000))
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d},{ms:03d}"


# ── Инспекция файла ──────────────────────────────────────────────────
def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW).stdout
    data = json.loads(out or "{}")
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return audio, duration


# ── Определение устройства ───────────────────────────────────────────
def resolve_model(model):
    """Если --model — папка с моделью, подключить её через junction.

    XXL принимает только ИМЯ модели и ищет папку faster-whisper-<имя>
    в своём _models. Для произвольной пользовательской папки создаём
    там NTFS-ссылку (mklink /J: мгновенно, места не занимает) и отдаём
    XXL имя «_custom».
    """
    if not os.path.isdir(model):
        return model
    target = os.path.abspath(model)
    link = os.path.join(APP_DIR, "xxl", "_models", "faster-whisper-_custom")
    if os.path.isdir(link):
        try:
            if os.path.samefile(link, target):
                return "_custom"
        except OSError:
            pass
        os.rmdir(link)  # удаляет только ссылку, не содержимое
    subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                   capture_output=True, creationflags=NO_WINDOW)
    log(f"Своя модель подключена: {target}")
    return "_custom"


def detect_device():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, encoding="utf-8", timeout=10,
            creationflags=NO_WINDOW).stdout.strip()
        vram = max(int(x) for x in out.splitlines() if x.strip())
        if vram >= 4000:
            return "cuda"
        log(f"NVIDIA найдена, но VRAM {vram} МБ < 4 ГБ — используем CPU.")
    except Exception:
        pass
    return "cpu"


# ── Запуск XXL и парсинг SRT ─────────────────────────────────────────
def run_xxl(audio_path, out_dir, args, diarize=False):
    cmd = [XXL_EXE, audio_path,
           "-m", args.model,
           "--device", args.device,
           "--threads", str(args.threads),
           "--compute_type", args.compute_type,
           "--language", args.language,
           "--vad_filter", "true" if args.vad == "true" else "false",
           "--sentence",
           "-f", "srt",
           "-o", out_dir,
           "--beep_off"]
    if diarize:
        cmd += ["--diarize", args.diarize_engine,
                "--diarize_device", args.diarize_device or args.device]
        if args.speakers:
            cmd += ["--num_speakers", str(args.speakers)]
    if args.xxl_args:
        cmd += args.xxl_args.split()
    run(cmd)
    base = os.path.splitext(os.path.basename(audio_path))[0]
    srt_path = os.path.join(out_dir, base + ".srt")
    if not os.path.isfile(srt_path):
        raise RuntimeError(f"XXL не создал {srt_path}")
    return srt_path


def parse_srt(path, default_speaker=None):
    """SRT -> список фраз [(start, end, speaker, text)].

    Спикер берётся из префикса [SPEAKER_NN]: (режим диаризации),
    иначе — default_speaker (режим дорожек).
    """
    text = open(path, encoding="utf-8-sig").read()
    phrases = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = SRT_TIME_RE.search(lines[1] if lines[0].strip().isdigit() else lines[0])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        body_idx = 2 if lines[0].strip().isdigit() else 1
        body = " ".join(l.strip() for l in lines[body_idx:])
        sp = SPEAKER_RE.match(body)
        if sp:
            speaker, body = sp.group(1), sp.group(2).strip()
        else:
            speaker = default_speaker
        if body:
            phrases.append([start, end, speaker, body])
    return phrases


# ── Склейка и переименование ─────────────────────────────────────────
def rename_speakers(phrases):
    """SPEAKER_NN / track:N -> «Спикер №K» по порядку первого появления."""
    mapping = {}
    for p in phrases:
        if p[2] not in mapping:
            mapping[p[2]] = f"Спикер {len(mapping) + 1}"
        p[2] = mapping[p[2]]
    return phrases


def merge_phrases(per_source_phrases, mark_overlaps):
    """Слить фразы из нескольких источников; отметить наложения."""
    phrases = sorted((p for src in per_source_phrases for p in src),
                     key=lambda p: p[0])
    phrases = rename_speakers(phrases)
    result = []
    for p in phrases:
        overlap = bool(mark_overlaps and result
                       and p[0] < result[-1][1] - 0.2
                       and p[2] != result[-1][2])
        result.append(p + [overlap])
    return result  # [start, end, speaker, text, overlap]


def group_turns(phrases):
    """Подряд идущие фразы одного спикера -> ходы (для md/txt-блоков)."""
    turns = []
    for start, end, speaker, text, _ in phrases:
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["end"] = max(turns[-1]["end"], end)
            turns[-1]["phrases"].append(text)
        else:
            turns.append({"speaker": speaker, "start": start,
                          "end": end, "phrases": [text]})
    return turns


# ── Выходные форматы ─────────────────────────────────────────────────
def write_outputs_plain(phrases, base_out, formats):
    """Форматы для режима без спикеров (монолог/подкаст)."""
    written = []
    if "timecodes" in formats:
        path = base_out + "_timecodes.txt"
        with open(path, "w", encoding="utf-8") as f:
            for start, end, _spk, text, _ov in phrases:
                f.write(f"[{fmt_hms(start)} - {fmt_hms(end)}] {text}\n")
        written.append(path)
    if "txt" in formats:
        path = base_out + ".txt"
        with open(path, "w", encoding="utf-8") as f:
            for _s, _e, _spk, text, _ov in phrases:
                f.write(text + "\n")
        written.append(path)
    if "md" in formats:
        path = base_out + ".md"
        name = os.path.basename(base_out)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Транскрипция: {name}\n\n---\n\n")
            para, para_start, prev_end = [], None, None
            def flush():
                if para:
                    text = re.sub(r"^(\d+)\.", r"\1\\.", " ".join(para))
                    f.write(f"`{fmt_hms(para_start)}`\n\n{text}\n\n")
            for start, end, _spk, text, _ov in phrases:
                # новая пауза длиннее 3 секунд = новый абзац
                if prev_end is not None and start - prev_end > 3.0:
                    flush()
                    para, para_start = [], None
                if para_start is None:
                    para_start = start
                para.append(text)
                prev_end = end
            flush()
        written.append(path)
    if "srt" in formats:
        path = base_out + ".srt"
        with open(path, "w", encoding="utf-8") as f:
            for i, (start, end, _spk, text, _ov) in enumerate(phrases, 1):
                f.write(f"{i}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n"
                        f"{text}\n\n")
        written.append(path)
    return written


def write_outputs(phrases, base_out, formats):
    written = []
    # Пометка наложения становится частью текста фразы — одинаково во всех
    # форматах (имя спикера и двоеточие остаются каноническими).
    phrases = [[s, e, spk, ("(одновременно) " if ov else "") + txt, ov]
               for s, e, spk, txt, ov in phrases]
    turns = group_turns(phrases)

    if "timecodes" in formats:
        path = base_out + "_diarize_timecodes.txt"
        with open(path, "w", encoding="utf-8") as f:
            for start, end, speaker, text, overlap in phrases:
                f.write(f"[{fmt_hms(start)} - {fmt_hms(end)}] {speaker}: {text}\n")
        written.append(path)

    if "txt" in formats:
        path = base_out + "_diarize.txt"
        with open(path, "w", encoding="utf-8") as f:
            for i, t in enumerate(turns):
                if i:
                    f.write("\n")
                f.write(f"[{t['speaker']}]\n")
                for ph in t["phrases"]:
                    f.write(ph + "\n")
        written.append(path)

    if "md" in formats:
        path = base_out + ".md"
        name = os.path.basename(base_out)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Транскрипция: {name}\n\n---\n\n")
            for t in turns:
                f.write(f"### {t['speaker']} — `{fmt_hms(t['start'])}`\n\n")
                para = " ".join(t["phrases"])
                # Абзац, начинающийся с "число." Markdown рисует как список —
                # экранируем точку, чтобы "2027." не превращалось в "1."
                para = re.sub(r"^(\d+)\.", r"\1\\.", para)
                f.write(para + "\n\n")
        written.append(path)

    if "srt" in formats:
        path = base_out + ".srt"
        with open(path, "w", encoding="utf-8") as f:
            for i, (start, end, speaker, text, _ov) in enumerate(phrases, 1):
                f.write(f"{i}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n"
                        f"{speaker}: {text}\n\n")
        written.append(path)

    return written


# ── Режимы ───────────────────────────────────────────────────────────
def extract_track(src, stream_index, dst):
    run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
         "-map", f"0:a:{stream_index}", "-ac", "1", "-ar", "16000", dst])


def extract_channel(src, channel, dst):  # channel: 0 = L, 1 = R
    run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
         "-af", f"pan=mono|c0=c{channel}", "-ar", "16000", dst])


def process(args):
    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        sys.exit(f"Файл не найден: {src}")

    audio_streams, duration = probe(src)
    if not audio_streams:
        sys.exit("В файле нет аудиодорожек.")
    log(f"Файл: {os.path.basename(src)} | длительность {fmt_hms(duration)} | "
        f"аудиодорожек: {len(audio_streams)}")
    for i, s in enumerate(audio_streams):
        title = s.get("tags", {}).get("title", "")
        log(f"  дорожка {i + 1}: {s.get('codec_name')} "
            f"{s.get('channels')}ch{(' — ' + title) if title else ''}")

    mode = args.mode
    if mode == "auto":
        mode = "tracks" if len(audio_streams) >= 2 else "diarize"
        log(f"Режим (авто): {mode}")

    # Пропуск уже обработанного: если ВСЕ выбранные форматы на месте
    out_dir_early = args.output_dir or os.path.dirname(src)
    base_early = os.path.join(out_dir_early,
                              os.path.splitext(os.path.basename(src))[0])
    sfx = ({"timecodes": "_timecodes.txt", "txt": ".txt",
            "md": ".md", "srt": ".srt"} if mode == "plain" else
           {"timecodes": "_diarize_timecodes.txt", "txt": "_diarize.txt",
            "md": ".md", "srt": ".srt"})
    expected = [base_early + sfx[f.strip()]
                for f in args.formats.split(",") if f.strip() in sfx]
    if expected and all(os.path.exists(p) for p in expected) \
            and not args.overwrite:
        log("ПРОПУЩЕНО: все выбранные форматы уже существуют:")
        for p in expected:
            log("  " + p)
        log("(включите «Перезаписывать готовые», чтобы пересоздать)")
        return

    args.model = resolve_model(args.model)
    if args.device == "auto":
        args.device = detect_device()
    if args.compute_type == "auto":
        args.compute_type = "int8_float32" if args.device == "cpu" else "float16"
    log(f"Устройство: {args.device} | модель: {args.model} | "
        f"compute: {args.compute_type} | потоков: {args.threads}")

    tmp = os.path.join(TEMP_ROOT, uuid.uuid4().hex[:8])
    os.makedirs(tmp, exist_ok=True)
    try:
        if mode == "tracks":
            indices = ([int(x) - 1 for x in args.tracks.split(",")]
                       if args.tracks else list(range(len(audio_streams))))
            missing = [i + 1 for i in indices if i >= len(audio_streams)]
            if missing:
                log(f"Дорожек {missing} в файле нет — игнорирую.")
            indices = [i for i in indices if i < len(audio_streams)]
            if len(indices) < 2:
                sys.exit("Для режима дорожек нужно минимум 2 выбранные "
                         "дорожки, имеющиеся в файле.")
            per_source = []
            for order, idx in enumerate(indices):
                wav = os.path.join(tmp, f"track{idx + 1}.wav")
                log(f"[{order + 1}/{len(indices)}] Извлечение дорожки {idx + 1}...")
                extract_track(src, idx, wav)
                log(f"[{order + 1}/{len(indices)}] Распознавание дорожки {idx + 1}...")
                srt = run_xxl(wav, tmp, args, diarize=False)
                per_source.append(parse_srt(srt, default_speaker=f"track:{idx}"))
            phrases = merge_phrases(per_source, mark_overlaps=True)

        elif mode == "stereo":
            per_source = []
            for ch, name in ((0, "L"), (1, "R")):
                wav = os.path.join(tmp, f"channel_{name}.wav")
                log(f"Извлечение канала {name}...")
                extract_channel(src, ch, wav)
                log(f"Распознавание канала {name}...")
                srt = run_xxl(wav, tmp, args, diarize=False)
                per_source.append(parse_srt(srt, default_speaker=f"channel:{ch}"))
            phrases = merge_phrases(per_source, mark_overlaps=True)

        elif mode == "plain":
            log("Распознавание без диаризации (монолог)...")
            srt = run_xxl(src, tmp, args, diarize=False)
            phrases = [p + [False] for p in parse_srt(srt, default_speaker=None)]
            phrases.sort(key=lambda p: p[0])

        else:  # diarize
            log("Распознавание с диаризацией...")
            srt = run_xxl(src, tmp, args, diarize=True)
            parsed = parse_srt(srt)
            # Сегменты без метки спикера = диаризация не нашла там речи.
            # Как правило, это галлюцинации Whisper на тишине — отбрасываем
            # (эталонный Subtitle Edit ведёт себя так же).
            untagged = [p for p in parsed if p[2] is None]
            if untagged:
                log(f"Отброшено фраз без спикера (галлюцинации на тишине): "
                    f"{len(untagged)}")
                for p in untagged:
                    log(f"  [{fmt_hms(p[0])}] {p[3][:60]}")
            phrases = merge_phrases([[p for p in parsed if p[2] is not None]],
                                    mark_overlaps=False)

        if not phrases:
            sys.exit("Не распознано ни одной фразы.")

        out_dir = args.output_dir or os.path.dirname(src)
        os.makedirs(out_dir, exist_ok=True)
        base_out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0])
        formats = [f.strip() for f in args.formats.split(",")]
        if mode == "plain":
            written = write_outputs_plain(phrases, base_out, formats)
            log(f"\nГотово. Фраз: {len(phrases)} (без разметки спикеров).")
        else:
            written = write_outputs(phrases, base_out, formats)
            speakers = sorted({p[2] for p in phrases})
            log(f"\nГотово. Фраз: {len(phrases)}, спикеров: {len(speakers)}.")
        for w in written:
            log("  " + w)
    finally:
        if args.model == "_custom":
            link = os.path.join(APP_DIR, "xxl", "_models",
                                "faster-whisper-_custom")
            try:
                os.rmdir(link)  # удаляет только ссылку, не содержимое
            except OSError:
                pass
        if args.keep_temp:
            log(f"Временные файлы сохранены: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description="Транскрибация с разметкой спикеров "
                                            "(Faster-Whisper XXL)")
    p.add_argument("file", help="аудио/видео файл")
    p.add_argument("--mode",
                   choices=["auto", "tracks", "stereo", "diarize", "plain"],
                   default="auto")
    p.add_argument("--speakers", type=int, default=None,
                   help="число спикеров (режим diarize)")
    p.add_argument("--tracks", default=None,
                   help="какие дорожки брать, напр. '1,2' (режим tracks)")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    p.add_argument("--language", default="ru")
    p.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    p.add_argument("--compute-type", dest="compute_type", default="auto")
    p.add_argument("--diarize-engine", dest="diarize_engine",
                   default="pyannote_v3.1",
                   choices=["pyannote_v3.0", "pyannote_v3.1",
                            "reverb_v1", "reverb_v2"])
    p.add_argument("--diarize-device", dest="diarize_device", default=None)
    p.add_argument("--vad", default="true", choices=["true", "false"],
                   help="VAD-фильтр тишины (по умолчанию true)")
    p.add_argument("--formats", default="timecodes,txt,md,srt")
    p.add_argument("--output-dir", dest="output_dir", default=None)
    p.add_argument("--xxl-args", dest="xxl_args", default=None,
                   help="дополнительные аргументы XXL одной строкой")
    p.add_argument("--keep-temp", action="store_true")
    p.add_argument("--overwrite", action="store_true",
                   help="обрабатывать, даже если результаты уже существуют")
    process(p.parse_args())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
