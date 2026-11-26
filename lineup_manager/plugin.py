import datetime, json, os
from . import lineup, members, tasks

S_LINEUP, S_HIDDEN, S_MEMBERS, S_SCHEDULE = "sec_lineup", "sec_hidden", "sec_members", "sec_schedule"


class Plugin:
    name = "Lineup Manager"
    version = "0.1.0"
    description = ("One clean guide from all your providers (same channels merged with failover), a report of "
                   "everything left out with one-line add-back, and friends & family logins with expiry dates.")
    author = "dispatcharr-lineup-manager contributors"
    help_url = "https://github.com/neilyboy/dispatcharr-lineup-manager"

    fields = [
        {"id": S_LINEUP, "label": "Lineup", "type": "section"},
        {"id": "countries", "label": "Countries to keep", "type": "string", "default": "US|USA|UK|GB", "section": S_LINEUP,
         "help_text": "Provider groups and channels prefixed with any other country code (DE:, AR |, ...) are left out. "
                      "Blank keeps every country."},
        {"id": "lineup_config", "label": "Lineup groups (JSON, optional)", "type": "text", "rows": 8, "default": "",
         "section": S_LINEUP,
         "help_text": "Blank uses the bundled US + UK + sports lineup. Paste your own {\"groups\": [...]} to change it; "
                      "see the README for the format."},
        {"id": "sync_limits", "label": "Read stream limits from Xtream providers", "type": "boolean", "default": False,
         "section": S_LINEUP, "help_text": "Asks each Xtream provider for its connection limit and updates the account."},

        {"id": S_HIDDEN, "label": "Left out / add back", "type": "section", "collapsed": True},
        {"id": "add_back", "label": "Add these provider groups back", "type": "text", "rows": 4, "default": "",
         "section": S_HIDDEN,
         "help_text": "One provider group name per line, copied from the 'What's left out' report. Each becomes its "
                      "own guide group, numbered from 80000."},

        {"id": S_MEMBERS, "label": "Members", "type": "section"},
        {"id": "members", "label": "Members", "type": "table", "default": [], "section": S_MEMBERS,
         "columns": [
             {"id": "name", "label": "Name", "type": "string"},
             {"id": "username", "label": "Username", "type": "string"},
             {"id": "stream_limit", "label": "Streams", "type": "number"},
             {"id": "expires", "label": "Expires (YYYY-MM-DD)", "type": "string"},
             {"id": "enabled", "label": "On", "type": "boolean"},
             {"id": "notes", "label": "Notes", "type": "string"},
         ]},
        {"id": "member_profile", "label": "Channel profile for members", "type": "string", "default": "",
         "section": S_MEMBERS, "help_text": "Optional. Blank gives members every channel."},
        {"id": "public_url", "label": "Server address members use", "type": "string", "default": "",
         "placeholder": "https://tv.example.com", "section": S_MEMBERS,
         "help_text": "Shown on setup cards. Use your HTTPS domain for members outside your home."},
        {"id": "renew_who", "label": "Renew", "type": "string", "default": "", "section": S_MEMBERS,
         "placeholder": "alice, bob   (or: all)"},
        {"id": "renew_months", "label": "Renew by (months)", "type": "number", "default": 3, "section": S_MEMBERS},

        {"id": S_SCHEDULE, "label": "Schedule", "type": "section", "collapsed": True},
        {"id": "every_hours", "label": "Rebuild the lineup and check members every (hours)", "type": "number",
         "default": 2, "section": S_SCHEDULE},
    ]

    actions = [
        {"id": "preview", "label": "Preview lineup", "async": True,
         "description": "Shows what each guide group would get. Changes nothing."},
        {"id": "apply", "label": "Build lineup", "async": True, "button_color": "blue",
         "description": "Builds the guide groups from every active provider.",
         "confirm": {"required": True, "title": "Build the lineup?",
                     "message": "Channels in the lineup's own groups are replaced. Other groups are not touched."}},
        {"id": "hidden", "label": "What's left out", "async": True,
         "description": "Lists provider groups not in the guide. Copy names into 'Add back'."},
        {"id": "sync_members", "label": "Save members", "button_color": "green",
         "description": "Creates new members' logins and turns off expired or removed members."},
        {"id": "renew", "label": "Renew", "description": "Extends the members named in 'Renew'."},
        {"id": "cards", "label": "Setup cards", "description": "Login details to send each member."},
        {"id": "backup", "label": "Export settings",
         "description": "Saves lineup + members (no passwords) to the plugin's data folder."},
        {"id": "schedule", "label": "Turn on schedule",
         "description": "Rebuilds the lineup and checks expiries on the interval set under Schedule."},
    ]

    # ---- helpers -------------------------------------------------------------------------------
    @staticmethod
    def lineup_cfg(settings):
        raw = (settings.get("lineup_config") or "").strip()
        if raw:
            cfg = json.loads(raw)
        else:
            with open(os.path.join(os.path.dirname(__file__), "lineup.example.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        if "countries" in settings:
            cfg["countries"] = settings.get("countries") or ""
        return lineup.with_add_backs(cfg, (settings.get("add_back") or "").splitlines())

    def refresh(self, settings, log, progress=lambda p, m: None):
        if settings.get("sync_limits"):
            sync_limits(log)
        summary = lineup.Curator(self.lineup_cfg(settings), log).run(apply=True, progress=progress)
        result = members.sync(settings.get("members") or [], settings.get("member_profile") or "")
        total = sum(r["channels"] for r in summary)
        return {"status": "ok", "message": f"Lineup rebuilt: {total} channels in {len(summary)} groups. "
                                           f"{len(result)} members checked.", "groups": summary, "members": result}

    # ---- actions -------------------------------------------------------------------------------
    def run(self, action, params, context):
        settings, log = context.get("settings", {}), context["logger"]
        progress = context.get("report_progress") or (lambda p, m: None)
        try:
            cfg = self.lineup_cfg(settings)
        except (ValueError, OSError) as e:
            return {"status": "error", "message": f"Lineup groups JSON is invalid: {e}"}

        if action in ("preview", "apply"):
            if action == "apply" and settings.get("sync_limits"):
                sync_limits(log)
            summary = lineup.Curator(cfg, log).run(apply=action == "apply", progress=progress)
            total = sum(r["channels"] for r in summary)
            verb = "Built" if action == "apply" else "Would build"
            return {"status": "ok", "message": f"{verb} {total} channels in {len(summary)} groups.", "groups": summary,
                    "_dispatcharr": {"refresh": "ui"}}

        if action == "hidden":
            rows = lineup.Curator(cfg, log).hidden()
            path = os.path.join(context["data_dir"], "left-out.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(f"{r['streams']:>7}  {r['group']}  ({r['why']})\n" for r in rows)
            top = ", ".join(r["group"] for r in sorted(rows, key=lambda r: -r["streams"])[:8])
            return {"status": "ok", "message": f"{len(rows)} provider groups ({sum(r['streams'] for r in rows)} streams) "
                                               f"are left out. Biggest: {top}. Full list: {path}", "left_out": rows}

        rows = settings.get("members") or []
        if action == "sync_members":
            result = members.sync(rows, settings.get("member_profile") or "")
            return {"status": "ok", "message": "; ".join(f"{r['username']}: {r['result']}" for r in result) or
                    "No members yet. Add rows to the Members table and save settings first.", "members": result}

        if action == "renew":
            rows, done = members.renew(rows, settings.get("renew_who"), int(settings.get("renew_months") or 3))
            if not done:
                return {"status": "error", "message": "Type usernames (or 'all') into Renew first."}
            save_settings({**settings, "members": rows, "renew_who": ""})
            members.sync(rows, settings.get("member_profile") or "")
            return {"status": "ok", "message": "Renewed: " + ", ".join(done), "_dispatcharr": {"refresh": "ui"}}

        if action == "cards":
            out = members.cards(rows, settings.get("public_url"))
            if not out:
                return {"status": "error", "message": "No members with logins yet. Run 'Save members' first."}
            return {"status": "ok", "message": " | ".join(
                f"{c['name']}: server {c['xtream_server'] or '(set Server address)'}, user {c['username']}, "
                f"password {c['password']}, expires {c['expires']}" for c in out), "cards": out}

        if action == "backup":
            stamp = datetime.date.today().isoformat()
            path = os.path.join(context["data_dir"], f"lineup-manager-{stamp}.json")
            data = {"plugin": self.name, "version": self.version, "exported": stamp,
                    "settings": {k: v for k, v in settings.items() if not k.startswith("sec_")}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return {"status": "ok", "message": f"Saved {path}. Passwords stay in Dispatcharr's own backups.",
                    "backup": data}

        if action == "schedule":
            hours = tasks.install_schedule(settings.get("every_hours"))
            return {"status": "ok", "message": f"Lineup rebuild and member check will run every {hours} h."}

        return {"status": "error", "message": f"Unknown action {action}"}


def save_settings(settings):
    row = tasks.config_row()
    if row:
        row.settings = settings
        row.save(update_fields=["settings"])


def sync_limits(log):
    import requests
    from apps.m3u.models import M3UAccount
    for a in M3UAccount.objects.filter(account_type="XC", is_active=True):
        try:
            info = requests.get(a.server_url.rstrip("/") + "/player_api.php",
                                params={"username": a.username, "password": a.password}, timeout=20).json()
            n = int((info.get("user_info") or {}).get("max_connections") or 0)
            if n and n != a.max_streams:
                log.info(f"stream limit {a.name}: {a.max_streams} -> {n}")
                a.max_streams = n
                a.save(update_fields=["max_streams"])
        except Exception as e:
            log.warning(f"stream limit {a.name}: could not read ({type(e).__name__})")
