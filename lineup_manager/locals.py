"""Finds the local channels nearest a US ZIP code from the providers' own stream names.

"US ABC 25 (WEEK-DT2) Peoria" or "IL | Peoria | CBS WMBD" give a call sign and its city; cities are geocoded
(Open-Meteo, no key) and the nearest market's stations are kept by call sign.
"""
import json
import os, math, os, re, statistics
from concurrent.futures import ThreadPoolExecutor

PAREN = re.compile(r"\(([KW][A-Z]{2,3})(?:-[A-Z0-9]+)?\)\s*(.+)$")
PIPE = re.compile(r"^([A-Z]{2})\s*\|\s*([^|]+?)\s*\|\s*.*\b([KW][A-Z]{2,3})(?:-[A-Z0-9]+)?\s*$")
NETWORK = re.compile(r"\b(ABC|CBS|NBC|FOX|PBS|CW|MY)\b", re.I)
SPANISH = re.compile(r"telemundo|univision|galavision|unimas", re.I)
_cache = {}


def _get(url):
    import requests
    r = requests.get(url, timeout=15, headers={"User-Agent": "dispatcharr-lineup-manager"})
    r.raise_for_status()
    return r.json()


def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def zip_place(z):
    p = _get(f"https://api.zippopotam.us/us/{z}")["places"][0]
    return f"{p['place name']}, {p['state abbreviation']}", (float(p["latitude"]), float(p["longitude"]))


def city_near(city, home):
    """The US place called `city` a TV market is named after: among places with at least half the population of
    the biggest one, the closest to `home` (a small Washington, IL nearby shouldn't beat Washington, DC)."""
    if city not in _cache:
        try:
            res = _get("https://geocoding-api.open-meteo.com/v1/search?count=10&countryCode=US&name="
                       + __import__("urllib.parse").parse.quote(city)).get("results") or []
        except Exception:
            return None
        _cache[city] = [(r["latitude"], r["longitude"], r.get("population") or 0) for r in res if r.get("country_code") == "US"]
    hits = _cache[city]
    if not hits:
        return None
    top = max(h[2] for h in hits)
    return min(((h[0], h[1]) for h in hits if h[2] * 2 >= top), key=lambda p: miles(home, p))


def pick(names, place, home):
    cands, cities = [], set()
    for raw in names:
        n = (raw or "").strip()
        m = PIPE.match(n)
        if m:
            c = f"{m.group(2).strip()}, {m.group(1)}"
            cands.append((n, m.group(3), [c])); cities.add(c)
            continue
        m = PAREN.search(n)
        if m:
            cs = [c.strip() for c in m.group(2).split("/") if c.strip()]
            if cs:
                cands.append((n, m.group(1), cs)); cities.update(cs)
    with ThreadPoolExecutor(8) as ex:
        found = dict(zip(cities, ex.map(lambda c: city_near(c.rsplit(", ", 1)[0] if ", " in c else c, home), cities)))
    where = {c: miles(home, p) for c, p in found.items() if p}

    def dist(cs):  # a multi-city market is as far as its middle city
        ds = sorted(where[c] for c in cs if c in where)
        return ds[len(ds) // 2] if ds else None

    picks = [(n, call, cs[0], d) for n, call, cs in cands if (d := dist(cs)) is not None]
    if not picks or min(p[3] for p in picks) > 150:
        raise ValueError(f"no local stations found near {place}")
    near = min(p[3] for p in picks)
    picks = [p for p in picks if p[3] <= near + 25]
    letters = {}
    for _, call, city, _ in picks:
        letters[city] = letters.get(city, "") + call[0]
    side = "K" if home[1] < -90 else "W"  # K west of the Mississippi, W east
    closest = {}
    for n, _, _, d in picks:
        if (net := NETWORK.search(n)):
            k = net.group(1).upper(); closest[k] = min(closest.get(k, d), d)
    out, calls = [], set()
    for n, call, city, d in sorted(picks, key=lambda p: p[3]):
        l = letters[city]
        if ("K" in l and "W" in l and call[0] != side) or (home[1] < -100 and call[0] == "W"):
            continue
        if (net := NETWORK.search(n)) and d > closest[net.group(1).upper()] + 5:
            continue
        out.append({"name": n, "call": call, "city": city, "miles": round(d)})
        calls.add(call)
    return {"place": place, "include": r"\b(" + "|".join(sorted(calls)) + r")\b", "stations": out}


def find(zip_code, sources, data_dir=None):
    """Stations for `zip_code` from provider groups matching the Locals group's `sources` regexes."""
    from apps.channels.models import ChannelGroup, Stream
    path = os.path.join(data_dir, "geocode-cache.json") if data_dir else None
    if path and not _cache and os.path.exists(path):
        try:
            _cache.update({k: [tuple(x) for x in v] for k, v in json.load(open(path)).items()})
        except (OSError, ValueError):
            pass
    pats = [re.compile(p, re.I) for p in sources]
    groups = [g for g in ChannelGroup.objects.all() if any(p.search(g.name) for p in pats) and not SPANISH.search(g.name)]
    names = Stream.objects.filter(channel_group__in=groups, is_stale=False, m3u_account__is_active=True).values_list("name", flat=True)
    place, home = zip_place(zip_code.strip())
    res = pick(list(names), place, home)
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(_cache, f)
        except OSError:
            pass
    return res
