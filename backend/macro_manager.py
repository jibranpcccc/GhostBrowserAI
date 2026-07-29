import os
import json
import uuid
from types import SimpleNamespace
from backend.config import get_data_dir

MACROS_FILE = os.path.join(get_data_dir("profiles_data"), "macros.json")

_macros = []

def _load():
    global _macros
    if os.path.exists(MACROS_FILE):
        try:
            with open(MACROS_FILE, "r") as f:
                _macros = json.load(f)
        except Exception:
            _macros = []
    else:
        _macros = []

def _save():
    with open(MACROS_FILE, "w") as f:
        json.dump(_macros, f, indent=4)

def list_macros():
    return _macros

def get_macro(macro_id: str):
    for m in _macros:
        if m["id"] == macro_id:
            return m
    return None

def create_macro(name: str, description: str, steps: list):
    macro = {"id": str(uuid.uuid4()), "name": name, "description": description, "steps": steps}
    _macros.append(macro)
    _save()
    return macro

def update_macro(macro_id: str, name: str, description: str, steps: list):
    macro = get_macro(macro_id)
    if macro:
        macro["name"] = name
        macro["description"] = description
        macro["steps"] = steps
        _save()
        return macro
    return None

def delete_macro(macro_id: str):
    global _macros
    _macros = [m for m in _macros if m["id"] != macro_id]
    _save()
    return True

macro_manager = SimpleNamespace(
    list_macros=list_macros, get_macro=get_macro, create_macro=create_macro,
    update_macro=update_macro, delete_macro=delete_macro,
)

_load()
