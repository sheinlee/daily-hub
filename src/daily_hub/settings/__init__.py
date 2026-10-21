"""Per-tool daily-settings editors (see base.py for the safety rules)."""
from __future__ import annotations

import importlib
import inspect
import logging

from .base import SettingsProvider

# Modules holding one SettingsProvider subclass each; a module that is missing or fails to import
# leaves its game "未接入" instead of breaking the hub.
_MODULES = ["maa", "maaend", "maabbb", "alas", "okww", "bettergi", "zzz", "march7th"]

PROVIDERS: dict[str, SettingsProvider] = {}
for _name in _MODULES:
    try:
        _mod = importlib.import_module(f".{_name}", __name__)
    except Exception as _e:          # noqa: BLE001 — any import error only disables that page
        logging.getLogger(__name__).warning("settings provider %s unavailable: %r", _name, _e)
        continue
    for _obj in vars(_mod).values():
        if (inspect.isclass(_obj) and issubclass(_obj, SettingsProvider) and _obj is not SettingsProvider
                and _obj.__module__ == _mod.__name__ and _obj.key):
            PROVIDERS[_obj.key] = _obj()


def get(adapter_key: str) -> SettingsProvider | None:
    return PROVIDERS.get(adapter_key)
