#!/usr/bin/env python3
# coding: utf-8
"""
build_playlist.py
Собирает custom_playlist.m3u на основе:
 - sources/requestedIPTV
 - sources/sourcesplaylists
 - (опционально) playlist.m3u
Выход: custom_playlist.m3u в корне репозитория.
"""

import re, os, sys, requests
from difflib import SequenceMatcher

REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (PlaylistBuilder/1.0)"}

def normalize_name(s: str) -> str:
    if not s: return ""
    s = s.lower()
    s = re.sub(r'\b(hd|sd|4k|fullhd)\b', ' ', s)
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'[^0-9a-zа-яё\s]', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def similar(a,b):
    return SequenceMatcher(None, a, b).ratio()

def read_lines(path):
    with open(path, encoding='utf-8', errors='ignore') as f:
        return [l.rstrip('\n') for l in f]

def parse_channels_spec(path):
    specs=[]
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith('#'): continue
        m = re.search(r'tvg-id\s*=\s*"(.*?)"', line, flags=re.IGNORECASE)
        desired = m.group(1) if m else None
        pr = re.search(r'(\d+(?:\s*,\s*\d+)*)\s*$', line)
        priorities=[]
        if pr:
            priorities=[int(x.strip()) for x in pr.group(1).split(',') if x.strip().isdigit()]
            name_part = line[:pr.start()].strip()
        else:
            name_part = line
        name_part = re.sub(r'tvg-id\s*=\s*".*?"', '', name_part, flags=re.IGNORECASE).strip()
        specs.append({
            "name": name_part,
            "norm_name": normalize_name(name_part),
            "desired_tvg": desired,
            "priorities": priorities
        })
    return specs

def read_sources_list(path):
    srcs=[]
    for raw in read_lines(path):
        line = raw.strip()
        if not line or line.startswith('#'): continue
        srcs.append(line)
    return srcs

def fetch_text(src):
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
            return open(src, encoding='utf-8', errors='ignore').read()
        return ""

def parse_m3u_entries(text):
    lines = text.splitlines()
    entries=[]
    i=0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith('#EXTINF'):
            extinf = line
            extvlc=[]
            j=i+1
            while j < len(lines) and lines[j].strip().upper().startswith('#EXTVLCOPT'):
                extvlc.append(lines[j].strip()); j+=1
            url = lines[j].strip() if j < len(lines) else ""
            j += 1
            meta = parse_extinf_meta(extinf)
            full = "\n".join([extinf] + extvlc + ([url] if url else []))
            entries.append({"extinf":extinf,"extvlc":extvlc,"url":url,"meta":meta,"full":full})
            i = j
        else:
            i += 1
    return entries

def parse_extinf_meta(extinf):
    meta={}
    m = re.search(r'tvg-id\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['tvg-id'] = m.group(1) if m else None
    m = re.search(r'tvg-logo\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['tvg-logo'] = m.group(1) if m else None
    m = re.search(r'group-title\s*=\s*"(.*?)"', extinf, flags=re.IGNORECASE)
    meta['group-title'] = m.group(1) if m else None
    parts = extinf.split(',',1)
    title = parts[1].strip() if len(parts)>1 else ""
    meta['title'] = title
    meta['norm_title'] = normalize_name(title)
    return meta

def normalize_group_title(g):
    if not g: return "Разное"
    g = re.sub(r'\s+',' ', g.strip())
    return " ".join([w.capitalize() for w in g.split(' ')])

def build(channels_spec, sources_list, extra_local=None, out_path="custom_playlist.m3u"):
    specs = parse_channels_spec(channels_spec)
    sources = read_sources_list(sources_list)
    # добавить локальный temp плейлист как источник, если есть
    if extra_local and os.path.exists(extra_local):
        sources.insert(0, extra_local)
    # загрузить и распарсить
    entries_by_source = {}
    for idx, src in enumerate(sources, start=1):
        txt = fetch_text(src)
        entries_by_source[idx] = parse_m3u_entries(txt)

    result_blocks=[]
    seen=set()
    report=[]

    for ch in specs:
        found=None; found_reason=None; found_src=None
        priorities = sorted(ch['priorities'], reverse=True) if ch['priorities'] else sorted(entries_by_source.keys(), reverse=True)
        # поиск по названию
        for sidx in priorities:
            for e in entries_by_source.get(sidx,[]):
                if not e['meta']['title']: continue
                if ch['norm_name'] and (ch['norm_name'] in e['meta']['norm_title'] or e['meta']['norm_title'] in ch['norm_name']):
                    found=e; found_reason='name'; found_src=sidx; break
                score = similar(ch['norm_name'], e['meta']['norm_title'])
                if score >= 0.78:
                    found=e; found_reason='name_sim'; found_src=sidx; break
            if found: break
        # поиск по tvg-id
        if not found and ch['desired_tvg']:
            for sidx in priorities:
                for e in entries_by_source.get(sidx,[]):
                    tid = e['meta'].get('tvg-id') or ""
                    if tid and tid.lower() == ch['desired_tvg'].lower():
                        found=e; found_reason='tvg-id'; found_src=sidx; break
                if found: break
        # fallback по похожему tvg-id
        if not found:
            for sidx in priorities:
                for e in entries_by_source.get(sidx,[]):
                    tid = e['meta'].get('tvg-id') or ""
                    if tid and (normalize_name(tid).find(ch['norm_name'])!=-1 or ch['norm_name'].find(normalize_name(tid))!=-1):
                        found=e; found_reason='tvg-id-sim'; found_src=sidx; break
                if found: break

        if found:
            block = found['full']
            # заменить/вставить tvg-id в extinf (безопасно, через callable)
            if ch['desired_tvg']:
                if re.search(r'tvg-id\s*=\s*".*?"', block, flags=re.IGNORECASE):
                    # безопасная замена через callable, чтобы избежать проблем с backreferences
                    block = re.sub(
                        r'(tvg-id\s*=\s*")(.*?)(")',
                        lambda m: m.group(1) + ch['desired_tvg'] + m.group(3),
                        block,
                        flags=re.IGNORECASE
                    )
                else:
                    block = re.sub(
                        r'(#EXTINF:[^\n]*?)\s*(,)',
                        lambda m: m.group(1) + ' tvg-id="' + ch['desired_tvg'] + '"' + m.group(2),
                        block,
                        count=1,
                        flags=re.IGNORECASE
                    )

            # нормализовать group-title
            if re.search(r'group-title\s*=\s*".*?"', block, flags=re.IGNORECASE):
                g = re.search(r'group-title\s*=\s*"(.*?)"', block, flags=re.IGNORECASE).group(1)
                newg = normalize_group_title(g)
                # безопасная замена через callable
                block = re.sub(
                    r'(group-title\s*=\s*")(.*?)(")',
                    lambda m: m.group(1) + newg + m.group(3),
                    block,
                    flags=re.IGNORECASE
                )
            else:
                # добавить group-title="Разное" если нет
                block = re.sub(r'(#EXTINF:[^\n]*?)\s*(,)', r'\1 group-title="Разное"\2', block, count=1, flags=re.IGNORECASE)

            # заменить отображаемое название, если отличается
            src_title = found['meta'].get('title') or ""
            if ch['name'] and normalize_name(src_title) != ch['norm_name']:
                # безопасная замена названия в первой строке EXTINF, учёт CRLF и многострочного текста
                clean_name = ch['name'].replace('\r', ' ').replace('\n', ' ').strip()
                block = re.sub(
                    r'(^#EXTINF:[^\r\n]*?,)[^\r\n]*([\r\n])',
                    lambda m: m.group(1) + clean_name + m.group(2),
                    block,
                    count=1,
                    flags=re.IGNORECASE | re.MULTILINE
                )
                report.append(f"{ch['name']}: исправлено название (источник #{found_src})")

            # сообщения по tvg-id
            if ch['desired_tvg'] and found['meta'].get('tvg-id') and found['meta']['tvg-id'].lower() != ch['desired_tvg'].lower():
                report.append(f"{ch['name']}: tvg-id {found['meta']['tvg-id']} -> {ch['desired_tvg']}")
            if ch['desired_tvg'] and not found['meta'].get('tvg-id'):
                report.append(f"{ch['name']}: добавлен tvg-id {ch['desired_tvg']}")
            if block not in seen:
                result_blocks.append(block)
                seen.add(block)
        else:
            report.append(f"{ch['name']}: не найден")


    # записать
    header = "#EXTM3U\n"
    content = header + "\n\n".join(b.rstrip() for b in result_blocks) + ("\n" if result_blocks else "")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return report

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--channels", default="sources/requestedIPTV")
    p.add_argument("--sources", default="sources/sourcesplaylists")
    p.add_argument("--temp", default="playlist.m3u")
    p.add_argument("--out", default="custom_playlist.m3u")
    args = p.parse_args()
    rpt = build(args.channels, args.sources, extra_local=args.temp, out_path=args.out)
    print("Report:")
    for r in rpt: print(" -", r)
