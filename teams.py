# teams.py
import json
from pathlib import Path

TEAMS_FILE = "teams.json"

def load_teams():
    if Path(TEAMS_FILE).exists():
        return json.loads(Path(TEAMS_FILE).read_text())
    return {}
