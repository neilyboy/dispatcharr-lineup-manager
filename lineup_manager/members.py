"""Friends and family accounts: one Dispatcharr streamer login per member, with an expiry date.

Passwords never live in plugin settings. A new member gets a generated password, stored only on their
Dispatcharr user (login + Xtream Codes password). "Setup cards" reads it back for you to hand out.
"""
import datetime, re, secrets

SAFE = re.compile(r"^[A-Za-z0-9._@-]+$")
MARK = "lineup_manager"


def parse_day(s):
    try:
        return datetime.date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return d.replace(year=y, month=m, day=min(d.day, days))


def sync(rows, profile_name="", today=None):
    """Creates/updates member users, disables expired or removed ones. Never touches admins or other users."""
    from django.contrib.auth import get_user_model
    from apps.channels.models import ChannelProfile
    User = get_user_model()
    today = today or datetime.date.today()
    profile = ChannelProfile.objects.filter(name=profile_name).first() if profile_name else None
    out, listed = [], set()
    for r in rows:
        u_name = (r.get("username") or "").strip()
        if not u_name:
            continue
        if not SAFE.fullmatch(u_name):
            out.append({"username": u_name, "result": "skipped: use letters, digits, . _ @ - only"})
            continue
        listed.add(u_name)
        exp = parse_day(r.get("expires"))
        active = bool(r.get("enabled", True)) and (exp is None or exp >= today)
        user = User.objects.filter(username=u_name).first()
        created = user is None
        if user and not (user.custom_properties or {}).get(MARK):
            out.append({"username": u_name, "result": "skipped: an existing user not created by this plugin"})
            continue
        if created:
            user = User(username=u_name, user_level=0)
            pw = secrets.token_urlsafe(9)
            user.set_password(pw)
            user.custom_properties = {MARK: True, "xc_password": pw}
        user.first_name = (r.get("name") or "")[:150]
        user.stream_limit = int(r.get("stream_limit") or 0)
        user.is_active = active
        user.save()
        if profile:
            user.channel_profiles.set([profile])
        state = "active" if active else ("expired" if exp and exp < today else "disabled")
        out.append({"username": u_name, "result": ("created, " if created else "") + state,
                    "expires": exp.isoformat() if exp else "never"})
    for user in User.objects.filter(custom_properties__has_key=MARK, is_active=True).exclude(username__in=listed):
        user.is_active = False
        user.save(update_fields=["is_active"])
        out.append({"username": user.username, "result": "disabled: no longer in the members table"})
    return out


def renew(rows, who, months, today=None):
    """Pushes each named member's expiry out by `months`, from today if already expired."""
    today = today or datetime.date.today()
    names = {w.strip().lower() for w in re.split(r"[,\s]+", who or "") if w.strip()}
    done = []
    for r in rows:
        if (r.get("username") or "").lower() in names or "all" in names:
            base = max(parse_day(r.get("expires")) or today, today)
            r["expires"] = add_months(base, months).isoformat()
            r["enabled"] = True
            done.append(f"{r['username']} → {r['expires']}")
    return rows, done


def cards(rows, public_url):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    url = (public_url or "").rstrip("/")
    out = []
    for r in rows:
        user = User.objects.filter(username=(r.get("username") or "").strip(), custom_properties__has_key=MARK).first()
        if not user:
            continue
        out.append({"name": r.get("name") or user.username, "active": user.is_active,
                    "expires": r.get("expires") or "never",
                    "xtream_server": url, "username": user.username,
                    "password": (user.custom_properties or {}).get("xc_password", ""),
                    "m3u": f"{url}/output/m3u?username={user.username}&password="
                           f"{(user.custom_properties or {}).get('xc_password', '')}" if url else "",
                    "epg": f"{url}/output/epg" if url else ""})
    return out
