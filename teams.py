# teams.py
import json
from pathlib import Path

TEAMS_FILE = "teams.json"


def normalize_members(raw):
    """Accept legacy "Last, First" strings or {name, email} dicts; return dicts.

    Emails are lowercased so all matching can be case-insensitive.
    """
    members = []
    for m in raw:
        if isinstance(m, dict):
            members.append({
                "name": m.get("name", ""),
                "email": (m.get("email") or "").strip().lower(),
            })
        else:
            members.append({"name": m, "email": ""})
    return members


def load_teams():
    if Path(TEAMS_FILE).exists():
        teams = json.loads(Path(TEAMS_FILE).read_text())
        for cfg in teams.values():
            cfg["members"] = normalize_members(cfg.get("members", []))
        return teams
    return {}
