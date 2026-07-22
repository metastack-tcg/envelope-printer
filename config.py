"""User settings, stored beside the user's data rather than baked into the exe.

Lives in %APPDATA% so it survives a reinstall and works when the exe sits
somewhere unwritable like Program Files.

Branding is kept in named **presets** so one install can print for several
businesses. The printer is not part of a preset — it belongs to the machine.
"""

import json
import os
from pathlib import Path

APP = "Envelope Printer"

# everything that makes an envelope look like a particular business
PRESET_DEFAULTS = {
    "brand_name": "",
    "return_address": ["", "", ""],
    "logo_path": "",
    "logo_width_in": 2.35,
    "logo_layout": "below",      # "below" (generic) | "hang" (under a wordmark)
    "margin_x_in": 0.4,
    "margin_top_in": 0.4,
    "addr_dx_in": 0.0,           # nudges the return address off its computed spot
    "addr_dy_in": 0.0,
    "font": "Fraunces",
    "accent_style": "tick",      # none|tick|bracket|rule|band|stripe
    "accent_color": "#C2410C",
    "return_style": "",          # any of b/i/u
    "recipient_style": "b",      # styles the name line only
    # only used by logo_layout "hang": where the wordmark starts inside the logo
    # box and its baseline height, as fractions of the logo's size
    "wordmark_x": 0.268,
    "baseline_y": 0.387,
}

DEFAULTS = {
    "presets": {"Default": dict(PRESET_DEFAULTS)},
    "active": "Default",
    "printer": "",
}


def path():
    base = os.environ.get("APPDATA") or Path.home()
    d = Path(base) / APP
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


def exists():
    return path().exists()


def _upgrade(p):
    """tick_show/tick_color predate the accent-style choice."""
    if "tick_show" in p or "tick_color" in p:
        p.setdefault("accent_style", "tick" if p.pop("tick_show", True) else "none")
        p.setdefault("accent_color", p.pop("tick_color", "#C2410C"))
    p.pop("tick_show", None)
    p.pop("tick_color", None)
    return p


def _migrate(data):
    """Pre-presets configs were flat. Fold one into a preset named after the brand."""
    name = (data.get("brand_name") or "").strip() or "Default"
    return {
        "presets": {name: {k: data[k] for k in PRESET_DEFAULTS if k in data}},
        "active": name,
        "printer": data.get("printer", ""),
    }


def load():
    cfg = {"presets": {"Default": dict(PRESET_DEFAULTS)}, "active": "Default", "printer": ""}
    p = path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if "presets" not in data:
                data = _migrate(data)
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
            pass  # a corrupt config shouldn't brick the app — defaults still print
    if not cfg["presets"]:
        cfg["presets"] = {"Default": dict(PRESET_DEFAULTS)}
    cfg["presets"] = {k: _upgrade(dict(v)) for k, v in cfg["presets"].items()}
    if cfg["active"] not in cfg["presets"]:
        cfg["active"] = next(iter(cfg["presets"]))
    return cfg


def active(cfg):
    """The flat settings the renderer wants, with any missing key defaulted."""
    p = dict(PRESET_DEFAULTS)
    p.update(cfg["presets"].get(cfg["active"], {}))
    return p


def names(cfg):
    return sorted(cfg["presets"], key=str.lower)


def save(cfg):
    path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
