#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
build_playlist.py
Создает/обновляет кастомный m3u плейлист на основе:
 - channels_spec.txt (файл 1) -- список каналов, желаемые tvg-id и приоритеты источников
 - sources_list.txt (файл 2) -- список источников (URL или локальные файлы), нумеруются с 1
Пример формата файлов в README ниже.
"""

import re
import sys
import os
import argparse
import requests
from urllib.parse import urlparse
from difflib import SequenceMatcher

# --- Настройки ---
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; PlaylistBuilder/1.0)"
HEADERS = {"User-Agent": USER_AGENT}

# --- Утилиты ---
def normalize_name(s: str) -> str:
    """Нормализация названий для сравнения: lower, убрать спецсимволы, убрать слова HD, 4K и т.п."""
    if not s:
        return ""
    s = s.lower()
    # убрать tvg-... в названии если попало
    s = re.sub(r'tvg-[a-z0-9"\-=_]+', '', s)
    # убрать common tags
    s = re.sub(r'\b(hd|sd|4k|fullhd|hd\.)\b', '', s)
    # убрать скобки и содержимое в них
    s = re.sub(r'\(.*?\)', '', s)
    # оставить только буквы, цифры и пробелы
    s = re.sub(r'[^0-9a-zа-яё\s]', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# --- Парсинг входных файлов ---
def parse_channels_spec(path: str):
    """
    Ожидаемый формат каждой строки:
      Название канала [tvg-id="desired"] [приоритеты через запятую]
    Примеры:
      Первый канал HD tvg-id="pervy" 1,2
      Россия 1 HD  tvg-id="ros" 1
      Пятый канал 1,2,3
    Возвращает список словарей:
      { 'raw': line, 'name': name, 'desired_tvg': tvg or None, 'priorities': [int,...] }
    """
    specs = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # найти tvg-id="..."
            m = re.search(r'tvg-id\s*=\s*"(.*?)"', line, flags=re.IGNORECASE)
            desired = m.group(1) if m else None
            # найти приоритеты: числа через запятую в конце или где-то
            pr = re.search(r'(\d+(?:\s*,\s*\d+)*)\s*$', line)
            priorities = []
            if pr:
                priorities = [int(x.strip()) for x in pr.group(1).split(",") if x.strip().isdigit()]
                # удалить приоритеты из имени
                name_part = line[:pr.start()].strip()
            else:
                name_part = line
            # удалить tvg-id часть из имени
            name_part = re.sub(r'tvg-id\s*=\s*".*?"', '', name_part, flags=re.IGNORECASE).strip()
            # final name
            name = name_part
            specs.append({
                "raw": line,
                "name": name,
                "norm_name": normalize_name(name),
                "desired_tvg": desired,
                "priorities": priorities or []
            })
    return specs

def read_sources_list(path: str):
    """
    Каждый непустой некомментированный рядок -- URL или локальный путь.
    Возвращает список строк; индексация для приоритетов начинается с 1.
    """
    sources = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            sources.append(line)
    return sources

# --- Загрузка контента источника ---
def fetch_source(src: str):
    if src.startswith("http://") or src.startswith("https://"):
        try:
            r = requests.get(src, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"[WARN] Не удалось загрузить {src}: {e}", file=sys.stderr)
            return ""
    else:
        # локальный файл
        try:
            with open(src, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            print(f"[WARN] Не удалось открыть {src}: {e}", file=sys.stderr)
            return ""

# --- Парсинг m3u блоков ---
def extract_entries(m3u_text: str):
    """
    Возвращает список записей, каждая запись как dict:
      { 'extinf': full_extinf_line, 'extvlc': [lines], 'url': url_line, 'meta': {tvg-id, tvg-logo, group-title, title} }
    Алгоритм: ищем строки #EXTINF и собираем до следующего #EXTINF.
    """
    lines = m3u_text.splitlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("#EXTINF"):
            extinf = line
            extvlc = []
            j = i + 1
            # collect following #EXTVLCOPT lines
            while j < len(lines) and lines[j].strip().upper().startswith("#EXTVLCOPT"):
                extvlc.append(lines[j].strip())
                j += 1
            # next non-empty line should be URL (or sometimes another #EXTINF if malformed)
            url = ""
            if j < len(lines):
                url = lines[j].strip()
                j += 1
            # parse meta from extinf
            meta = parse_extinf_meta(extinf)
            entries.append({
                "extinf": extinf,
                "extvlc": extvlc,
                "url": url,
                "meta": meta,
                "full_block": "\n".join([extinf] + extvlc + ([url] if url else []))
            })
            i = j
        else:
            i += 1
    return entries

def parse_extinf_meta(extinf_line: str):
    # tvg-id="..."
    meta = {}
    m = re.search(r'tvg-id\s*=\s*"(.*?)"', extinf_line, flags=re.IGNORECASE)
    meta['tvg-id'] = m.group(1) if m else None
    m = re.search(r'tvg-logo\s*=\s*"(.*?)"', extinf_line, flags=re.IGNORECASE)
    meta['tvg-logo'] = m.group(1) if m else None
    m = re.search(r'group-title\s*=\s*"(.*?)"', extinf_line, flags=re.IGNORECASE)
    meta['group-title'] = m.group(1) if m else None
    # title after comma
    parts = extinf_line.split(",", 1)
    title = parts[1].strip() if len(parts) > 1 else ""
    meta['title'] = title
    meta['norm_title'] = normalize_name(title)
    return meta

# --- Нормализация group-title ---
def normalize_group_title(g: str):
    if not g:
        return "Разное"
    g = g.strip()
    # простая нормализация: убрать лишние пробелы, привести к заглавным буквам слов
    g = re.sub(r'\s+', ' ', g)
    return " ".join([w.capitalize() for w in g.split(" ")])

# --- Основная логика поиска и сборки ---
def build_playlist(channels_spec_path, sources_list_path, output_path):
    specs = parse_channels_spec(channels_spec_path)
    sources = read_sources_list(sources_list_path)
    # загрузить все источники в память (индексация с 1)
    source_contents = {}
    for idx, src in enumerate(sources, start=1):
        print(f"[INFO] Загружаю источник #{idx}: {src}")
        source_contents[idx] = fetch_source(src)

    # распарсить все источники в entries_by_source
    entries_by_source = {}
    for idx, text in source_contents.items():
        entries_by_source[idx] = extract_entries(text)

    # результат: список полных блоков, в порядке указанном в channels_spec
    result_blocks = []
    seen_full_blocks = set()  # чтобы исключить полные дубликаты
    report = []  # сообщения о том, что было исправлено/добавлено

    for ch in specs:
        found = None
        found_reason = None
        found_source_idx = None
        found_entry = None

        # если приоритеты указаны, проверяем их в обратном порядке (большие номера первыми)
        priorities = sorted(ch['priorities'], reverse=True) if ch['priorities'] else list(sorted(entries_by_source.keys(), reverse=True))
        # сначала ищем совпадения по названию (приоритет)
        for src_idx in priorities:
            entries = entries_by_source.get(src_idx, [])
            # поиск по названию: ищем entry, где norm_title содержит norm_name или наоборот
            best = None
            best_score = 0.0
            for e in entries:
                if not e['meta']['title']:
                    continue
                # exact substring match after normalization
                if ch['norm_name'] and (ch['norm_name'] in e['meta']['norm_title'] or e['meta']['norm_title'] in ch['norm_name']):
                    score = 1.0
                else:
                    score = similar(ch['norm_name'], e['meta']['norm_title'])
                if score > best_score:
                    best_score = score
                    best = e
            # порог для принятия по названию
            if best and (best_score >= 0.75 or ch['norm_name'] in best['meta']['norm_title'] or best['meta']['norm_title'] in ch['norm_name']):
                found = best
                found_reason = "name"
                found_source_idx = src_idx
                found_entry = best
                break
        # если не найдено по названию, ищем по tvg-id
        if not found and ch['desired_tvg']:
            for src_idx in priorities:
                entries = entries_by_source.get(src_idx, [])
                for e in entries:
                    if e['meta'].get('tvg-id') and e['meta']['tvg-id'].lower() == ch['desired_tvg'].lower():
                        found = e
                        found_reason = "tvg-id"
                        found_source_idx = src_idx
                        found_entry = e
                        break
                if found:
                    break
        # если не найдено по desired_tvg, можно также попытаться найти по любому tvg-id совпадающему с именем
        if not found:
            for src_idx in priorities:
                entries = entries_by_source.get(src_idx, [])
                for e in entries:
                    # попытка: если tvg-id присутствует и нормализованное tvg-id похоже на имя
                    tid = e['meta'].get('tvg-id') or ""
                    if tid and (normalize_name(tid).find(ch['norm_name']) != -1 or ch['norm_name'].find(normalize_name(tid)) != -1):
                        found = e
                        found_reason = "tvg-id-similar"
                        found_source_idx = src_idx
                        found_entry = e
                        break
                if found:
                    break

        if found_entry:
            # подготовка блока для вставки: заменить tvg-id если в spec указан desired_tvg
            block = found_entry['full_block']
            # заменить/вставить tvg-id в extinf
            if ch['desired_tvg']:
                # заменить существующий tvg-id или добавить
                if re.search(r'tvg-id\s*=\s*".*?"', block, flags=re.IGNORECASE):
                    block = re.sub(r'(tvg-id\s*=\s*").*?(")', r'\1' + ch['desired_tvg'] + r'\2', block, flags=re.IGNORECASE)
                else:
                    # вставить после #EXTINF:-1
                    block = re.sub(r'(#EXTINF:[^\n]*?)\s*(,)', r'\1 tvg-id="' + ch['desired_tvg'] + r'"\2', block, count=1, flags=re.IGNORECASE)
            # нормализовать group-title
            if re.search(r'group-title\s*=\s*".*?"', block, flags=re.IGNORECASE):
                g = re.search(r'group-title\s*=\s*"(.*?)"', block, flags=re.IGNORECASE).group(1)
                newg = normalize_group_title(g)
                block = re.sub(r'(group-title\s*=\s*").*?(")', r'\1' + newg + r'\2', block, flags=re.IGNORECASE)
            else:
                # добавить group-title="Разное" если нет
                block = re.sub(r'(#EXTINF:[^\n]*?)\s*(,)', r'\1 group-title="Разное"\2', block, count=1, flags=re.IGNORECASE)

            # заменить title в extinf на точное имя из spec (если нужно)
            # если название в источнике существенно отличается от желаемого, подставим желаемое
            src_title = found_entry['meta'].get('title') or ""
            if ch['name'] and normalize_name(src_title) != ch['norm_name']:
                # заменить часть после запятой
                block = re.sub(r'(#EXTINF:[^\n]*?,).*\n', lambda m: m.group(1) + ch['name'] + "\n", block, count=1)
                report.append(f"У {ch['name']} Исправлено название (источник #{found_source_idx})")
            # если tvg-id был заменён
            if ch['desired_tvg'] and found_entry['meta'].get('tvg-id') and found_entry['meta']['tvg-id'].lower() != ch['desired_tvg'].lower():
                report.append(f"У {ch['name']} исправлен tvg-id ({found_entry['meta']['tvg-id']} -> {ch['desired_tvg']})")
            if ch['desired_tvg'] and not found_entry['meta'].get('tvg-id'):
                report.append(f"У {ch['name']} добавлен отсутствующий tvg-id ({ch['desired_tvg']})")
            # исключаем полные дубликаты
            if block not in seen_full_blocks:
                result_blocks.append(block)
                seen_full_blocks.add(block)
            else:
                print(f"[INFO] Дубликат пропущен для {ch['name']}", file=sys.stderr)
        else:
            print(f"[WARN] Не найден канал: {ch['name']}", file=sys.stderr)
            report.append(f"{ch['name']}: не найден в источниках")

    # Записать итоговый m3u
    with open(output_path, "w", encoding="utf-8") as out:
        out.write("#EXTM3U\n")
        for b in result_blocks:
            out.write(b.rstrip() + "\n")
            # убедиться, что между блоками есть пустая строка для читаемости
            out.write("\n")

    print(f"[OK] Плейлист записан в {output_path}")
    print("\nОтчет:")
    for r in report:
        print(" -", r)

# --- CLI ---
def main():
    parser = argparse.ArgumentParser(description="Build custom IPTV playlist from sources")
    parser.add_argument("--channels", "-c", required=True, help="Файл со списком каналов (channels_spec.txt)")
    parser.add_argument("--sources", "-s", required=True, help="Файл со списком источников (sources_list.txt)")
    parser.add_argument("--out", "-o", default="custom_playlist.m3u", help="Выходной m3u файл")
    args = parser.parse_args()
    build_playlist(args.channels, args.sources, args.out)

if __name__ == "__main__":
    main()
