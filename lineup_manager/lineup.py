"""Builds one merged lineup from every active provider.

Streams that are the same channel (same tvg-id or same cleaned-up name) across providers become ONE
channel with every provider's stream attached as failovers, ordered by each provider's stream limit.
Only channels in the groups the lineup owns are touched, so manually added groups are left alone.
"""
import functools, re
from collections import defaultdict

JUNK = re.compile(r"\b(hd|fhd|uhd|4k|sd|hevc|h265|lb|hb|backup|alt|alternative|\(.*?\)|\[.*?\])\b", re.I)
PREFIX = re.compile(r"^\s*(us|usa|united states|uk|ca|au)\s*[:|\-]?\s+", re.I)
# "DE: RTL" on streams, "AR | beIN" on groups. Streams only count "XX:" so US state locals like
# "IL | Springfield | CBS" aren't mistaken for Israel.
CODE = re.compile(r"^\s*([A-Z]{2,4})\s*:")
GROUP_CODE = re.compile(r"^\s*([A-Z]{2,4})\s*[:|]")
COUNTRY_CODES = set("""AD AE AF AL AM AO AR AT AU AZ BA BD BE BG BH BO BR BY CA CH CL CN CO CR CU CY CZ DE DK DO DZ
EC EE EG ES ET FI FR GB GE GH GR GT HK HN HR HT HU ID IE IL IN IQ IR IS IT JM JO JP KE KR KW KZ LB LK LT LU LV LY MA MK
MT MX MY NG NI NL NO NP NZ OM PA PE PH PK PL PR PS PT PY QA RO RS RU SA SE SG SI SK SN SV SY TH TN TR TW TZ UA UG UK US
UY UZ VE VN YE ZA USA GER FRA ESP ITA POR POL NED SWE NOR DEN FIN TUR ARA RUS BRA ARG MEX IND PAK EXYU AFR CAN AUS""".split())
EMPTY_EVENT = re.compile(r"no events?\b|no games?\b|off ?air|^[^:]*:\s*$|\(209\d", re.I)
ADD_BACK_START = 80000


def name_key(name):
    return re.sub(r"[^a-z0-9]+", "", JUNK.sub(" ", PREFIX.sub("", name or "")).lower())


def tvg_key(t):
    t = (t or "").strip().lower()
    return "" if not t or "dummy" in t or t in ("none", "null") else t


def natural(n):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n.lower())]


def with_add_backs(cfg, names):
    """Each restored provider group becomes its own lineup group, exactly as the provider named it."""
    groups = list(cfg.get("groups", []))
    have = {g["name"] for g in groups}
    for i, n in enumerate(x.strip() for x in names if x.strip()):
        if n not in have:
            groups.append({"name": n, "start": ADD_BACK_START + i * 1000, "any_country": True, "merge": False,
                           "sources": ["^" + re.escape(n) + "$"]})
    return {**cfg, "groups": groups}


class Curator:
    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.allowed = {c.strip().upper() for c in (cfg.get("countries") or "").split("|") if c.strip()}
        self.skip = re.compile(cfg["skip_groups"], re.I) if cfg.get("skip_groups") else None
        self.own = {g["name"] for g in cfg.get("groups", [])}
        self.claimed = set()
        self.epg = {}

    def foreign(self, name, group=False):
        m = (GROUP_CODE if group else CODE).match(name or "") if self.allowed else None
        return bool(m) and m.group(1) in COUNTRY_CODES and m.group(1) not in self.allowed

    def claim(self, spec, all_groups):
        pats = [re.compile(p, re.I) for p in spec["sources"]]
        skip = re.compile(spec["skip_groups"], re.I) if spec.get("skip_groups") else None
        groups = [g for g in all_groups
                  if g.id not in self.claimed and (g.name not in self.own or g.name == spec["name"])
                  and any(p.search(g.name) for p in pats)
                  and not (self.skip and self.skip.search(g.name)) and not (skip and skip.search(g.name))
                  and (spec.get("any_country") or not self.foreign(g.name, group=True))]
        self.claimed.update(g.id for g in groups)
        return groups

    def build_group(self, spec, rank, all_groups):
        from apps.channels.models import Stream
        groups = self.claim(spec, all_groups)
        streams = list(Stream.objects.filter(channel_group__in=groups, m3u_account__is_active=True, is_stale=False)
                       .select_related("m3u_account"))
        if not spec.get("any_country"):
            streams = [s for s in streams if not self.foreign(s.name)]
        if self.cfg.get("drop_empty_events") and spec.get("merge") is False:
            streams = [s for s in streams if not EMPTY_EVENT.search(s.name or "")]
        if spec.get("exclude"):
            ex = re.compile(spec["exclude"], re.I)
            streams = [s for s in streams if not ex.search(s.name or "")]
        if spec.get("include"):
            inc = re.compile(spec["include"], re.I)
            streams = [s for s in streams if inc.search(s.name or "")]
        parent = list(range(len(streams)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        seen = {}
        if spec.get("merge", True):
            for i, s in enumerate(streams):
                for k in filter(None, ("t:" + tvg_key(s.tvg_id) if tvg_key(s.tvg_id) else "", "n:" + name_key(s.name))):
                    if k in seen:
                        parent[find(i)] = find(seen[k])
                    else:
                        seen[k] = i
        clusters = defaultdict(list)
        for i, s in enumerate(streams):
            clusters[find(i)].append(s)
        out = []
        for members in clusters.values():
            if len({s.m3u_account_id for s in members}) < spec.get("min_providers", 1):
                continue
            members.sort(key=lambda s: (rank.get(s.m3u_account_id, 99), s.id))
            names = sorted({PREFIX.sub("", s.name).strip() for s in members}, key=lambda n: (len(n), n))
            tvg = next((s.tvg_id for s in members if tvg_key(s.tvg_id)), "")
            out.append({"name": names[0], "tvg_id": tvg, "logo": next((s.logo_url for s in members if s.logo_url), ""),
                        "streams": members})
        out.sort(key=lambda c: natural(c["name"]))
        return [g.name for g in groups], out

    def epg_for(self, tvg):
        from apps.epg.models import EPGData
        if not tvg_key(tvg):
            return None
        if not self.epg:
            for e in EPGData.objects.exclude(tvg_id=None).order_by("-id"):
                self.epg[e.tvg_id.lower()] = e
        return self.epg.get(tvg.lower())

    @functools.lru_cache(maxsize=None)
    def logo_for(self, url):
        from apps.channels.models import Logo
        if not url:
            return None
        try:
            return Logo.objects.get_or_create(url=url, defaults={"name": url.rsplit("/", 1)[-1][:200]})[0]
        except Exception:
            return None

    def hidden(self):
        """Provider groups the lineup leaves out, with stream counts and the reason."""
        from django.db.models import Count
        from apps.channels.models import ChannelGroup, Stream
        all_groups = list(ChannelGroup.objects.all())
        for spec in self.cfg.get("groups", []):
            self.claim(spec, all_groups)
        counts = {r["channel_group"]: r["n"] for r in Stream.objects.filter(m3u_account__is_active=True, is_stale=False)
                  .values("channel_group").annotate(n=Count("id"))}
        rows = []
        for g in all_groups:
            if g.id in self.claimed or g.name in self.own or not counts.get(g.id):
                continue
            why = ("skipped" if self.skip and self.skip.search(g.name)
                   else "other country" if self.foreign(g.name, group=True) else "no matching group")
            rows.append({"group": g.name, "streams": counts[g.id], "why": why})
        return sorted(rows, key=lambda r: natural(r["group"]))

    def run(self, apply=False, progress=lambda p, m: None):
        from django.db import transaction
        from apps.channels.models import Channel, ChannelGroup, ChannelStream
        from apps.m3u.models import M3UAccount
        accts = sorted(M3UAccount.objects.filter(is_active=True),
                       key=lambda a: (-(a.max_streams or 0), -(getattr(a, "priority", 0) or 0), a.id))
        rank = {a.id: i for i, a in enumerate(accts)}
        self.log.info("provider order: " + ", ".join(f"{a.name}({a.max_streams})" for a in accts))
        all_groups = list(ChannelGroup.objects.all())
        specs = self.cfg.get("groups", [])
        summary = []
        for n, spec in enumerate(specs):
            progress(int(100 * n / max(1, len(specs))), spec["name"])
            src, chans = self.build_group(spec, rank, all_groups)
            row = {"group": spec["name"], "channels": len(chans), "source_groups": len(src),
                   "with_guide": sum(1 for c in chans if self.epg_for(c["tvg_id"])), "removed": 0}
            summary.append(row)
            if not apply:
                continue
            with transaction.atomic():
                group, _ = ChannelGroup.objects.get_or_create(name=spec["name"])
                existing = {cs.stream_id: cs.channel for cs in ChannelStream.objects
                            .filter(channel__channel_group=group, order=0).select_related("channel")}
                num = float(spec.get("start", 1000))
                rows, new, old = [], [], []
                for c in chans:
                    ch = existing.pop(c["streams"][0].id, None) or Channel(channel_group=group)
                    ch.name, ch.tvg_id, ch.channel_number = c["name"], c["tvg_id"] or None, num
                    ch.epg_data = self.epg_for(c["tvg_id"]) or ch.epg_data
                    ch.logo = self.logo_for(c["logo"]) or ch.logo
                    (old if ch.pk else new).append(ch)
                    rows.append((ch, c))
                    num += 1
                Channel.objects.bulk_create(new, batch_size=1000)
                Channel.objects.bulk_update(old, ["name", "tvg_id", "channel_number", "epg_data", "logo"], batch_size=1000)
                ChannelStream.objects.filter(channel__channel_group=group).delete()
                ChannelStream.objects.bulk_create(
                    [ChannelStream(channel=ch, stream=st, order=i) for ch, c in rows for i, st in enumerate(c["streams"])],
                    batch_size=5000)
                gone = Channel.objects.filter(channel_group=group).exclude(id__in={ch.id for ch, _ in rows})
                row["removed"] = gone.count()
                gone.delete()
        progress(100, "done")
        return summary
