#!/usr/bin/env python3
# coding: utf-8

import re
import os
import sys
import requests
from difflib import SequenceMatcher

REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (PlaylistBuilder/3.1)"}


# -------------------- Утилиты --------------------
def read_lines(path: str):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return [l.rstrip("\n") for l in f]


def fetch_text(src: str) -> str:
    if src.startswith("http://") or src.startswith("https://"):
        try:
            r = requests.get(src, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"[WARN] fetch {src}: {e}", file=sys.stderr)
            return ""
    else:
        if os.path.exists(src):
            try:
                return open(src, encoding="utf-8", errors="ignore").read()
            except Exception as e:
                print(f"[WARN] open {src}: {e}", file=sys.stderr)
                return ""
        return ""


# -------------------- Универсальная нормализация названий --------------------

KNOWN_SUFFIXES = {
    "kids", "love", "plus", "international", "world", "hits",
    "family", "action", "europe", "asia", "africa", "uhd",
    "premium", "extra", "classic", "cinema", "music"
}


def split_name(name: str):
    if not name:
        return "", "", "sd"

    name = re.sub(r"\(.*?\)", "", name).strip()

    quality = "hd" if re.search(r"\bhd\b", name, flags=re.IGNORECASE) else "sd"

    name_clean = re.sub(r"\bhd\b", "", name, flags=re.IGNORECASE).strip()

    n = name_clean.lower()
    n = re.sub(r"[^0-9a-zа-яё\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()

    parts = n.split()

    if len(parts) == 1:
        return parts[0], "", quality

    if parts[-1] in KNOWN_SUFFIXES:
        base = " ".join(parts[:-1])
        suffix = parts[-1]
        return base, suffix, quality

    return n, "", quality


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# -------------------- Парсинг requestedIPTV --------------------
def parse_channels_spec(path: str):
    specs = []
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(";")]
        while len(parts) < 5:
            parts.append("")

        name, epg_id, group, sources, variant = parts[:5]

        if not name:
            continue

        priorities = []
        if sources:
            for x in sources.split(","):
                x = x.strip()
                if x.isdigit():
                    priorities.append(int(x))

        pick_index = None
        if variant.startswith("+") and variant[1:].isdigit():
            pick_index = int(variant[1:])

        base, suffix, quality = split_name(name)

        specs.append({
            "name": name,
            "desired_tvg": epg_id or None,
            "group_override": group or None,
            "priorities": priorities,
            "pick_index": pick_index,
            "base": base,
            "suffix": suffix,
            "quality": quality,
            "is_main": suffix == ""
        })

    return specs


# -------------------- Парсинг источников --------------------
def read_sources_list(path: str):
    srcs = []
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        cleaned = re.sub(r"^\s*\d+\)\s*", "", line)
        srcs.append(cleaned)

    return srcs


def parse_extinf_meta(extinf: str):
    meta = {}
    m = re.search(r'tvg-id\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta["tvg-id"] = m.group(1) if m else None

    parts = extinf.split(",", 1)
    title = parts[1].strip() if len(parts) > 1 else ""
    meta["title"] = title

    base, suffix, quality = split_name(title)
    meta["base"] = base
    meta["suffix"] = suffix
    meta["quality"] = quality

    return meta


def parse_m3u_entries(text: str):
    lines = text.splitlines()
    entries = []
    i = 0
    pos = 0

    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("#EXTINF"):
            pos += 1
            extinf = lines[i].rstrip("\r\n")
            extvlc = []
            j = i + 1

            while j < len(lines) and lines[j].strip().startswith("#"):
                if lines[j].strip().upper().startswith("#EXTVLCOPT"):
                    extvlc.append(lines[j].rstrip("\r\n"))
                j += 1

            url = ""
            if j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("#"):
                url = lines[j].strip()
                j += 1

            meta = parse_extinf_meta(extinf)
            full = "\n".join([extinf] + extvlc + [url])

            entries.append({
                "extinf": extinf,
                "extvlc": extvlc,
                "url": url,
                "meta": meta,
                "full": full,
                "pos": pos
            })

            i = j
        else:
            i += 1

    return entries


# -------------------- Фильтрация совпадений --------------------
def filter_matches(spec, matches):
    base = spec["base"]
    suffix = spec["suffix"]
    is_main = spec["is_main"]

    filtered = []

    for e in matches:
        mb = e["meta"]["base"]
        ms = e["meta"]["suffix"]

        if mb != base:
            continue

        if is_main:
            if ms != "":
                continue
        else:
            if ms != suffix:
                continue

        filtered.append(e)

    if not filtered:
        return []

    hd = [e for e in filtered if e["meta"]["quality"] == "hd"]
    if hd:
        return hd

    return filtered


# -------------------- Выбор дублей --------------------
def choose_from_matches(matches, pick_index):
    if not matches:
        return None
    if not pick_index:
        return matches[0]
    if 0 < pick_index <= len(matches):
        return matches[pick_index - 1]
    return None


# -------------------- Основная сборка --------------------
def build(channels_spec, sources_list, out_path="playlist.m3u"):
    specs = parse_channels_spec(channels_spec)
    sources_paths = read_sources_list(sources_list)

    entries_by_source = {}

    for idx, src in enumerate(sources_paths, start=1):
        print(f"[INFO] Загружаю источник #{idx}: {src}")
        txt = fetch_text(src)
        entries_by_source[idx] = parse_m3u_entries(txt)

    all_sources = sorted(entries_by_source.keys(), reverse=True)

    result = []
    seen = set()
    report = []

    for ch in specs:
        found = None

        priorities = sorted(ch["priorities"], reverse=True) if ch["priorities"] else all_sources

        for sidx in priorities:
            # 1) Поиск по tvg-id
            matches = []
            if ch["desired_tvg"]:
                matches = [
                    e for e in entries_by_source[sidx]
                    if (e["meta"]["tvg-id"] or "").lower() == ch["desired_tvg"].lower()
                ]
                matches = filter_matches(ch, matches)
                matches = sorted(matches, key=lambda x: x["pos"])
                found = choose_from_matches(matches, ch["pick_index"])

            # 2) Поиск по названию
            if not found:
                matches = [
                    e for e in entries_by_source[sidx]
                    if e["meta"]["base"] == ch["base"]
                ]
                matches = filter_matches(ch, matches)
                matches = sorted(matches, key=lambda x: x["pos"])
                found = choose_from_matches(matches, ch["pick_index"])

            if found:
                break

        if not found:
            report.append(f"{ch['name']}: не найден")
            continue

        block = found["full"]

        if ch["desired_tvg"]:
            if re.search(r'tvg-id=".*?"', block):
                block = re.sub(r'tvg-id=".*?"', f'tvg-id="{ch["desired_tvg"]}"', block)
            else:
                block = block.replace("#EXTINF:", f'#EXTINF: tvg-id="{ch["desired_tvg"]}" ', 1)

        # group-title
        if ch["group_override"]:
            # Ищем первую запятую — она отделяет атрибуты от названия
            if "," in block:
                before, after = block.split(",", 1)

                # before = "#EXTINF: ... атрибуты ..."
                # Удаляем старый group-title
                before = re.sub(r'group-title=".*?"', "", before)

                # Удаляем двойные пробелы
                before = re.sub(r'\s+', ' ', before).strip()

                # Гарантируем, что строка начинается с "#EXTINF:"
                if before.startswith("#EXTINF"):
                    # Вставляем новый group-title сразу после "#EXTINF:"
                    before = re.sub(
                        r'#EXTINF\s*:',
                        f'#EXTINF: group-title="{ch["group_override"]}" ',
                        before,
                        count=1
                    )

                # Собираем обратно
                block = f"{before},{after}"


        clean_name = re.sub(r"\(.*?\)", "", ch["name"]).strip()
        block = re.sub(r'(#EXTINF:[^,]*,)(.*)', r'\1' + clean_name, block)

        if block not in seen:
            result.append(block)
            seen.add(block)

    EPG_URLS = (
        'https://iptvx.one/EPG,'
        'http://epg.one/epg2.xml.gz,'
        'https://github.com/matthuisman/i.mjh.nz/raw/master/SamsungTVPlus/us.xml.gz,'
        'https://str-01.sunset-media.org/epg.xml'
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{EPG_URLS}"\n')
        f.write("\n\n".join(result) + "\n")

    return report



def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--channels", "-c", default="sources/requestedIPTV")
    p.add_argument("--sources", "-s", default="sources/sourcesplaylists")
    p.add_argument("--out", "-o", default="playlist.m3u")
    args = p.parse_args()

    rpt = build(args.channels, args.sources, out_path=args.out)
    print("Report:")
    for r in rpt:
        print(" -", r)


if __name__ == "__main__":
    main()
