# -*- coding: utf-8 -*-
"""Конвертер транскриптов в читабельный MD (диалог + статистика).

Понимает несколько диалектов входных файлов (автоопределение):
  1. Построчный с таймкодами:
     [00:03:28 - 00:03:43] Спикер 1: текст        (наш транскрибер)
     [03:28.190 --> 03:43.740] [SPEAKER_00]: текст (Subtitle Edit / XXL)
     [03:28 - 03:43] Спикер №1: текст              (GigaAMGUI)
  2. SRT/VTT-субтитры, в теле которых «Имя: текст».
  3. Блоки по спикерам: строка [Имя], под ней фразы (GigaAMGUI _diarize.txt).
  4. Голый диалог «Имя: текст» без таймкодов (статистика только по тексту).

Непонятые строки не глотаются молча: считаются и показываются в логе.

Выход: «<имя> (расшифровка).md» — шапка со статистикой (как в транскриптах
пользователя) + диалог `**Имя:** текст` по ходам спикеров.
"""

import argparse
import os
import re
import sys

TIME = r"(\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)"
LINE_TC = re.compile(
    rf"^\[?\s*{TIME}\s*(?:-->|—|–|-)\s*{TIME}\s*\]?\s+"
    rf"\[?([^:\[\]]{{1,40}}?)\]?\s*:\s*(.*)$")
SRT_TIME = re.compile(rf"^{TIME}\s*-->\s*{TIME}")
BLOCK_HDR = re.compile(r"^\[([^\[\]]{1,40})\]$")
PLAIN = re.compile(r"^([^:]{1,40}?)\s*:\s+(.+)$")
MD_TURN = re.compile(r"^\*\*(.{1,40}?):\*\*\s*(.*)$")
MD_STAT = re.compile(r"^(-\s*)(.{1,40}?)(\s*:\s*\d+[.,]?\d*%\s*)$")
SPEAKER_BRACKETS = re.compile(r"^\[?([^\[\]]+?)\]?$")


def log(msg):
    print(msg, flush=True)


def to_sec(t):
    parts = [float(p) for p in t.replace(",", ".").split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def fmt_hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fmt_short(sec):
    """Короткий таймкод: без ведущих нулей (3:28, 15:42, 1:02:05)."""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ── парсеры диалектов ────────────────────────────────────────────────
def parse_timecoded(nonempty):
    phrases, bad = [], []
    for line in nonempty:
        m = LINE_TC.match(line.strip())
        if m:
            phrases.append([to_sec(m[1]), to_sec(m[2]),
                            m[3].strip(), m[4].strip()])
        else:
            bad.append(line)
    return phrases, bad


def parse_srt_blocks(text):
    phrases, bad = [], []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines or lines[0].upper().startswith("WEBVTT"):
            continue
        idx = 0
        if lines[idx].isdigit():
            idx += 1
        if idx >= len(lines):
            continue
        tm = SRT_TIME.match(lines[idx])
        if not tm:
            bad.append(lines[0])
            continue
        start, end = to_sec(tm[1]), to_sec(tm[2])
        body = " ".join(lines[idx + 1:]).strip()
        pm = PLAIN.match(body)
        if pm:
            spk = SPEAKER_BRACKETS.match(pm[1].strip())[1]
            phrases.append([start, end, spk.strip(), pm[2].strip()])
        elif body:
            phrases.append([start, end, None, body])
    return phrases, bad


def parse_blocks(nonempty):
    phrases, bad, current = [], [], None
    for line in nonempty:
        m = BLOCK_HDR.match(line.strip())
        if m:
            current = m[1].strip()
        elif current:
            phrases.append([None, None, current, line.strip()])
        else:
            bad.append(line)
    ok = bool(phrases) and len(bad) <= len(nonempty) * 0.2
    return phrases, bad, ok


def parse_plain(nonempty):
    phrases, bad = [], []
    for line in nonempty:
        m = PLAIN.match(line.strip())
        if m and len(m[1].strip()) <= 30:
            phrases.append([None, None, m[1].strip(), m[2].strip()])
        elif phrases:  # перенос строки = продолжение предыдущей реплики
            phrases[-1][3] += " " + line.strip()
        else:
            bad.append(line)
    names = {p[2] for p in phrases}
    ok = bool(phrases) and len(names) <= 12
    return phrases, bad, ok


def parse_md(text):
    """Готовый MD: ходы «**Имя:** текст» (продолжения абзаца — к ходу).

    Реплики ищутся только после разделителя «---», чтобы жирные строки
    шапки («**По объему текста...**») не принимались за спикеров.
    """
    lines = text.splitlines()
    has_separator = any(l.strip() == "---" for l in lines)
    body = not has_separator
    phrases, current = [], None
    for line in lines:
        s = line.strip()
        if s == "---":
            body = True
            continue
        if not body:
            continue
        m = MD_TURN.match(s)
        if m:
            current = [None, None, m[1].strip(), m[2].strip()]
            phrases.append(current)
        elif s and current and not s.startswith(("#", "*", "-", "`")):
            current[3] += " " + s
    return phrases


def parse_file(path):
    """-> (phrases [start, end, speaker, text], название диалекта, bad)."""
    text = read_text(path)
    nonempty = [l for l in text.splitlines() if l.strip()]
    if not nonempty:
        return [], "пустой файл", []

    if path.lower().endswith(".md"):
        phrases = parse_md(text)
        if len(phrases) >= 2:
            return phrases, "готовый MD (переименование)", []
        return [], "md без реплик «**Имя:** текст»", []

    phrases, bad = parse_timecoded(nonempty)
    if len(phrases) >= max(3, len(nonempty) * 0.5):
        return phrases, "строки с таймкодами", bad

    phrases, bad = parse_srt_blocks(text)
    if len(phrases) >= 3:
        if all(p[2] is None for p in phrases):
            return [], "субтитры без имён спикеров", []
        return phrases, "субтитры (srt/vtt)", bad

    phrases, bad, ok = parse_blocks(nonempty)
    if ok:
        return phrases, "блоки по спикерам", bad

    phrases, bad, ok = parse_plain(nonempty)
    if ok:
        return phrases, "диалог без таймкодов", bad

    return [], "формат не распознан", nonempty[:5]


# ── сборка MD ────────────────────────────────────────────────────────
def escape_md_start(text):
    return re.sub(r"^(\d+)\.(\s)", r"\1\\.\2", text)


def build_md(phrases, rename, excluded, with_stats, turn_timecodes):
    included = [p for p in phrases if p[2] not in excluded]
    turns = []
    # break_next: на этом месте была вырезанная реплика — не склеивать
    # соседние ходы оставшегося спикера, начать новый абзац
    break_next = False
    for start, _end, spk, text in phrases:
        if spk in excluded:
            break_next = True
            continue
        name = rename.get(spk, spk) or "Без имени"
        if turns and turns[-1]["name"] == name and not break_next:
            turns[-1]["texts"].append(text)
        else:
            turns.append({"name": name, "start": start, "texts": [text]})
        break_next = False

    out = ["# Расшифровка"]
    has_tc = any(p[0] is not None for p in included)
    if with_stats:
        if has_tc:
            total_end = max((p[1] or 0) for p in included)
            out.append(f"**Длительность:** {round(total_end / 60)} мин.")
        speech, chars = {}, {}
        for start, end, spk, text in included:
            name = rename.get(spk, spk) or "Без имени"
            if start is not None and end is not None:
                speech[name] = speech.get(name, 0.0) + (end - start)
            chars[name] = chars.get(name, 0) + len(text)
        if has_tc and sum(speech.values()) > 0:
            out += ["", "**По времени речи:**", ""]
            total = sum(speech.values())
            for name, val in sorted(speech.items(), key=lambda x: -x[1]):
                out.append(f"- {name}: {100 * val / total:.1f}%"
                           .replace(".", ","))
        if sum(chars.values()) > 0:
            out += ["**По объему текста (символы):**", ""]
            total_c = sum(chars.values())
            for name, val in sorted(chars.items(), key=lambda x: -x[1]):
                out.append(f"- {name}: {round(100 * val / total_c)}%")
    if excluded:
        shown = ", ".join(sorted(excluded))
        out += ["", f"*Речь исключена из расшифровки: {shown}*"]
    out += ["", "---"]

    for t in turns:
        tc = (f" `{fmt_short(t['start'])}`"
              if (turn_timecodes and t["start"] is not None) else "")
        body = escape_md_start(" ".join(t["texts"]))
        out.append(f"\n**{t['name']}:**{tc} {body}")

    return "\n".join(out) + "\n", len(turns)


STRIP_SUFFIXES = ("_diarize_timecodes", "_timecodes", "_diarize")


def process(args):
    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        sys.exit(f"Файл не найден: {src}")

    is_md = src.lower().endswith(".md")
    base = os.path.splitext(os.path.basename(src))[0]
    for sfx in STRIP_SUFFIXES:
        if base.endswith(sfx):
            base = base[: -len(sfx)]
            break
    out_dir = args.output_dir or os.path.dirname(src)
    os.makedirs(out_dir, exist_ok=True)
    suffix = " (переименовано)" if is_md else " (расшифровка)"
    out_path = os.path.join(out_dir, f"{base}{suffix}.md")

    if os.path.exists(out_path) and not args.overwrite:
        log(f"ПРОПУЩЕНО: результат уже существует — {out_path}")
        log("(включите «Перезаписывать готовые», чтобы пересоздать)")
        return

    phrases, dialect, bad = parse_file(src)
    log(f"Формат входа: {dialect}")
    if not phrases:
        if bad:
            log("Примеры строк, которые не удалось разобрать:")
            for b in bad[:5]:
                log("  " + b[:100])
        sys.exit("Не удалось разобрать файл.")

    speakers = sorted({p[2] for p in phrases if p[2]})
    log(f"Фраз: {len(phrases)}, спикеров: {len(speakers)} "
        f"({', '.join(speakers)})")
    if bad:
        log(f"ВНИМАНИЕ: не распознано строк: {len(bad)} — примеры:")
        for b in bad[:5]:
            log("  " + b[:100])

    rename = {}
    for pair in args.map or []:
        if "=" in pair:
            old, new = pair.split("=", 1)
            rename[old.strip()] = new.strip()
    excluded = set(args.exclude or [])

    if is_md and not excluded:
        # Только переименование: правим имена по месту, шапку и цифры
        # сохраняем как есть
        out_lines = []
        for line in read_text(src).splitlines():
            m = MD_TURN.match(line.strip())
            if m and m[1].strip() in rename:
                line = line.replace(f"**{m[1].strip()}:**",
                                    f"**{rename[m[1].strip()]}:**", 1)
            else:
                sm = MD_STAT.match(line.strip())
                if sm and sm[2].strip() in rename:
                    line = sm[1] + rename[sm[2].strip()] + sm[3]
            out_lines.append(line)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
        log(f"Готово (переименование, шапка сохранена): {out_path}")
        return
    if is_md and excluded:
        log("Исключение спикера из готового MD: документ пересобирается, "
            "проценты времени в шапке опускаются (их нет в исходнике).")

    md, n_turns = build_md(phrases, rename, excluded,
                           with_stats=not args.no_stats,
                           turn_timecodes=args.turn_timecodes)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"Готово: {out_path} (ходов: {n_turns})")


def main():
    p = argparse.ArgumentParser(description="Транскрипт -> читабельный MD")
    p.add_argument("file", help="txt/srt/vtt транскрипт")
    p.add_argument("--map", action="append", metavar="СТАРОЕ=НОВОЕ",
                   help="переименование спикера (можно несколько раз)")
    p.add_argument("--exclude", action="append", metavar="ИМЯ",
                   help="исключить речь спикера (можно несколько раз)")
    p.add_argument("--no-stats", action="store_true",
                   help="без шапки со статистикой")
    p.add_argument("--turn-timecodes", action="store_true",
                   help="таймкод начала каждого хода")
    p.add_argument("--output-dir", dest="output_dir", default=None)
    p.add_argument("--overwrite", action="store_true")
    process(p.parse_args())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
