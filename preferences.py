"""Only non-secret settings are stored on disk."""
from __future__ import annotations
import json
import os
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG = CONFIG_HOME / "ai-usage-pet" / "settings.json"
AUTOSTART = CONFIG_HOME / "autostart" / "ai-usage-pet.desktop"

DEFAULTS = {"refresh_minutes": 5, "warn_codex_below": 20, "warn_deepseek_cny_below": 10, "selected_provider": "codex"}


def load() -> dict:
    try:
        obj = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {**DEFAULTS, **{k: v for k, v in obj.items() if k in DEFAULTS}}
    except (ValueError, OSError):
        pass
    return DEFAULTS.copy()


def save(prefs: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({k: prefs.get(k, v) for k, v in DEFAULTS.items()}, indent=2), encoding="utf-8")


def is_autostart() -> bool:
    return AUTOSTART.exists()


def set_autostart(enabled: bool, launcher: Path):
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        # desktop-entry specifications require quoting paths with spaces
        cmd = str(launcher.resolve()).replace('\\', '\\\\').replace('"', '\\"')
        AUTOSTART.write_text(
            '[Desktop Entry]\nType=Application\nName=AI Usage Pet\n'
            f'Exec="{cmd}"\nTerminal=false\n'
            'Icon=utilities-system-monitor\nX-GNOME-Autostart-enabled=true\n',
            encoding="utf-8",
        )
    else:
        AUTOSTART.unlink(missing_ok=True)
