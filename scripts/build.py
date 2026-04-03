import requests
import re
import os

SRC_RU = "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVstable.m3u8"
SRC_LT = "https://raw.githubusercontent.com/iptv-org/iptv/refs/heads/master/streams/lt.m3u"

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
]

EPG_REMAP = {
    "DelfiTV.lt@SD": "delfi-tv",
    "LietuvosRytasTV.lt@SD": "lietuvos-ryto-televizija",
}

EXCLUDE_PATTERNS = [
    r"\+1", r"\+2", r"\+4", r"\+7",
    r"International", r"Int",
    r"Premium", r"World", r"Europe",
    r"Baltic",
    r"UHD", r"4K",
    # r"24$"  # УБРАНО, чтобы не резать "Запад 24"
]

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
    """Название канала после запятой"""
    if "," not in extinf:
        return None
    name = extinf.split(",", 1)[1].strip()
    # убираем скобки, например (Калининград)
    name = re.sub(r"\(.*?\)", "", name).strip()
    return name

def normalize(name):
    """Нормализация для сравнения"""
    return re.sub(r"\s+", " ", name.lower()).strip()

def is_excluded(name):
    # точечно исключим "Мир 24", если нужно
    norm = normalize(name)
    if norm == "мир 24":
        return True

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

def build():
    ru_raw = download(SRC_RU)
    lt_raw = download(SRC_LT)

    ru_entries = parse_m3u(ru_raw)
    lt_entries = parse_m3u(lt_raw)

    existing = load_existing_playlist()

    final = ["#EXTM3U"]

    for channel in CHANNEL_ORDER:
        target_norm = normalize(channel)

        # fallback
        old_extinf, old_url = existing.get(target_norm, (None, None))

        # ищем в источниках
        if channel in ["Delfi TV", "Lietuvos Rytas TV"]:
            new = find_best_variant(lt_entries, channel)
        else:
            new = find_best_variant(ru_entries, channel)

        if new:
            extinf, url = new
            extinf = remap_epg(extinf)
        else:
            extinf, url = old_extinf, old_url

        if not extinf or not url:
            continue

        final.append(extinf)
        final.append(url)

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("\n".join(final))

    print("playlist.m3u updated")

if __name__ == "__main__":
    build()
