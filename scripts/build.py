import base64
import requests
import re
import os
import hashlib
from datetime import datetime

# Источники
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

RADIO_CHANNELS = ["M-1", "Power Hit Radio"]

EPG_REMAP = {
    "DelfiTV.lt@SD": "delfi-tv",
    "LietuvosRytasTV.lt@SD": "lietuvos-ryto-televizija",
}

FIXED_TVG_IDS = {
    "СТС": "sts",
    "СТС HD": "sts",
    "Домашний": "domashniy",
    "Домашний HD": "domashniy",
}

EXCLUDE_PATTERNS = [
    r"\+1", r"\+2", r"\+4", r"\+7",
    r"International", r"Int",
    r"Premium", r"World", r"Europe",
    r"Baltic",
    r"UHD", r"4K",
]

# -----------------------------
#  СКАЧИВАНИЕ TV ЧЕРЕЗ RAW С ПРАВИЛЬНЫМ ACCEPT
# -----------------------------
def download_dimonovich_tv():
    meta = requests.get(
        "https://api.github.com/repos/Dimonovich/TV/contents/FREE/TV?ref=Dimonovich"
    ).json()

    sha = meta["sha"]

    blob = requests.get(
        f"https://api.github.com/repos/Dimonovich/TV/git/blobs/{sha}"
    ).json()

    return base64.b64decode(blob["content"]).decode("utf-8")


# -----------------------------
#  ПАРСЕР M3U
# -----------------------------
def parse_m3u(text):
    lines = text.splitlines()
    result = []

    current_extinf = None
    current_vlcopts = []
    current_url = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF"):
            if current_extinf and current_url:
                result.append(
                    (current_extinf,
                     "\n".join(current_vlcopts) if current_vlcopts else None,
                     current_url)
                )
            current_extinf = line
            current_vlcopts = []
            current_url = None
            continue

        if line.startswith("#EXTVLCOPT"):
            if current_extinf:
                current_vlcopts.append(line)
            continue

        if re.match(r"^(https?|rtmp|rtsp)://", line):
            if current_extinf:
                current_url = line
                result.append(
                    (current_extinf,
                     "\n".join(current_vlcopts) if current_vlcopts else None,
                     current_url)
                )
                current_extinf = None
                current_vlcopts = []
                current_url = None
            continue

    return result


# -----------------------------
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# -----------------------------
def load_source_url(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()

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
    extinf = re.sub(r'group-title="[^"]+"', 'group-title="Развлекательные"', extinf)

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


# -----------------------------
#  ОСНОВНАЯ ЛОГИКА
# -----------------------------
def build():
    log = []

    ru_url = load_source_url(RU_SOURCE_FILE)
    lt_url = load_source_url(LT_SOURCE_FILE)

    ru_entries = parse_m3u(requests.get(ru_url).text)
    lt_entries = parse_m3u(requests.get(lt_url).text)

    # 🔥 ВСЕГДА АКТУАЛЬНЫЙ TV ИЗ DIMONOVICH
    third_text = download_dimonovich_tv()
    third_entries = parse_m3u(third_text)

    existing = load_existing_playlist()
    old_hash = file_hash("playlist.m3u")

    final = ["#EXTM3U"]

    for channel in CHANNEL_ORDER:
        target_norm = normalize(channel)

        old_extinf, old_vlcopt, old_url = existing.get(target_norm, (None, None, None))

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
