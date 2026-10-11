"""
config.py
---------
Configurações persistentes do app, salvas em JSON em:
    %LOCALAPPDATA%\\NitroSwitch\\config.json

Guarda:
    - default_mode: "eco", "balanced" ou "boost" -> modo aplicado automaticamente ao iniciar
    - apply_on_startup: bool -> se True, aplica default_mode assim que o app abre
"""

import copy
import json
import os

CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NitroSwitch")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "default_mode": "eco",
    "apply_on_startup": True,
    # modo de ventoinha escolhido para cada modo do app (ver fan_control.FAN_MODES)
    "fan_mode": {"supereco": "auto", "eco": "auto", "balanced": "auto", "boost": "auto"},
    "fan_custom_cap": 70,
    # tecla que abre/fecha o painel, aprendida pelo botão na UI (ver hotkey.py)
    "hotkey": None,
    # visual do painel: "gamer" (Cockpit Neon) ou "casual" (ver ui.THEMES)
    "theme": "gamer",
}


def load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save(DEFAULTS)
        return copy.deepcopy(DEFAULTS)
    try:
        # utf-8-sig (e não utf-8): se o arquivo tiver sido gravado por outra
        # ferramenta que adiciona BOM (o `Out-File` do PowerShell faz isso por
        # padrão, por exemplo), `utf-8` puro estoura JSONDecodeError e a config
        # inteira do usuário era descartada em silêncio, voltando pros valores
        # padrão. `utf-8-sig` lê os dois formatos (com e sem BOM).
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        # Garante que chaves novas adicionadas em versões futuras existam.
        # deepcopy: DEFAULTS tem dict aninhado (fan_mode) - uma cópia rasa
        # faria alterações na config em memória vazarem para os DEFAULTS.
        merged = copy.deepcopy(DEFAULTS)
        merged.update(data)
        fan_mode = copy.deepcopy(DEFAULTS["fan_mode"])
        fan_mode.update(data.get("fan_mode") or {})
        merged["fan_mode"] = fan_mode
        return merged
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULTS)


def save(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def set_default_mode(mode: str) -> None:
    assert mode in ("supereco", "eco", "balanced", "boost")
    data = load()
    data["default_mode"] = mode
    save(data)


def set_apply_on_startup(enabled: bool) -> None:
    data = load()
    data["apply_on_startup"] = bool(enabled)
    save(data)


def set_fan_mode(app_mode: str, fan_mode: str) -> None:
    """Guarda qual modo de ventoinha usar quando `app_mode` estiver ativo."""
    assert app_mode in ("supereco", "eco", "balanced", "boost")
    data = load()
    data["fan_mode"][app_mode] = fan_mode
    save(data)


def set_fan_custom_cap(cap: int) -> None:
    """Teto do modo de ventoinha "personalizada". Nunca abaixo de 40% (trava
    de segurança, ver fan_control.MIN_CUSTOM_CAP)."""
    data = load()
    data["fan_custom_cap"] = int(max(40, min(100, cap)))
    save(data)


def set_hotkey(binding) -> None:
    data = load()
    data["hotkey"] = binding
    save(data)
