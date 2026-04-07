#!/usr/bin/env python3
# coding: utf-8

import re
import os
import sys
import requests
from difflib import SequenceMatcher

REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (PlaylistBuilder/1.0)"}


# -------------------- Утилиты --------------------
def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r'\b(hd|sd|4k|fullhd)\b', ' ', s)
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'[^0-9a-zа-яё\s]', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def read_lines(path: str):
    with open(path, encoding='utf-8', errors='ignore') as f:
        return [l.rstrip('\n') for l in f]


# -------------------- Парсинг requestedIPTV --------------------
def parse_channels_spec(path: str):
    specs = []
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue

        parts = [p.strip() for p in line.split(';')]
        while len(parts) < 5:
            parts.append("")

        name, epg_id, group, sources, variant = parts[:5]

        if not name:
            continue

        priorities = []
        if sources:
            for x in sources.split(','):
                x = x.strip()
                if x.isdigit():
                    priorities.append(int(x))

        pick_index = None
        if variant.startswith('+') and variant[1:].isdigit():
            pick_index = int(variant[1:])

        specs.append({
            "name": name,
            "norm_name": normalize_name(name),
            "desired_tvg": epg_id or None,
            "group_override": group or None,
            "priorities": priorities,
            "pick_index": pick_index
        })
    return specs

def read_sources_list(path: str):
    srcs = []
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue

        # Удаляем нумерацию вида "1)" или "12)"
        cleaned = re.sub(r'^\s*\d+\)\s*', '', line)

        srcs.append(cleaned)
    return srcs


# -------------------- Загрузка и парсинг M3U --------------------
def fetch_text(src: str) -> str:
    if src.startswith('http://') or src.startswith('https://'):
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
                return open(src, encoding='utf-8', errors='ignore').read()
            except Exception as e:
                print(f"[WARN] open {src}: {e}", file=sys.stderr)
                return ""
        return ""


def parse_extinf_meta(extinf: str):
    meta = {}
    m = re.search(r'tvg-id\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['tvg-id'] = m.group(1) if m else None
    m = re.search(r'tvg-logo\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['tvg-logo'] = m.group(1) if m else None
    m = re.search(r'group-title\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['group-title'] = m.group(1) if m else None
    parts = extinf.split(',', 1)
    title = parts[1].strip() if len(parts) > 1 else ""
    meta['title'] = title
    meta['norm_title'] = normalize_name(title)
    return meta


def parse_m3u_entries(text: str):
    lines = text.splitlines()
    entries = []
    i = 0
    pos_counter = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("#EXTINF"):
            pos_counter += 1
            extinf = lines[i].rstrip('\r\n')
            extvlc = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("#"):
                if lines[j].strip().upper().startswith("#EXTVLCOPT"):
                    extvlc.append(lines[j].rstrip('\r\n'))
                j += 1
            url = ""
            if j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("#"):
                url = lines[j].strip()
                j += 1
            meta = parse_extinf_meta(extinf)
            parts = [extinf] + extvlc + ([url] if url else [])
            full = "\n".join(parts)
            entries.append({
                "extinf": extinf,
                "extvlc": extvlc,
                "url": url,
                "meta": meta,
                "full": full,
                "pos": pos_counter
            })
            i = j
        else:
            i += 1
    return entries


def choose_from_matches(matches, pick_index):
    if not matches:
        return None
    if not pick_index:
        return matches[0]
    if 0 < pick_index <= len(matches):
        return matches[pick_index - 1]
    return None


# -------------------- Сборка плейлиста --------------------
def build(channels_spec, sources_list, out_path="custom_playlist.m3u"):
    specs = parse_channels_spec(channels_spec)
    sources_paths = read_sources_list(sources_list)

    entries_by_source = {}

    # источники нумеруются строго 1..N
    for idx, src in enumerate(sources_paths, start=1):
        print(f"[INFO] Загружаю источник #{idx}: {src}")
        txt = fetch_text(src)
        entries_by_source[idx] = parse_m3u_entries(txt)

    all_source_ids = sorted(entries_by_source.keys(), reverse=True)

    result_blocks = []
    seen = set()
    report = []

    for ch in specs:
        found = None
        found_src = None

        if ch['priorities']:
            priorities = sorted(ch['priorities'], reverse=True)
        else:
            priorities = all_source_ids

        # 1) поиск по tvg-id
        if ch['desired_tvg']:
            for sidx in priorities:
                matches = [
                    e for e in entries_by_source.get(sidx, [])
                    if (e['meta'].get('tvg-id') or "").lower() == ch['desired_tvg'].lower()
                ]
                if matches:
                    matches = sorted(matches, key=lambda x: x['pos'])
                    found = choose_from_matches(matches, ch['pick_index'])
                    if found:
                        found_src = sidx
                        break

        # 2) поиск по названию
        if not found:
            for sidx in priorities:
                matches = []
                for e in entries_by_source.get(sidx, []):
                    nt = e['meta']['norm_title']
                    if ch['norm_name'] in nt or nt in ch['norm_name']:
                        matches.append(e)
                    else:
                        if similar(ch['norm_name'], nt) >= 0.78:
                            matches.append(e)
                if matches:
                    matches = sorted(matches, key=lambda x: x['pos'])
                    found = choose_from_matches(matches, ch['pick_index'])
                    if found:
                        found_src = sidx
                        break

        if not found:
            report.append(f"{ch['name']}: не найден")
            continue

        block = found['full']

        # tvg-id
        if ch['desired_tvg']:
            if re.search(r'tvg-id\s*=\s*".*?"', block, flags=re.IGNORECASE):
                block = re.sub(
                    r'(tvg-id\s*=\s*")(.*?)(")',
                    lambda m: m.group(1) + ch['desired_tvg'] + m.group(3),
                    block,
                    flags=re.IGNORECASE
                )
            else:
                block = re.sub(
                    r'(^#EXTINF:[^\r\n]*?)(,)',
                    lambda m: m.group(1) + f' tvg-id="{ch["desired_tvg"]}"' + m.group(2),
                    block,
                    count=1,
                    flags=re.IGNORECASE | re.MULTILINE
                )

        # group-title
        if ch['group_override']:
            newg = ch['group_override']
            if re.search(r'group-title\s*=\s*".*?"', block, flags=re.IGNORECASE):
                block = re.sub(
                    r'(group-title\s*=\s*")(.*?)(")',
                    lambda m: m.group(1) + newg + m.group(3),
                    block,
                    flags=re.IGNORECASE
                )
            else:
                block = re.sub(
                    r'(^#EXTINF:[^\r\n]*?)(,)',
                    lambda m: m.group(1) + f' group-title="{newg}"' + m.group(2),
                    block,
                    count=1,
                    flags=re.IGNORECASE | re.MULTILINE
                )

        # название
        src_title = found['meta'].get('title') or ""
        if normalize_name(src_title) != ch['norm_name']:
            clean_name = ch['name']
            block = re.sub(
                r'(^#EXTINF:[^\r\n]*?,)[^\r\n]*([\r\n])',
                lambda m: m.group(1) + clean_name + m.group(2),
                block,
                count=1,
                flags=re.IGNORECASE | re.MULTILINE
            )

        if block not in seen:
            result_blocks.append(block)
            seen.add(block)

    header = "#EXTM3U\n"
    content = header + "\n\n".join(result_blocks) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return report


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--channels", "-c", default="sources/requestedIPTV")
    p.add_argument("--sources", "-s", default="sources/sourcesplaylists")
    p.add_argument("--out", "-o", default="custom_playlist.m3u")
    args = p.parse_args()

    rpt = build(args.channels, args.sources, out_path=args.out)
    print("Report:")
    for r in rpt:
        print(" -", r)


if __name__ == "__main__":
    main()
