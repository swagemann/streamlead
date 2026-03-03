# teams.py
import json
from pathlib import Path

TEAMS_FILE = "teams.json"

def load_teams():
    if Path(TEAMS_FILE).exists():
        return json.loads(Path(TEAMS_FILE).read_text())
    return {}

def save_teams(teams):
    Path(TEAMS_FILE).write_text(json.dumps(teams, indent=2))

def add_member(team_name, member_name, teams):
    teams.setdefault(team_name, [])
    if member_name not in teams[team_name]:
        teams[team_name].append(member_name)
    save_teams(teams)

def remove_member(team_name, member_name, teams):
    if team_name in teams:
        teams[team_name] = [m for m in teams[team_name] if m != member_name]
    save_teams(teams)