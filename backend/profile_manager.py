import os
import json
import uuid
from datetime import datetime
import shutil
from typing import List, Dict, Optional
from cryptography.fernet import Fernet
from backend.config import get_data_dir

PROFILES_DIR = get_data_dir("profiles_data")
KEY_FILE = os.path.join(PROFILES_DIR, ".master.key")

class ProfileManager:
    def __init__(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        self.metadata_file = os.path.join(PROFILES_DIR, "profiles_meta.json")
        self._init_crypto()
        self._load_metadata()

    def _init_crypto(self):
        if not os.path.exists(KEY_FILE):
            key = Fernet.generate_key()
            with open(KEY_FILE, "wb") as f:
                f.write(key)
        else:
            with open(KEY_FILE, "rb") as f:
                key = f.read()
        self.cipher = Fernet(key)

    def _load_metadata(self):
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, "r") as f:
                    encrypted_profiles = json.load(f)
                    
                    self.profiles = {}
                    for pid, pdata in encrypted_profiles.items():
                        # Decrypt sensitive fields if present and encrypted
                        if "proxy" in pdata and isinstance(pdata["proxy"], str) and pdata["proxy"].startswith("enc:"):
                            try:
                                pdata["proxy"] = json.loads(self.cipher.decrypt(pdata["proxy"][4:].encode()).decode())
                            except:
                                pdata["proxy"] = None
                                
                        if "advanced" in pdata and isinstance(pdata["advanced"], str) and pdata["advanced"].startswith("enc:"):
                            try:
                                pdata["advanced"] = json.loads(self.cipher.decrypt(pdata["advanced"][4:].encode()).decode())
                            except:
                                pdata["advanced"] = {}
                                
                        self.profiles[pid] = pdata
                        
            except json.JSONDecodeError:
                self.profiles = {}
        else:
            self.profiles = {}

    def _save_metadata(self):
        # We need to make a copy to encrypt before saving, without mutating the runtime self.profiles
        profiles_to_save = {}
        for pid, pdata in self.profiles.items():
            save_data = pdata.copy()
            if save_data.get("proxy"):
                save_data["proxy"] = "enc:" + self.cipher.encrypt(json.dumps(save_data["proxy"]).encode()).decode()
            if save_data.get("advanced"):
                save_data["advanced"] = "enc:" + self.cipher.encrypt(json.dumps(save_data["advanced"]).encode()).decode()
            profiles_to_save[pid] = save_data
            
        with open(self.metadata_file, "w") as f:
            json.dump(profiles_to_save, f, indent=4)

    def create_profile(self, name: str, proxy: dict = None, timezone: str = None, locale: str = None, advanced: dict = None):
        profile_id = str(uuid.uuid4())
        profile_path = os.path.join(PROFILES_DIR, profile_id)
        os.makedirs(profile_path, exist_ok=True)

        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        if advanced and advanced.get("os") == "Mac":
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        elif advanced and advanced.get("os") == "Linux":
            user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"

        profile_data = {
            "id": profile_id,
            "name": name,
            "created_at": datetime.now().isoformat(),
            "path": os.path.abspath(profile_path),
            "proxy": proxy,
            "timezone": timezone or "UTC",
            "locale": locale or "en-US",
            "user_agent": user_agent,
            "advanced": advanced or {},
            "tags": [],
            "notes": ""
        }
        
        self.profiles[profile_id] = profile_data
        self._save_metadata()
        return profile_data

    def get_profile(self, profile_id: str):
        return self.profiles.get(profile_id)

    def list_profiles(self):
        return list(self.profiles.values())

    def delete_profile(self, profile_id: str):
        profile = self.profiles.get(profile_id)
        if profile:
            path = profile.get("path")
            if path and os.path.exists(path):
                try:
                    shutil.rmtree(path)
                except Exception as e:
                    print(f"Error removing profile directory: {e}")
            del self.profiles[profile_id]
            self._save_metadata()
            return True
        return False

    def update_profile(self, profile_id: str, updates: dict):
        if profile_id in self.profiles:
            for k, v in updates.items():
                self.profiles[profile_id][k] = v
            self._save_metadata()
            return True
        return False

    def rename_profile(self, profile_id: str, new_name: str):
        profile = self.profiles.get(profile_id)
        if profile:
            profile["name"] = new_name
            self._save_metadata()
            return True
        return False

# Global instance
profile_manager = ProfileManager()
