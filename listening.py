# -*- coding: utf-8 -*-
"""«Файл для прослушивания»: нормализация громкости + сведение + сжатие.

Вход — любой аудио/видео файл:
  * мультитрек (OBS)  -> каждая дорожка нормализуется отдельно, затем сведение
                         в моно ИЛИ в стерео «голоса по ушам» (дор.1 -> L, 2 -> R);
  * обычный файл      -> нормализация + перекодирование (видео отбрасывается).

Нормализация: двухпроходный ffmpeg loudnorm (EBU R128, речь: I=-16 LUFS).
Выход: «<имя> (прослушивание).opus|mp3» рядом с исходником или в --output-dir.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.path.join(APP_DIR, "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(APP_DIR, "ffmpeg", "ffprobe.exe")
TEMP_ROOT = os.path.join(APP_DIR, "temp")

# качество: пресет -> (битрейт opus, битрейт mp3)
QUALITY = {"compact": ("32k", "64k"),
           "standard": ("64k", "128k"),
           "high": ("96k", "192k"),
           "music": ("256k", "320k")}
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VERBOSE = False  # --verbose: транслировать родной вывод ffmpeg в лог


def log(msg):
    print(msg, flush=True)


def run_ff(args_list, duration=None, label=""):
    """Запуск ffmpeg; прогресс в % (или полный вывод при VERBOSE)."""
    if VERBOSE:
        # «Шланг»: родной вывод ffmpeg построчно в лог (universal newlines
        # Python сам режет \r-перерисовки статистики на строки)
        proc = subprocess.Popen([FFMPEG, "-y", "-stats"] + args_list,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log("  " + line)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg завершился с ошибкой (см. вывод выше)")
        return
    base = [FFMPEG, "-y", "-loglevel", "error"]
    if duration:
        base += ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(base + args_list,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    if duration:
        last = -25
        for line in proc.stdout:
            if line.startswith(("out_time_ms=", "out_time_us=")):
                try:
                    t = int(line.split("=")[1]) / 1_000_000
                except ValueError:
                    continue
                pct = min(100, int(t / duration * 100))
                if pct - last >= 25:
                    last = pct
                    log(f"      {label}{pct}%")
    _out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.strip()[:400]}")


def probe_audio(path):
    out = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW).stdout
    data = json.loads(out or "{}")
    streams = [s for s in data.get("streams", [])
               if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return streams, duration


def measure_loudnorm(path, duration=None):
    """Первый проход: измерение громкости, возвращает параметры для второго."""
    cmd = [FFMPEG, "-hide_banner"]
    if duration:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += ["-i", path, "-af", LOUDNORM + ":print_format=json", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    if duration:
        last = -25
        for line in proc.stdout:
            if line.startswith(("out_time_ms=", "out_time_us=")):
                try:
                    t = int(line.split("=")[1]) / 1_000_000
                except ValueError:
                    continue
                pct = min(100, int(t / duration * 100))
                if pct - last >= 25:
                    last = pct
                    log(f"      измерение громкости: {pct}%")
    _out, stderr_text = proc.communicate()
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text, re.S)
    if not m:
        return None
    d = json.loads(m.group(0))
    return (f":measured_I={d['input_i']}:measured_TP={d['input_tp']}"
            f":measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}"
            f":offset={d['target_offset']}:linear=true")


def normalize_track(src, stream_idx, dst, do_norm, duration=None):
    """Извлечь дорожку в WAV (моно 48к) с двухпроходной нормализацией."""
    base = ["-i", src, "-map", f"0:a:{stream_idx}", "-ac", "1", "-ar", "48000"]
    if do_norm:
        tmp_raw = dst + ".raw.wav"
        run_ff(base + [tmp_raw], duration, "извлечение: ")
        measured = measure_loudnorm(tmp_raw, duration)
        af = LOUDNORM + (measured or "")
        run_ff(["-i", tmp_raw, "-af", af, "-ar", "48000", dst],
               duration, "нормализация: ")
        os.remove(tmp_raw)
    else:
        run_ff(base + [dst], duration, "извлечение: ")


def encode(inputs_filtergraph, out_path, fmt, bitrate, extra_inputs,
           duration=None):
    codec = ["-c:a", "libopus"] if fmt == "opus" else ["-c:a", "libmp3lame"]
    rate = ["-ar", "48000"] if fmt == "opus" else ["-ar", "44100"]
    cmd = []
    for p in extra_inputs:
        cmd += ["-i", p]
    if inputs_filtergraph:
        cmd += ["-filter_complex", inputs_filtergraph, "-map", "[out]"]
    cmd += codec + ["-b:a", bitrate] + rate + [out_path]
    run_ff(cmd, duration, "кодирование: ")


def process(args):
    global VERBOSE
    VERBOSE = args.verbose
    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        sys.exit(f"Файл не найден: {src}")
    streams, duration = probe_audio(src)
    if not streams:
        sys.exit("В файле нет аудиодорожек.")

    fmt = args.format
    bitrate = QUALITY[args.quality][0 if fmt == "opus" else 1]
    out_dir = args.output_dir or os.path.dirname(src)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    # Теги шаблона имени
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(src))
    n = max(1, args.index)
    name = ((args.name_template or "{имя}")
            .replace("{имя}", base)
            .replace("{дата}", mtime.strftime("%Y-%m-%d"))
            .replace("{время_чмс}", mtime.strftime("%H-%M-%S"))
            .replace("{время_чм}", mtime.strftime("%H-%M"))
            .replace("{сегодня}", datetime.date.today().isoformat())
            .replace("{###}", f"{n:03d}")
            .replace("{##}", f"{n:02d}")
            .replace("{#}", str(n))
            .strip()) or base
    name = re.sub(r'[<>:"/\\|?*]', "_", name)  # запрещённые в Windows символы
    out_path = os.path.join(out_dir, f"{name}.{fmt}")
    if os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(src):
        sys.exit("Имя результата совпадает с исходным файлом — "
                 "измените шаблон имени или папку вывода.")

    if os.path.exists(out_path) and not args.overwrite:
        log(f"ПРОПУЩЕНО: результат уже существует — {out_path}")
        log("(включите «Перезаписывать готовые», чтобы пересоздать)")
        return

    indices = ([int(x) - 1 for x in args.tracks.split(",")]
               if args.tracks else list(range(len(streams))))
    indices = [i for i in indices if i < len(streams)]
    if not indices:
        sys.exit("Ни одной из выбранных дорожек в файле нет.")
    log(f"Файл: {os.path.basename(src)} | дорожек: {len(streams)}, "
        f"берём: {[i + 1 for i in indices]} | {fmt} {bitrate} "
        f"{args.channels} | нормализация: {'да' if not args.no_normalize else 'нет'}")

    if args.channels == "original" and len(streams) > 1:
        sys.exit("«Как в исходнике» — только для файлов с одной дорожкой.\n"
                 "У этого файла их несколько: выберите «Моно» (сведение) "
                 "или «Стерео» (по ушам).")

    tmp = os.path.join(TEMP_ROOT, uuid.uuid4().hex[:8])
    os.makedirs(tmp, exist_ok=True)
    try:
        if args.channels != "original" and len(indices) >= 2:
            wavs = []
            for n, idx in enumerate(indices):
                log(f"Дорожка {idx + 1} [{n + 1}/{len(indices)}]:")
                w = os.path.join(tmp, f"t{n}.wav")
                normalize_track(src, idx, w, not args.no_normalize, duration)
                wavs.append(w)
            if args.channels == "stereo":
                if len(wavs) > 2:
                    log(f"ВНИМАНИЕ: режим «по ушам» использует только две "
                        f"дорожки — беру {indices[0] + 1} (L) и "
                        f"{indices[1] + 1} (R), остальные не войдут.")
                log("Сведение в стерео (голоса по ушам)...")
                fg = "[0:a][1:a]join=inputs=2:channel_layout=stereo[out]"
                encode(fg, out_path, fmt, bitrate, wavs[:2], duration)
            else:
                log("Сведение в моно...")
                n = len(wavs)
                fg = ("".join(f"[{i}:a]" for i in range(n))
                      + f"amix=inputs={n}:normalize=0[out]")
                encode(fg, out_path, fmt, bitrate, wavs, duration)
        else:
            log("Одна дорожка: нормализация и перекодирование...")
            w = os.path.join(tmp, "t0.wav")
            # для обычного файла сохраняем каналы источника при stereo,
            # при mono — сводим в один канал
            idx = indices[0]
            if args.channels == "mono":
                normalize_track(src, idx, w, not args.no_normalize, duration)
            else:
                run_ff(["-i", src, "-map", f"0:a:{idx}", "-ar", "48000", w],
                       duration, "извлечение: ")
                if not args.no_normalize:
                    measured = measure_loudnorm(w, duration)
                    w2 = os.path.join(tmp, "t0n.wav")
                    run_ff(["-i", w, "-af", LOUDNORM + (measured or ""),
                            "-ar", "48000", w2], duration, "нормализация: ")
                    w = w2
            encode(None, out_path, fmt, bitrate, [w], duration)

        size_mb = os.path.getsize(out_path) / 1024 / 1024
        src_mb = os.path.getsize(src) / 1024 / 1024
        ratio = src_mb / size_mb if size_mb else 0
        log(f"Готово: {out_path}")
        log(f"Размер: {src_mb:.1f} МБ -> {size_mb:.1f} МБ "
            f"(сжатие в {ratio:.1f} раза)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description="Файл для прослушивания "
                                            "(нормализация + сжатие)")
    p.add_argument("file")
    p.add_argument("--format", choices=["opus", "mp3"], default="opus")
    p.add_argument("--quality", choices=list(QUALITY), default="standard")
    p.add_argument("--channels", choices=["mono", "stereo", "original"],
                   default="mono")
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--tracks", default=None, help="какие дорожки, напр. '1,2'")
    p.add_argument("--output-dir", dest="output_dir", default=None)
    p.add_argument("--verbose", action="store_true",
                   help="полный вывод ffmpeg вместо процентов")
    p.add_argument("--overwrite", action="store_true",
                   help="создавать, даже если результат уже существует")
    p.add_argument("--name-template", dest="name_template",
                   default="{имя} (прослушивание)",
                   help="шаблон имени результата, {имя} = имя исходника")
    p.add_argument("--index", type=int, default=1,
                   help="порядковый номер файла в пачке (для тегов {#})")
    process(p.parse_args())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
