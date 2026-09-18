import os
import json
import uuid
import colorsys
import hashlib
import hmac
import secrets
import re
from datetime import datetime
import shutil
import sys
import ctypes
from ctypes import wintypes
from cryptography.fernet import Fernet, InvalidToken
from backend.config import get_data_dir
from backend.proxy_manager import guess_locale_timezone

_PIN_HASH_ITERATIONS = 100_000
_NEW_PIN_PATTERN = re.compile(r"[0-9]{4,6}\Z")


def is_valid_new_pin(pin: str) -> bool:
    """Return whether a newly submitted PIN meets the current policy.

    Verification intentionally does not use this check: hashes created under
    previous policies must remain usable until their owner replaces the PIN.
    """
    return isinstance(pin, str) and _NEW_PIN_PATTERN.fullmatch(pin) is not None


def _hash_pin(pin: str) -> str:
    """Hash a profile PIN using PBKDF2-HMAC-SHA256. Returns a storable token."""
    pin = pin.encode("utf-8")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin, salt, _PIN_HASH_ITERATIONS)
    return f"pbkdf2_sha256${_PIN_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_pin_hash(pin: str, pin_hash: str) -> bool:
    """Constant-time verify a PIN against its stored PBKDF2 hash token."""
    if not isinstance(pin_hash, str):
        return False
    parts = pin_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        stored_digest = parts[3]
    except (ValueError, TypeError):
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), salt, iterations
    ).hex()
    return hmac.compare_digest(computed, stored_digest)

PROFILES_DIR = get_data_dir("profiles_data")
KEY_FILE = os.path.join(PROFILES_DIR, ".master.key")
DPAPI_KEY_FILE = os.path.join(PROFILES_DIR, ".master.key.dpapi")

PROFILE_COLOR_PALETTE = (
    "#6366F1", "#EC4899", "#14B8A6", "#F59E0B", "#8B5CF6", "#22C55E",
    "#06B6D4", "#F97316", "#3B82F6", "#E11D48", "#84CC16", "#A855F7",
    "#0EA5E9", "#D946EF", "#10B981", "#EAB308", "#4F46E5", "#F43F5E",
)

def build_user_agent(os_name: str = "Windows") -> str:
    """Assemble a Chromium user-agent string for the requested OS."""
    from backend.config import get_installed_chromium_version
    current_chrome_ver = get_installed_chromium_version()
    if os_name == "Mac":
        return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{current_chrome_ver} Safari/537.36"
    if os_name == "Linux":
        return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{current_chrome_ver} Safari/537.36"
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{current_chrome_ver} Safari/537.36"


def _normalize_privacy_advanced(advanced: dict, timezone: str = None, locale: str = None) -> dict:
    """Normalize persisted privacy settings and apply the high-privacy contract."""
    advanced = dict(advanced or {})
    default_privacy = "high" if os.environ.get("GHOSTBROWSER_REQUIRE_PROXY", "1").strip().lower() in ("1", "true") else "standard"
    mode = str(advanced.get("privacy_mode", default_privacy)).strip().lower()
    advanced["privacy_mode"] = mode if mode in ("standard", "strict", "ephemeral", "high") else "standard"
    if not advanced.get("timezone"):
        advanced["timezone"] = timezone or "UTC"
    if not advanced.get("locale"):
        advanced["locale"] = locale or "en-US"
    if advanced["privacy_mode"] == "high":
        advanced.update({"webrtc_mode": "disabled", "block_service_workers": True,
                         "canvas_noise": True, "audio_noise": True, "webgl_noise": True})
        seed = secrets.randbelow(8_000_000) + 1_000_000
        advanced.setdefault("canvas_noise_seed", seed)
        advanced.setdefault("audio_noise_seed", seed + 1)
        os_name = advanced.get("os", "Windows")
        if os_name == "Mac":
            advanced.setdefault("webgl_vendor", "Apple Inc.")
            advanced.setdefault("webgl_renderer", "Apple GPU")
        elif os_name == "Linux":
            advanced.setdefault("webgl_vendor", "Google Inc. (NVIDIA)")
            advanced.setdefault("webgl_renderer", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660, OpenGL)")
        else:
            advanced.setdefault("webgl_vendor", "Google Inc. (Intel)")
            advanced.setdefault("webgl_renderer", "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)")
    return advanced

class ProfileManager:
    def __init__(self, override_dir: str = None):
        test_dir = override_dir or os.environ.get("GHOSTBROWSER_TEST_PROFILES_DIR")
        if test_dir:
            self.PROFILES_DIR = os.path.abspath(test_dir)
            self.metadata_file = os.path.join(self.PROFILES_DIR, "profiles_meta.json")
            self.key_file = os.path.join(self.PROFILES_DIR, ".master.key")
        else:
            self.PROFILES_DIR = PROFILES_DIR
            self.metadata_file = os.path.join(PROFILES_DIR, "profiles_meta.json")
            self.key_file = KEY_FILE
        self.dpapi_key_file = os.path.join(self.PROFILES_DIR, ".master.key.dpapi")

        os.makedirs(self.PROFILES_DIR, exist_ok=True)
        self._init_crypto()
        self._load_metadata()

    @staticmethod
    def _valid_profile_color(value) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 7
            and value.startswith("#")
            and all(character in "0123456789abcdefABCDEF" for character in value[1:])
        )

    @staticmethod
    def _normalize_tags(tags):
        """Validate and normalize a list of tags.

        * Each tag must be a string.
        * Trimmed, lowercased, and truncated to 24 characters.
        * Empty or duplicate tags are dropped.
        """
        if tags is None:
            return []
        if not isinstance(tags, (list, tuple, set)):
            raise ValueError("tags must be a list of strings")
        normalized = []
        seen = set()
        for tag in tags:
            if not isinstance(tag, str):
                raise ValueError(f"tag must be a string, got {type(tag).__name__}")
            cleaned = tag.strip().lower()[:24]
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized

    def _next_profile_color(self) -> str:
        """Return a display color not currently used by another profile."""
        used = {
            str(profile.get("color", "")).upper()
            for profile in self.profiles.values()
            if isinstance(profile, dict)
        }
        for color in PROFILE_COLOR_PALETTE:
            if color not in used:
                return color

        # Continue with a golden-angle sequence when the high-contrast palette
        # is exhausted. The used-color check makes the stored value unique.
        for index in range(len(self.profiles), len(self.profiles) + 4096):
            hue = ((index + 1) * 137.507764) % 360
            saturation = 0.66 + (index % 3) * 0.06
            lightness = 0.52 + (index % 2) * 0.08
            red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
            color = "#{:02X}{:02X}{:02X}".format(
                round(red * 255), round(green * 255), round(blue * 255)
            )
            if color not in used:
                return color

        # This is practically unreachable, but still guarantees a valid value.
        while True:
            color = f"#{uuid.uuid4().hex[:6].upper()}"
            if color not in used:
                return color

    def _init_crypto(self):
        if sys.platform != "win32":
            raise RuntimeError("Profile encryption requires Windows DPAPI; refusing unprotected key storage")

        if os.path.exists(self.dpapi_key_file):
            with open(self.dpapi_key_file, "rb") as key_file:
                key = self._unprotect_key_dpapi(key_file.read())
        elif os.path.exists(self.key_file):
            # One-time migration of legacy plaintext Fernet keys. Do not remove
            # the old key until the protected replacement is durably written.
            with open(self.key_file, "rb") as key_file:
                key = key_file.read()
            protected = self._protect_key_dpapi(key)
            self._write_protected_key(protected)
            os.remove(self.key_file)
        else:
            key = Fernet.generate_key()
            self._write_protected_key(self._protect_key_dpapi(key))
        self.cipher = Fernet(key)

    def _write_protected_key(self, protected_key: bytes) -> None:
        temporary = self.dpapi_key_file + ".tmp"
        with open(temporary, "wb") as f:
            f.write(protected_key)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.dpapi_key_file)

    @staticmethod
    def _protect_key_dpapi(key: bytes) -> bytes:
        return ProfileManager._crypt_protect_data(key, protect=True)

    @staticmethod
    def _unprotect_key_dpapi(protected_key: bytes) -> bytes:
        return ProfileManager._crypt_protect_data(protected_key, protect=False)

    @staticmethod
    def _crypt_protect_data(data: bytes, protect: bool) -> bytes:
        """Protect/unprotect bytes with the current Windows user's DPAPI key."""
        if sys.platform != "win32":
            raise RuntimeError("Windows DPAPI is unavailable on this platform")

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        input_blob = DATA_BLOB(len(data), raw)
        output_blob = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if protect:
            success = crypt32.CryptProtectData(
                ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
            )
        else:
            success = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
            )
        if not success:
            raise OSError("Windows DPAPI operation failed")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def _decrypt_json_field(self, value, fallback):
        if not isinstance(value, str) or not value.startswith("enc:"):
            return value
        try:
            return json.loads(self.cipher.decrypt(value[4:].encode("ascii")).decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            return fallback

    def _decrypt_text_field(self, value, fallback=None):
        if not isinstance(value, str) or not value.startswith("enc:"):
            return value
        try:
            return self.cipher.decrypt(value[4:].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
            return fallback

    def _encrypt_json_field(self, value) -> str:
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return "enc:" + self.cipher.encrypt(encoded).decode("ascii")

    def _encrypt_text_field(self, value: str) -> str:
        return "enc:" + self.cipher.encrypt(str(value).encode("utf-8")).decode("ascii")

    def _load_metadata(self):
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, "r") as f:
                    encrypted_profiles = json.load(f)

                self.profiles = {}
                needs_migration = False
                for pid, pdata in encrypted_profiles.items():
                    if not isinstance(pdata, dict):
                        continue
                    # Decrypt sensitive fields if present and encrypted
                    if "proxy" in pdata:
                        raw_proxy = pdata["proxy"]
                        pdata["proxy"] = self._decrypt_json_field(raw_proxy, None)
                        if isinstance(raw_proxy, dict):
                            needs_migration = True

                    if "advanced" in pdata:
                        raw_advanced = pdata["advanced"]
                        pdata["advanced"] = self._decrypt_json_field(raw_advanced, {})
                        if isinstance(raw_advanced, dict):
                            needs_migration = True

                    if "proxy_pin" in pdata:
                        raw_pin = pdata["proxy_pin"]
                        pdata["proxy_pin"] = self._decrypt_text_field(raw_pin, None)
                        if raw_pin and isinstance(raw_pin, str) and not raw_pin.startswith("enc:"):
                            needs_migration = True

                    # Migrate legacy profiles that stored provenance only inside the fingerprint.
                    # Launch-time fail-closed checks require top-level ai_provenance.
                    fingerprint = pdata.get("fingerprint")
                    if isinstance(fingerprint, dict) and not pdata.get("ai_provenance"):
                        provenance = fingerprint.get("_provenance")
                        if isinstance(provenance, dict):
                            pdata["ai_provenance"] = provenance
                            needs_migration = True

                    existing_color = str(pdata.get("color", "")).upper()
                    color_is_already_used = any(
                        str(profile.get("color", "")).upper() == existing_color
                        for profile in self.profiles.values()
                    )
                    if (
                        not self._valid_profile_color(pdata.get("color"))
                        or color_is_already_used
                    ):
                        pdata["color"] = self._next_profile_color()
                        needs_migration = True
                    else:
                        pdata["color"] = pdata["color"].upper()

                    if not isinstance(pdata.get("pinned"), bool):
                        pdata["pinned"] = False
                        needs_migration = True

                    self.profiles[pid] = pdata

                if needs_migration:
                    self._save_metadata()

            except json.JSONDecodeError:
                backup_path = self.metadata_file + f".corrupted.{int(datetime.now().timestamp())}.json"
                try:
                    shutil.copy2(self.metadata_file, backup_path)
                    print(f"[ProfileManager] ⚠️ Corrupted metadata backed up to {backup_path}")
                except Exception:
                    pass
                self.profiles = {}
        else:
            self.profiles = {}

    def _save_metadata(self):
        # We need to make a copy to encrypt before saving, without mutating the runtime self.profiles
        profiles_to_save = {}
        for pid, pdata in self.profiles.items():
            save_data = pdata.copy()
            if save_data.get("proxy"):
                save_data["proxy"] = self._encrypt_json_field(save_data["proxy"])
            if save_data.get("advanced"):
                save_data["advanced"] = self._encrypt_json_field(save_data["advanced"])
            if save_data.get("proxy_pin"):
                save_data["proxy_pin"] = self._encrypt_text_field(save_data["proxy_pin"])
            profiles_to_save[pid] = save_data

        tmp_file = self.metadata_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(profiles_to_save, f, indent=4)
            os.replace(tmp_file, self.metadata_file)
        except Exception as e:
            if os.path.exists(tmp_file):
                try: os.remove(tmp_file)
                except Exception: pass
            raise e

    def create_profile(self, name: str, proxy: dict = None, timezone: str = None, locale: str = None, advanced: dict = None, pin: str = None, tags: list = None):
        if proxy and (not timezone or not locale):
            server = proxy.get("server")
            if server:
                try:
                    guessed_locale, guessed_tz = guess_locale_timezone(server)
                    timezone = timezone or guessed_tz
                    locale = locale or guessed_locale
                except Exception:
                    pass

        advanced = _normalize_privacy_advanced(advanced, timezone, locale)

        profile_id = str(uuid.uuid4())
        profile_path = os.path.join(self.PROFILES_DIR, profile_id)
        os.makedirs(profile_path, exist_ok=True)

        user_agent = build_user_agent(advanced.get("os") if advanced else "Windows")

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
            "tags": self._normalize_tags(tags),
            "notes": "",
            "color": self._next_profile_color(),
            "pinned": False,
        }
        if is_valid_new_pin(pin):
            profile_data["pin_hash"] = _hash_pin(pin)

        self.profiles[profile_id] = profile_data
        self._save_metadata()
        return profile_data

    def register_profile(self, profile_id: str, name: str, proxy: dict = None, timezone: str = None, locale: str = None, advanced: dict = None, behavior: dict = None, fingerprint: dict = None, user_agent: str = None, pin: str = None, tags: list = None):
        if proxy and (not timezone or not locale):
            server = proxy.get("server")
            if server:
                try:
                    guessed_locale, guessed_tz = guess_locale_timezone(server)
                    timezone = timezone or guessed_tz
                    locale = locale or guessed_locale
                except Exception:
                    pass

        advanced = _normalize_privacy_advanced(advanced, timezone, locale)

        profile_path = os.path.join(self.PROFILES_DIR, profile_id)
        if not os.path.exists(profile_path):
            os.makedirs(profile_path, exist_ok=True)

        if not user_agent:
            user_agent = build_user_agent(advanced.get("os") if advanced else "Windows")

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
            "tags": self._normalize_tags(tags),
            "notes": "",
            "behavior": behavior or {},
            "fingerprint": fingerprint or {},
            "color": self._next_profile_color(),
            "pinned": False,
        }
        if is_valid_new_pin(pin):
            profile_data["pin_hash"] = _hash_pin(pin)

        self.profiles[profile_id] = profile_data
        self._save_metadata()
        return profile_data

    def get_profile(self, profile_id: str):
        return self.profiles.get(profile_id)

    def list_profiles(self):
        # Python's sort is stable, so profiles retain their normal creation
        # order inside the pinned and unpinned groups.
        return sorted(
            self.profiles.values(),
            key=lambda profile: not bool(profile.get("pinned", False)),
        )

    def list_profile_ids(self) -> list[str]:
        """Return the IDs of all known profiles."""
        return list(self.profiles.keys())

    def delete_profile(self, profile_id: str):
        if not profile_id or not isinstance(profile_id, str):
            raise ValueError("Invalid profile ID")

        profile = self.profiles.get(profile_id)
        if not profile:
            raise ValueError(f"Profile {profile_id} not found")

        path = profile.get("path")
        if not path:
            self.profiles.pop(profile_id, None)
            try:
                self._save_metadata()
            except Exception:
                self.profiles[profile_id] = profile
                raise
            return True

        # Canonicalize profile path
        canonical_profiles_dir = os.path.normcase(os.path.realpath(self.PROFILES_DIR))
        canonical_profile_path = os.path.normcase(os.path.realpath(path))

        # Reject empty paths, root paths, traversal, symlink escapes
        if not canonical_profile_path:
            raise ValueError("Empty profile path")
        if canonical_profile_path == canonical_profiles_dir:
            raise ValueError("Root profile path deletion blocked")
        if not canonical_profile_path.startswith(canonical_profiles_dir + os.sep):
            raise ValueError("Traversal deletion blocked")

        # Confirm process is stopped
        from backend.browser_manager import find_profile_processes
        procs = find_profile_processes(canonical_profile_path)
        if procs:
            raise RuntimeError("Cannot delete profile: browser process is still running.")

        # Stage physical deletion using a tombstone path
        tombstone_path = os.path.join(self.PROFILES_DIR, f"tombstone_{profile_id}")
        if os.path.exists(tombstone_path):
            shutil.rmtree(tombstone_path, ignore_errors=True)

        if os.path.exists(canonical_profile_path):
            try:
                os.rename(canonical_profile_path, tombstone_path)
            except Exception as e:
                raise RuntimeError(f"Failed to stage profile directory deletion: {str(e)}")

        # Remove database record and lock
        old_profile_data = self.profiles.pop(profile_id, None)

        # Save metadata atomically
        try:
            self._save_metadata()
        except Exception as e:
            # ROLLBACK: Restore database record and rename directory back
            if old_profile_data:
                self.profiles[profile_id] = old_profile_data
            if os.path.exists(tombstone_path):
                try:
                    os.rename(tombstone_path, canonical_profile_path)
                except Exception:
                    pass
            raise RuntimeError(f"Failed to save metadata during deletion: {str(e)}. Rollback completed.")

        # Complete final deletion of tombstone directory
        if os.path.exists(tombstone_path):
            try:
                shutil.rmtree(tombstone_path)
            except Exception as e:
                print(f"Warning: Failed to cleanup tombstone directory {tombstone_path}: {e}")

        from backend.lock_manager import lock_manager
        from backend.lock_manager import locks as _lm_locks
        if profile_id in _lm_locks:
            _lm_locks.pop(profile_id, None)

        return True

    def update_profile(self, profile_id: str, updates: dict):
        if profile_id in self.profiles:
            for k, v in updates.items():
                if k == "tags":
                    self.profiles[profile_id][k] = self._normalize_tags(v)
                elif isinstance(v, dict) and isinstance(self.profiles[profile_id].get(k), dict):
                    self.profiles[profile_id][k].update(v)
                else:
                    self.profiles[profile_id][k] = v
            if "advanced" in updates:
                self.profiles[profile_id]["advanced"] = _normalize_privacy_advanced(
                    self.profiles[profile_id].get("advanced"),
                    self.profiles[profile_id].get("timezone"),
                    self.profiles[profile_id].get("locale"),
                )
            self._save_metadata()
            return True
        return False

    def add_tags(self, profile_id: str, tags: list):
        """Add tags to a profile, deduplicating and preserving order."""
        if profile_id not in self.profiles:
            return False
        profile = self.profiles[profile_id]
        normalized = self._normalize_tags(tags)
        existing = set(profile.get("tags", []))
        updated = list(profile.get("tags", []))
        for tag in normalized:
            if tag not in existing:
                existing.add(tag)
                updated.append(tag)
        profile["tags"] = updated
        self._save_metadata()
        return True

    def remove_tags(self, profile_id: str, tags: list):
        """Remove tags from a profile. Missing tags are ignored."""
        if profile_id not in self.profiles:
            return False
        to_remove = set(self._normalize_tags(tags))
        profile = self.profiles[profile_id]
        profile["tags"] = [tag for tag in profile.get("tags", []) if tag not in to_remove]
        self._save_metadata()
        return True

    def rename_profile(self, profile_id: str, new_name: str):
        profile = self.profiles.get(profile_id)
        if profile:
            profile["name"] = new_name
            self._save_metadata()
            return True
        return False

    def set_profile_pin(self, profile_id: str, pin: str) -> bool:
        """Store a PBKDF2 hash of the given PIN for the profile."""
        if profile_id not in self.profiles or not is_valid_new_pin(pin):
            return False
        self.profiles[profile_id]["pin_hash"] = _hash_pin(pin)
        self._save_metadata()
        return True

    def verify_profile_pin(self, profile_id: str, pin: str) -> bool:
        """Return True if the provided PIN matches the stored hash."""
        profile = self.profiles.get(profile_id)
        if not profile or not isinstance(pin, str):
            return False
        pin_hash = profile.get("pin_hash")
        if not pin_hash:
            return False
        return _verify_pin_hash(pin, pin_hash)

    def clear_profile_storage(self, profile_id: str) -> bool:
        """Clear browsing data (cookies, cache, storage) while preserving profile identity and fingerprint."""
        if profile_id not in self.profiles:
            return False
        profile = self.profiles[profile_id]
        path = profile.get("path") or os.path.join(self.PROFILES_DIR, profile_id)
        if not os.path.exists(path):
            return True

        from backend.browser_manager import is_profile_running
        if is_profile_running(profile_id):
            raise RuntimeError("Cannot clear storage while browser profile is running.")

        storage_targets = [
            "Default/Cache", "Default/Code Cache", "Default/Cookies", "Default/Cookies-journal",
            "Default/IndexedDB", "Default/Local Storage", "Default/Session Storage",
            "Default/Network", "Default/Service Worker", "Default/Storage", "Default/GPUCache"
        ]
        for sub in storage_targets:
            target = os.path.join(path, sub.replace("/", os.sep))
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.isfile(target):
                try:
                    os.remove(target)
                except OSError:
                    pass
        return True

# Global instance
profile_manager = ProfileManager()

