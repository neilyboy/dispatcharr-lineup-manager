"""Parse provider-local channel names into portable station metadata, and build the national locals guide.

Ported from the dispatcharr-smart-lineup lab.

The parser only uses stream/group metadata already stored in Dispatcharr. It never opens
stream URLs and never contacts providers.
"""
import re
from collections import Counter, defaultdict

STATE = r"([A-Z]{2}(?:-[A-Z]{2})*)"
CALL = r"([KW][A-Z]{2,3}(?:-[A-Z0-9]+)?)"
NETWORK = r"(?:ABC|CBS|NBC|FOX|PBS|CW|MY|IND|Telemundo|Univision|Unimas|Galavision)"
PIPE_MARKET_PIPE = re.compile(r"^" + STATE + r"\s*\|\s*([^|]+?)\s*\|\s*.*\b" + CALL + r"\s*$", re.I)
PIPE_MARKET_INLINE = re.compile(r"^" + STATE + r"\s*\|\s*(.*?)\s+\b" + NETWORK + r"\b.*\b" + CALL + r"\s*$", re.I)
PAREN_CALL_CITY = re.compile(r"\(" + CALL + r"\)\s*(.+)$", re.I)
BRACKET_CITY_STATE = re.compile(r"\[([A-Za-z ._]+?)_([A-Z]{2})\]\s*(?:[A-Z]+-)?([KW][A-Z]{2,3})?", re.I)
NETWORK_RE = re.compile(r"\b(ABC|CBS|NBC|FOX|PBS|CW|MY|IND|Telemundo|Univision|Unimas|Galavision)\b", re.I)
NETWORK_CHANNEL_RE = re.compile(r"\b(?:ABC|CBS|NBC|FOX|PBS|CW|MY|IND|Telemundo|Univision|Unimas|Galavision)\s+(\d+(?:/\d+)?)\b", re.I)
US_LOCAL_GROUP = re.compile(r"^(US|USA)\s*\|\s*(Local\b|.*Locals)", re.I)


def clean_city(value):
    return re.sub(r"\s+", " ", (value or "").strip().replace("_", " ")).title()


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def network_for(text):
    match = NETWORK_RE.search(text or "")
    if not match:
        return "Other"
    value = match.group(1).upper()
    if value == "MY":
        return "My"
    if value in {"TELEMUNDO", "UNIVISION", "UNIMAS", "GALAVISION"}:
        return value.title()
    return value


def parse_station_name(name, variants=()):
    """Best-effort parse of one local-stream name.

    Returns state, city/market, call sign, network, the parser that matched, and a stable
    station key. Unknown fields are empty strings. Multi-state markets keep labels like
    ``TN-VA`` because provider names use that market boundary.
    """
    texts = []
    for value in (name, *(variants or ())):
        if value and value not in texts:
            texts.append(value)

    state = city = call = parser = ""
    for text in texts:
        match = PIPE_MARKET_PIPE.match(text.strip()) or PIPE_MARKET_INLINE.match(text.strip())
        if match:
            state, city, call = match.group(1).upper(), clean_city(match.group(2)), match.group(3).upper()
            parser = "state-market"
            break

    if not call:
        for text in texts:
            match = BRACKET_CITY_STATE.search(text.strip())
            if match:
                state = match.group(2).upper()
                city = clean_city(match.group(1))
                call = (match.group(3) or "").upper()
                parser = "bracket-market"
                break

    if not call:
        for text in texts:
            match = PAREN_CALL_CITY.search(text.strip())
            if match:
                call = match.group(1).upper()
                city = clean_city(match.group(2))
                parser = "call-city"
                break

    network = network_for(" ".join(texts))
    channel = next((match.group(1) for text in texts if (match := NETWORK_CHANNEL_RE.search(text))), "")
    return {
        "state": state,
        "city": city,
        "call": call,
        "network": network,
        "channel": channel,
        "parser": parser,
        "station_key": station_key(state, city, call, network, name),
    }


def station_key(state, city, call, network, fallback_name):
    """Stable identity for the same local station across provider naming variants."""
    if call:
        return "call:" + norm(call)
    parts = [network, state, city]
    key = norm("|".join(p for p in parts if p))
    return "market:" + key if key else "name:" + norm(fallback_name)


def scan_nationwide_locals(sources):
    """Read-only scan of active, non-stale streams in the configured local source groups."""
    from apps.channels.models import ChannelGroup, Stream

    patterns = [re.compile(p, re.I) for p in sources]
    source_groups = [g for g in ChannelGroup.objects.all()
                     if US_LOCAL_GROUP.search(g.name) and any(p.search(g.name) for p in patterns)]
    streams = (Stream.objects.filter(channel_group__in=source_groups, m3u_account__is_active=True, is_stale=False)
               .select_related("channel_group", "m3u_account").order_by("id"))

    entries = [(stream, parse_station_name(stream.name)) for stream in streams.iterator()]

    city_states = defaultdict(Counter)
    for _, parsed in entries:
        if parsed["state"] and parsed["city"]:
            parts = [parsed["city"]] + [p.strip() for p in parsed["city"].split("/") if p.strip()]
            for part in parts:
                city_states[norm(part)][parsed["state"]] += 1

    def inferred_state(city):
        states = city_states.get(norm(city))
        if not states and "/" in (city or ""):
            states = city_states.get(norm(city.split("/")[0]))
        return next(iter(states)) if states and len(states) == 1 else ""

    stations = {}
    unparsed = []
    partial = 0
    for stream, parsed in entries:
        if not parsed["state"] and parsed["city"]:
            parsed["state"] = inferred_state(parsed["city"])
            parsed["station_key"] = station_key(parsed["state"], parsed["city"], parsed["call"], parsed["network"], stream.name)
        if not parsed["parser"]:
            unparsed.append(stream.name)
        elif not parsed["state"] or not parsed["city"]:
            partial += 1

        key = parsed["station_key"]
        row = stations.setdefault(key, {
            **parsed,
            "name": stream.name,
            "streams": 0,
            "providers": set(),
            "groups": set(),
            "variants": set(),
            "tvg_ids": set(),
            "stream_ids": [],
        })
        for field in ("state", "city", "call", "channel"):
            if not row[field] and parsed[field]:
                row[field] = parsed[field]
        if row["network"] == "Other" and parsed["network"] != "Other":
            row["network"] = parsed["network"]
        if not row["parser"] and parsed["parser"]:
            row["parser"] = parsed["parser"]

        row["streams"] += 1
        row["stream_ids"].append(stream.id)
        row["variants"].add(stream.name)
        if stream.tvg_id:
            row["tvg_ids"].add(stream.tvg_id)
        if stream.m3u_account:
            row["providers"].add(stream.m3u_account.name)
        if stream.channel_group:
            row["groups"].add(stream.channel_group.name)

    rows = []
    for row in stations.values():
        rows.append({
            **row,
            "providers": len(row["providers"]),
            "provider_names": sorted(row["providers"]),
            "groups": sorted(row["groups"]),
            "variants": sorted(row["variants"])[:8],
            "tvg_ids": sorted(row["tvg_ids"]),
        })
    rows.sort(key=lambda r: (r["state"] or "ZZ", r["city"] or "ZZ", r["network"], r["name"]))

    state_counts = Counter(r["state"] or "Unknown" for r in rows)
    network_counts = Counter(r["network"] for r in rows)
    city_counts = Counter((r["city"], r["state"]) for r in rows if r["city"])
    return {
        "summary": {
            "stations": len(rows),
            "streams": sum(r["streams"] for r in rows),
            "states": len({r["state"] for r in rows if r["state"]}),
            "cities": len({(r["city"], r["state"]) for r in rows if r["city"]}),
            "unparsed_streams": len(unparsed),
            "partial_streams": partial,
            "by_state": [{"name": k, "stations": v} for k, v in state_counts.most_common()],
            "by_network": [{"name": k, "stations": v} for k, v in network_counts.most_common()],
            "top_cities": [{"name": f"{city}, {state or 'Unknown'}", "stations": count}
                           for (city, state), count in city_counts.most_common(30)],
        },
        "source_groups": sorted(g.name for g in source_groups),
        "stations": rows,
        "unparsed_examples": sorted(set(unparsed))[:100],
    }


def display_name(row):
    if row.get("city") and row.get("network") and row.get("call"):
        number = f" {row['channel']}" if row.get("channel") else ""
        return f"{row['city']}: {row['network']}{number} {row['call']}"
    variants = sorted(row.get("variants") or [row.get("name", "")], key=lambda n: (len(n), n.lower()))
    return variants[0] if variants else row.get("name", "Unknown")


def grouped_stations(rows, mode):
    if mode == "nationwide":
        return [("Nationwide", rows)]
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("state") or "Unparsed"].append(row)
    return sorted(groups.items(), key=lambda item: (item[0] == "Unparsed", item[0]))


def build_local_guide(scan, mode="state", apply=False, group_prefix="Lab — ", number_start=90000, log=None):
    """Preview or build flat local guide groups from scanned station metadata.

    Only groups beginning with group_prefix are touched. Streaming metadata is read, but no
    stream URLs are opened and no provider connections are made.
    """
    rows = scan["stations"]
    groups = grouped_stations(rows, mode)
    summary = []
    for index, (label, members) in enumerate(groups):
        members = sorted(members, key=lambda r: (r.get("city") or "ZZ", r.get("network") or "", display_name(r).lower()))
        summary.append({
            "group": f"{group_prefix}Locals — {label}" if label != "Nationwide" else f"{group_prefix}Locals — Nationwide",
            "channels": len(members),
            "streams": sum(r["streams"] for r in members),
            "start": number_start + index * 1000,
        })
    result = {"mode": mode, "groups": summary, "channels": sum(g["channels"] for g in summary),
              "streams": sum(g["streams"] for g in summary), "applied": False}
    if not apply:
        return result

    from django.db import transaction
    from apps.channels.models import Channel, ChannelGroup, ChannelStream, Logo, Stream
    from apps.epg.models import EPGData, EPGSourceIndex
    from django.db.models import Count
    from apps.m3u.models import M3UAccount

    stream_ids = sorted({sid for row in rows for sid in row.get("stream_ids", [])})
    stream_objects = {s.id: s for s in Stream.objects.filter(id__in=stream_ids).select_related("m3u_account")}
    accounts = sorted(M3UAccount.objects.filter(is_active=True),
                      key=lambda a: (-(a.max_streams or 0), -(getattr(a, "priority", 0) or 0), a.id))
    rank = {a.id: i for i, a in enumerate(accounts)}
    raw_tvg_ids = {t for row in rows for t in row.get("tvg_ids", []) if t}
    lookup_tvg_ids = sorted(raw_tvg_ids | {t.upper() for t in raw_tvg_ids} | {t.lower() for t in raw_tvg_ids})
    epg_by_id = {}
    candidate_epgs = list(EPGData.objects.filter(tvg_id__in=lookup_tvg_ids)
                          .annotate(program_count=Count("programs")))
    source_ids = {epg.epg_source_id for epg in candidate_epgs if epg.epg_source_id}
    indexed_tvg_ids = {}
    for index in EPGSourceIndex.objects.filter(source_id__in=source_ids):
        indexed_tvg_ids[index.source_id] = {
            tvg.lower() for tvg in (index.data or {}).get("channels", {})
        }

    def epg_rank(epg):
        has_source_programs = epg.tvg_id.lower() in indexed_tvg_ids.get(epg.epg_source_id, set())
        return (has_source_programs, epg.program_count, -(epg.epg_source_id or 999), epg.id)

    candidates = defaultdict(list)
    for epg in candidate_epgs:
        candidates[epg.tvg_id.lower()].append(epg)
    epg_by_id = {tvg: max(rows, key=epg_rank) for tvg, rows in candidates.items()}

    def logo_for(url):
        if not url:
            return None
        try:
            return Logo.objects.get_or_create(url=url, defaults={"name": url.rsplit("/", 1)[-1][:200]})[0]
        except Exception:
            return None

    for index, (label, members) in enumerate(groups):
        members = sorted(members, key=lambda r: (r.get("city") or "ZZ", r.get("network") or "", display_name(r).lower()))
        group_name = f"{group_prefix}Locals — {label}" if label != "Nationwide" else f"{group_prefix}Locals — Nationwide"
        with transaction.atomic():
            group, _ = ChannelGroup.objects.get_or_create(name=group_name)
            existing = {cs.stream_id: cs.channel for cs in ChannelStream.objects
                        .filter(channel__channel_group=group, order=0).select_related("channel")}
            number = float(number_start + index * 1000)
            create, update, channel_rows = [], [], []
            for row in members:
                streams = [stream_objects[sid] for sid in row.get("stream_ids", []) if sid in stream_objects]
                streams.sort(key=lambda s: (rank.get(s.m3u_account_id, 99), s.id))
                if not streams:
                    continue
                channel = existing.pop(streams[0].id, None) or Channel(channel_group=group)
                channel.name = display_name(row)
                epg = next((epg_by_id[t.lower()] for t in row.get("tvg_ids", []) if t and t.lower() in epg_by_id), None)
                if epg:
                    channel.epg_data = epg
                    channel.tvg_id = epg.tvg_id
                else:
                    channel.tvg_id = next((t for t in row.get("tvg_ids", []) if t), None)
                channel.channel_number = number
                logo = next((logo_for(s.logo_url) for s in streams if s.logo_url), None)
                if logo:
                    channel.logo = logo
                (update if channel.pk else create).append(channel)
                channel_rows.append((channel, streams))
                number += 1
            Channel.objects.bulk_create(create, batch_size=1000)
            Channel.objects.bulk_update(update, ["name", "tvg_id", "channel_number", "epg_data", "logo"], batch_size=1000)
            ChannelStream.objects.filter(channel__channel_group=group).delete()
            ChannelStream.objects.bulk_create(
                [ChannelStream(channel=channel, stream=stream, order=order)
                 for channel, streams in channel_rows for order, stream in enumerate(streams)],
                batch_size=5000)
            keep = {channel.id for channel, _ in channel_rows}
            stale = Channel.objects.filter(channel_group=group).exclude(id__in=keep)
            removed = stale.count()
            stale.delete()
            for item in summary:
                if item["group"] == group_name:
                    item["removed"] = removed
    result["applied"] = True
    return result


ENGLISH_NETWORKS = {"ABC", "CBS", "NBC", "FOX", "PBS", "CW", "My", "IND"}
NATIONAL_PREFIX = "Locals — "


def national(apply=False, start=100000, log=None):
    """One "Locals — <state>" group per state with every English-language local station the providers carry.

    Each station is one channel with every provider's copy attached for failover. Groups for states that
    no longer have stations are emptied.
    """
    scan = scan_nationwide_locals([US_LOCAL_GROUP.pattern])
    scan["stations"] = [r for r in scan["stations"] if r["network"] in ENGLISH_NETWORKS and r["state"]]
    result = build_local_guide(scan, "state", apply, group_prefix="", number_start=start, log=log)
    if apply:
        from apps.channels.models import Channel
        keep = {g["group"] for g in result["groups"]}
        stale = Channel.objects.filter(channel_group__name__startswith=NATIONAL_PREFIX).exclude(channel_group__name__in=keep)
        result["removed"] = stale.count()
        stale.delete()
    return result
