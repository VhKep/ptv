import requests
import re
import os
import hashlib
from datetime import datetime

# Файлы с внешними ссылками
RU_SOURCE_FILE = "sources/ru.txt"
LT_SOURCE_FILE = "sources/lt.txt"

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

# Замена EPG-ID
EPG_REMAP = {
    "DelfiTV.lt@SD": "delfi-tv",
    "LietuvosRytasTV.lt@SD": "lietuvos-ryto-televizija",
}

# Фильтры для исключения лишних вариантов
EXCLUDE_PATTERNS = [
    r"\+1", r"\+2", r"\+4", r"\+7",
    r"International", r"Int",
    r"Premium", r"World", r"Europe",
    r"Baltic",
    r"UHD", r"4K",
]

def load_source_url(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()

def download(url):
    return requests.get(url).text

def parse_m3u(text):
    lines = text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            extinf = lines[i]
            url = lines[i+1] if i+1 < len(lines) else ""
            result.append((extinf, url))
            i += 2
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
    for extinf, url in entries:
        name = extract_name(extinf)
        if name:
            result[normalize(name)] = (extinf, url)
    return result

def find_best_variant(entries, target):
    target_norm = normalize(target)
    hd_norm = normalize(target + " HD")

    hd = None
    sd = None

    for extinf, url in entries:
        name = extract_name(extinf)
        if not name:
            continue

        name_norm = normalize(name)

        if is_excluded(name):
            continue

        if name_norm == hd_norm:
            hd = (extinf, url)

        if name_norm == target_norm:
            sd = (extinf, url)

    return hd or sd

def remap_epg(extinf):
    for old, new in EPG_REMAP.items():
        extinf = re.sub(rf'tvg-id="{old}"', f'tvg-id="{new}"', extinf)
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

    ru_raw = download(ru_url)
    lt_raw = download(lt_url)

    ru_entries = parse_m3u(ru_raw)
    lt_entries = parse_m3u(lt_raw)

    existing = load_existing_playlist()

    old_hash = file_hash("playlist.m3u")

    final = ["#EXTM3U"]

    for channel in CHANNEL_ORDER:
        target_norm = normalize(channel)

        old_extinf, old_url = existing.get(target_norm, (None, None))

        if channel in ["Delfi TV", "Lietuvos Rytas TV", "M-1", "Power Hit Radio"]:
            new = find_best_variant(lt_entries, channel)
        else:
            new = find_best_variant(ru_entries, channel)

        if new:
            extinf, url = new
            extinf = remap_epg(extinf)
            log.append(f"[UPDATE] {channel}: обновлена ссылка")
        else:
            extinf, url = old_extinf, old_url
            log.append(f"[FALLBACK] {channel}: использована старая ссылка")

        if not extinf or not url:
            log.append(f"[SKIP] {channel}: нет данных")
            continue

        extinf = strip_epg_for_radio(extinf, channel)

        final.append(extinf)
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
