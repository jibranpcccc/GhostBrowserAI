import os
import json
import uuid
from datetime import datetime
from backend.config import get_data_dir

MACROS_FILE = os.path.join(get_data_dir("profiles_data"), "macros.json")

class MacroManager:
    def __init__(self):
        self._load()

    def _load(self):
        if os.path.exists(MACROS_FILE):
            try:
                with open(MACROS_FILE, "r") as f:
                    self.macros = json.load(f)
            except Exception:
                # ROBUSTNESS FIX: Backup corrupted file before resetting
                import shutil
                backup_path = MACROS_FILE + f".corrupted.{int(datetime.now().timestamp())}.json"
                try:
                    shutil.copy2(MACROS_FILE, backup_path)
                    print(f"[MacroManager] ⚠️ Corrupted macros file backed up to {backup_path}")
                except Exception:
                    pass
                self.macros = []
        else:
            self.macros = []
            
    def _save(self):
        with open(MACROS_FILE, "w") as f:
            json.dump(self.macros, f, indent=4)
            
    def list_macros(self):
        return self.macros
        
    def get_macro(self, macro_id: str):
        for m in self.macros:
            if m["id"] == macro_id:
                return m
        return None
        
    def create_macro(self, name: str, description: str, steps: list):
        macro = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "steps": steps
        }
        self.macros.append(macro)
        self._save()
        return macro
        
    def update_macro(self, macro_id: str, name: str, description: str, steps: list):
        macro = self.get_macro(macro_id)
        if macro:
            macro["name"] = name
            macro["description"] = description
            macro["steps"] = steps
            self._save()
            return macro
        return None
        
    def delete_macro(self, macro_id: str):
        self.macros = [m for m in self.macros if m["id"] != macro_id]
        self._save()
        return True

macro_manager = MacroManager()
