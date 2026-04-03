import requests
import re
import os
import hashlib
from datetime import datetime

# Внешние источники
RU_SOURCE_FILE = "sources/ru.txt"
LT_SOURCE_FILE = "sources/lt.txt"
THIRD_SOURCE_FILE = "sources/third.txt"  # новый источник

# Порядок каналов
CHANNEL_ORDER = [
    "Первый канал",
    "Россия 1",
    "Россия К",
    "НТВ",
    "Пятый канал",
    "ОТР",
    "ТВ центр",
    "СТС",
    "Домашний",
    "Мир",
    "Запад 24",
    "Delfi TV",
    "Lietuvos Rytas TV",
    "M-1",
    "Power Hit Radio",
]

# Радио-каналы (убираем tvg-id)
RADIO_CHANNELS = ["M-1", "Power Hit Radio"]

# EPG remap
EPG_REMAP = {
    "DelfiTV.lt@SD": "delfi-tv",
    "LietuvosRytasTV.lt@SD": "lietuvos-ryto-televizija",
}

# Фильтры
EXCLUDE_PATTERNS = [
    r"\+1", r"\+2", r"\+4", r"\+7",
    r"International", r"Int",
    r"Premium", r"World", r"Europe",
    r"Baltic",
    r"UHD", r"4K",
]

# tvg-id для СТС и Домашний
FIXED_TVG_IDS = {
    "СТС": "sts",
    "СТС HD": "sts",
    "Домашний": "domashniy",
    "Домашний HD": "domashniy",
}

def load_source_url(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()

def download(url):
    return requests.get(url).text

def parse_m3u(text):
    """
    Поддержка формата:
    #EXTINF
    #EXTVLCOPT (опционально)
    URL
    """
    lines = text.splitlines()
    result = []
    i = 0

    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            extinf = lines[i]
            vlcopt = None
            url = ""

            # Следующая строка может быть #EXTVLCOPT
            if i + 1 < len(lines) and lines[i+1].startswith("#EXTVLCOPT"):
                vlcopt = lines[i+1]
                url = lines[i+2] if i+2 < len(lines) else ""
                i += 3
            else:
                url = lines[i+1] if i+1 < len(lines) else ""
                i += 2

            result.append((extinf, vlcopt, url))
        else:
            i += 1

    return result

def extract_name(extinf):
    if "," not in extinf:
        return None
    name = extinf.split(",", 1)[1].strip()
    name = re.sub(r"\(.*?\)", "", name).strip()
    return name

def normalize(name):
    return re.sub(r"\s+", " ", name.lower()).strip()

def is_excluded(name):
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return True
    return False

def load_existing_playlist():
    if not os.path.exists("playlist.m3u"):
        return {}
    with open("playlist.m3u", encoding="utf-8") as f:
        text = f.read()
    entries = parse_m3u(text)
    result = {}
    for extinf, vlcopt, url in entries:
        name = extract_name(extinf)
        if name:
            result[normalize(name)] = (extinf, vlcopt, url)
    return result

def find_best_variant(entries, target):
    target_norm = normalize(target)
    hd_norm = normalize(target + " HD")

    hd = None
    sd = None

    for extinf, vlcopt, url in entries:
        name = extract_name(extinf)
        if not name:
            continue

        name_norm = normalize(name)

        if is_excluded(name):
            continue

        if name_norm == hd_norm:
            hd = (extinf, vlcopt, url)

        if name_norm == target_norm:
            sd = (extinf, vlcopt, url)

    return hd or sd

def remap_epg(extinf):
    for old, new in EPG_REMAP.items():
        extinf = re.sub(rf'tvg-id="{old}"', f'tvg-id="{new}"', extinf)
    return extinf

def fix_group_and_tvg(extinf, channel):
    # Группа
    extinf = re.sub(r'group-title="[^"]+"', 'group-title="Развлекательные"', extinf)

    # tvg-id для СТС и Домашний
    for key, tvgid in FIXED_TVG_IDS.items():
        if normalize(key) == normalize(channel):
            if 'tvg-id="' in extinf:
                extinf = re.sub(r'tvg-id="[^"]+"', f'tvg-id="{tvgid}"', extinf)
            else:
                extinf = extinf.replace(",", f' tvg-id="{tvgid}",')
    return extinf

def strip_epg_for_radio(extinf, channel):
    if channel in RADIO_CHANNELS:
        extinf = re.sub(r'tvg-id="[^"]+"', '', extinf)
    return extinf

def file_hash(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def write_log(log_lines, changed):
    with open("update.log", "a", encoding="utf-8") as f:
        f.write("\n" + "="*60 + "\n")
        f.write(f"Обновление: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Изменения: {'ДА' if changed else 'НЕТ'}\n")
        f.write("-"*60 + "\n")
        for line in log_lines:
            f.write(line + "\n")
        f.write("="*60 + "\n")

def build():
    log = []

    ru_url = load_source_url(RU_SOURCE_FILE)
    lt_url = load_source_url(LT_SOURCE_FILE)
    third_url = load_source_url(THIRD_SOURCE_FILE)

    ru_entries = parse_m3u(download(ru_url))
    lt_entries = parse_m3u(download(lt_url))
    third_entries = parse_m3u(download(third_url))

    existing = load_existing_playlist()
    old_hash = file_hash("playlist.m3u")

    final = ["#EXTM3U"]

    for channel in CHANNEL_ORDER:
        target_norm = normalize(channel)

        old_extinf, old_vlcopt, old_url = existing.get(target_norm, (None, None, None))

        # Приоритет источников
        if channel in ["СТС", "Домашний"]:
            new = find_best_variant(third_entries, channel)
        elif channel in ["Delfi TV", "Lietuvos Rytas TV", "M-1", "Power Hit Radio"]:
            new = find_best_variant(lt_entries, channel)
        else:
            new = find_best_variant(ru_entries, channel)

        if new:
            extinf, vlcopt, url = new
            extinf = remap_epg(extinf)
            extinf = fix_group_and_tvg(extinf, channel)
            log.append(f"[UPDATE] {channel}: обновлена ссылка")
        else:
            extinf, vlcopt, url = old_extinf, old_vlcopt, old_url
            log.append(f"[FALLBACK] {channel}: использована старая ссылка")

        if not extinf or not url:
            log.append(f"[SKIP] {channel}: нет данных")
            continue

        extinf = strip_epg_for_radio(extinf, channel)

        final.append(extinf)
        if vlcopt:
            final.append(vlcopt)
        final.append(url)

    new_content = "\n".join(final)
    new_hash = hashlib.md5(new_content.encode("utf-8")).hexdigest()

    changed = new_hash != old_hash

    if not changed:
        print("Нет изменений — файл не обновлён.")
        write_log(log, changed=False)
        return

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write(new_content)

    write_log(log, changed=True)

    print("\n".join(log))
    print("playlist.m3u updated")

if __name__ == "__main__":
    build()
