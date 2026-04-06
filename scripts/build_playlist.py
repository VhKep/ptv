#!/usr/bin/env python3
# coding: utf-8
"""
build_playlist.py

Собирает custom_playlist.m3u на основе:
 - sources/requestedIPTV (табличный формат с ;)
 - sources/sourcesplaylists (список источников)
 - опционально playlist.m3u

Формат requestedIPTV:
# Название ; EPG ID ; Группа ; Источники ; Вариант
СТС HD ; sts ; Развлекательные ; 1,3 ;
Домашний HD ; domashny ; Развлекательные ; 1,3 ; +2
"""

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
    """
    Формат строки:
    Название ; EPG ID ; Группа ; Источники ; Вариант

    Название   : отображаемое имя и ключ для поиска
    EPG ID     : tvg-id (без tvg-id=""), если пусто — не трогаем tvg-id
    Группа     : group-title, если пусто — не трогаем group-title
    Источники  : "1,3" или пусто (все источники)
    Вариант    : "" или "+n" (n-й дубль сверху)
    """
    specs = []
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue

        parts = [p.strip() for p in line.split(';')]
        # гарантируем 5 столбцов
        while len(parts) < 5:
            parts.append("")
        name, epg_id, group, sources, variant = parts[:5]

        if not name:
            continue

        # приоритеты источников
        priorities = []
        if sources:
            for x in sources.split(','):
                x = x.strip()
                if x.isdigit():
                    priorities.append(int(x))

        # вариант (+n)
        pick_index = None
        if variant:
            variant = variant.strip()
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
        srcs.append(line)
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
    """
    Возвращает список записей в порядке появления:
      { extinf, extvlc: [..], url, meta, full, pos }
    Сохраняет все #EXTVLCOPT строки между EXTINF и URL.
    """
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
            # собираем все строки, начинающиеся с #
            while j < len(lines) and lines[j].strip().startswith("#"):
                if lines[j].strip().upper().startswith("#EXTVLCOPT"):
                    extvlc.append(lines[j].rstrip('\r\n'))
                j += 1
            # следующая некомментированная строка — URL
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


def normalize_group_title(g: str):
    if not g:
        return ""
    g = re.sub(r'\s+', ' ', g.strip())
    return " ".join([w.capitalize() for w in g.split(' ')])


# -------------------- Выбор дубликатов --------------------
def choose_from_matches(matches: list, pick_index: int | None):
    """
    matches: список найденных записей (уже отсортированных по pos)
    pick_index: None или 1-based индекс
    """
    if not matches:
        return None
    if not pick_index:
        return matches[0]
    if pick_index > 0 and pick_index <= len(matches):
        return matches[pick_index - 1]
    return None


# -------------------- Сборка плейлиста --------------------
def build(channels_spec: str, sources_list: str, extra_local: str = None, out_path: str = "custom_playlist.m3u"):
    specs = parse_channels_spec(channels_spec)
    sources = read_sources_list(sources_list)

    # добавить локальный temp плейлист как источник с наивысшим приоритетом (если есть)
    if extra_local and os.path.exists(extra_local):
        sources.insert(0, extra_local)

    # загрузить и распарсить все источники
    entries_by_source = {}
    for idx, src in enumerate(sources, start=1):
        print(f"[INFO] Загружаю источник #{idx}: {src}")
        txt = fetch_text(src)
        entries_by_source[idx] = parse_m3u_entries(txt)

    result_blocks = []
    seen = set()
    report = []

    all_source_ids = sorted(entries_by_source.keys(), reverse=True)

    for ch in specs:
        found = None
        found_reason = None
        found_src = None

        # приоритеты: если не заданы — все источники
        if ch['priorities']:
            priorities = sorted(ch['priorities'], reverse=True)
        else:
            priorities = all_source_ids

        # 1) поиск по названию
        for sidx in priorities:
            entries = entries_by_source.get(sidx, [])
            matches = []
            for e in entries:
                if not e['meta']['title']:
                    continue
                nt = e['meta']['norm_title']
                if ch['norm_name'] and (ch['norm_name'] in nt or nt in ch['norm_name']):
                    matches.append(e)
                else:
                    score = similar(ch['norm_name'], nt)
                    if score >= 0.78:
                        matches.append(e)
            if matches:
                matches = sorted(matches, key=lambda x: x.get('pos', 0))
                chosen = choose_from_matches(matches, ch.get('pick_index'))
                if chosen:
                    found = chosen
                    found_reason = 'name'
                    found_src = sidx
                    break

        # 2) поиск по tvg-id (EPG ID), если не нашли по названию
        if not found and ch['desired_tvg']:
            for sidx in priorities:
                entries = entries_by_source.get(sidx, [])
                matches = []
                for e in entries:
                    tid = (e['meta'].get('tvg-id') or "").lower()
                    if tid and tid == ch['desired_tvg'].lower():
                        matches.append(e)
                if matches:
                    matches = sorted(matches, key=lambda x: x.get('pos', 0))
                    chosen = choose_from_matches(matches, ch.get('pick_index'))
                    if chosen:
                        found = chosen
                        found_reason = 'tvg-id'
                        found_src = sidx
                        break

        # 3) fallback по похожему tvg-id
        if not found:
            for sidx in priorities:
                entries = entries_by_source.get(sidx, [])
                matches = []
                for e in entries:
                    tid = e['meta'].get('tvg-id') or ""
                    if not tid:
                        continue
                    ntid = normalize_name(tid)
                    if ntid and (ntid.find(ch['norm_name']) != -1 or ch['norm_name'].find(ntid) != -1):
                        matches.append(e)
                if matches:
                    matches = sorted(matches, key=lambda x: x.get('pos', 0))
                    chosen = choose_from_matches(matches, ch.get('pick_index'))
                    if chosen:
                        found = chosen
                        found_reason = 'tvg-id-sim'
                        found_src = sidx
                        break

        if found:
            block = found['full']

            # tvg-id: меняем/добавляем только если EPG ID указан
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
                        lambda m: m.group(1) + ' tvg-id="' + ch['desired_tvg'] + '"' + m.group(2),
                        block,
                        count=1,
                        flags=re.IGNORECASE | re.MULTILINE
                    )

            # group-title: меняем только если поле Группа заполнено
            if ch['group_override']:
                newg = normalize_group_title(ch['group_override'])
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
                        lambda m: m.group(1) + ' group-title="' + newg + '"' + m.group(2),
                        block,
                        count=1,
                        flags=re.IGNORECASE | re.MULTILINE
                    )

            # заменить отображаемое название, если отличается
            src_title = found['meta'].get('title') or ""
            if ch['name'] and normalize_name(src_title) != ch['norm_name']:
                clean_name = ch['name'].replace('\r', ' ').replace('\n', ' ').strip()
                block = re.sub(
                    r'(^#EXTINF:[^\r\n]*?,)[^\r\n]*([\r\n])',
                    lambda m: m.group(1) + clean_name + m.group(2),
                    block,
                    count=1,
                    flags=re.IGNORECASE | re.MULTILINE
                )
                report.append(f"{ch['name']}: исправлено название (источник #{found_src}, причина {found_reason})")

            # сообщения по tvg-id
            if ch['desired_tvg']:
                src_tvg = (found['meta'].get('tvg-id') or "").lower()
                if src_tvg and src_tvg != ch['desired_tvg'].lower():
                    report.append(f"{ch['name']}: tvg-id {src_tvg} -> {ch['desired_tvg']}")
                if not src_tvg:
                    report.append(f"{ch['name']}: добавлен tvg-id {ch['desired_tvg']}")

            if block not in seen:
                result_blocks.append(block)
                seen.add(block)
            else:
                print(f"[INFO] Дубликат пропущен для {ch['name']}", file=sys.stderr)
        else:
            report.append(f"{ch['name']}: не найден")

    # Записать итоговый m3u
    header = "#EXTM3U\n"
    content = header + "\n\n".join(b.rstrip() for b in result_blocks) + ("\n" if result_blocks else "")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return report


# -------------------- CLI --------------------
def main():
    import argparse
    p = argparse.ArgumentParser(description="Build custom IPTV playlist from sources")
    p.add_argument("--channels", "-c", default="sources/requestedIPTV", help="Файл со списком каналов (табличный формат с ;) ")
    p.add_argument("--sources", "-s", default="sources/sourcesplaylists", help="Файл со списком источников")
    p.add_argument("--temp", "-t", default="playlist.m3u", help="Локальный временный плейлист (опционально)")
    p.add_argument("--out", "-o", default="custom_playlist.m3u", help="Выходной m3u файл")
    args = p.parse_args()

    rpt = build(args.channels, args.sources, extra_local=args.temp, out_path=args.out)
    print("Report:")
    for r in rpt:
        print(" -", r)


if __name__ == "__main__":
    main()
