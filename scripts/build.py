import requests
import re

# Источники
SRC_RU = "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVstable.m3u8"
SRC_LT = "https://raw.githubusercontent.com/iptv-org/iptv/refs/heads/master/streams/lt.m3u"

# Каналы из первого плейлиста
RU_CHANNELS = [
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
    "Запад 34",
]

# Каналы из второго плейлиста
LT_CHANNELS = [
    "Delfi TV",
    "Lietuvos Rytas TV",
]

# Замена EPG-ID
EPG_REMAP = {
    "DelfiTV.lt@SD": "delfi-tv",
    "LietuvosRytasTV.lt@SD": "lietuvos-ryto-televizija",
}

def download(url):
    print(f"Downloading {url}")
    return requests.get(url).text

def parse_m3u(text):
    """Возвращает список (extinf, url)"""
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

def filter_channels(entries, names):
    """Фильтрует каналы по названию"""
    result = []
    for extinf, url in entries:
        for name in names:
            if name.lower() in extinf.lower():
                result.append((extinf, url))
    return result

def remap_epg(extinf):
    """Заменяет tvg-id"""
    for old, new in EPG_REMAP.items():
        extinf = re.sub(rf'tvg-id="{old}"', f'tvg-id="{new}"', extinf)
    return extinf

def build():
    ru_raw = download(SRC_RU)
    lt_raw = download(SRC_LT)

    ru_entries = parse_m3u(ru_raw)
    lt_entries = parse_m3u(lt_raw)

    ru_final = filter_channels(ru_entries, RU_CHANNELS)
    lt_final = filter_channels(lt_entries, LT_CHANNELS)

    # Применяем замену EPG-ID
    lt_final = [(remap_epg(extinf), url) for extinf, url in lt_final]

    # Собираем итоговый плейлист
    output = ["#EXTM3U"]

    for extinf, url in ru_final + lt_final:
        output.append(extinf)
        output.append(url)

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("\n".join(output))

    print("playlist.m3u updated")

if __name__ == "__main__":
    build()
