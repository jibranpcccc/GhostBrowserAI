import os
import json
from backend.logging_config import logger


def validate_extensions(extensions_dir: str) -> list:
    """Return a list of valid, safe extension paths under extensions_dir."""
    valid_paths = []
    if not os.path.exists(extensions_dir):
        return valid_paths

    for item in os.listdir(extensions_dir):
        if item == "dummy_extension" and os.environ.get("GHOSTBROWSER_TEST_ENV") != "1":
            continue

        item_path = os.path.join(extensions_dir, item)
        manifest_path = os.path.join(item_path, "manifest.json")

        if os.path.isdir(item_path) and os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                permissions = manifest.get("permissions", [])
                if "<all_urls>" in permissions or "*://*/*" in permissions:
                    logger.warning(f"Extension '{item}' requests highly broad permissions.", extra={"event_type": "security_warning"})

                valid_paths.append(item_path)
                logger.info(f"Extension '{item}' validated successfully.")
            except json.JSONDecodeError:
                logger.error(f"Extension '{item}' has an invalid manifest.json. Skipping.", extra={"event_type": "security_error"})
            except Exception as e:
                logger.error(f"Error validating extension '{item}': {e}", extra={"event_type": "security_error"})

    return valid_paths


def check_extensions_for_privacy(valid_paths: list) -> list:
    """Return privacy warnings for validated extension directories."""
    warnings = []
    fingerprint_permissions = {
        "<all_urls>", "*://*/*", "tabs", "cookies", "debugger", "management",
        "nativeMessaging", "webRequest", "webRequestBlocking",
    }
    for extension_path in valid_paths:
        manifest_path = os.path.join(extension_path, "manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except (OSError, json.JSONDecodeError):
            warnings.append({"extension": os.path.basename(extension_path), "warning": "Unable to inspect extension manifest"})
            continue

        permissions = set(manifest.get("permissions") or []) | set(manifest.get("host_permissions") or [])
        broad = sorted(permissions & fingerprint_permissions)
        if broad:
            warnings.append({
                "extension": manifest.get("name") or os.path.basename(extension_path),
                "permissions": broad,
                "warning": "Broad extension permissions can expose browsing or fingerprint data",
            })
    return warnings


def get_privacy_recommendations(profile: dict) -> list[dict]:
    """Suggest profile-level changes that reduce unnecessary data exposure."""
    advanced = profile.get("advanced") or {}
    recommendations = []
    if advanced.get("privacy_mode") != "high":
        recommendations.append({"setting": "privacy_mode", "recommended": "high", "reason": "Restricts high-entropy browser APIs and third-party cookies."})
    if advanced.get("webrtc_mode") not in ("protected", "disabled"):
        recommendations.append({"setting": "webrtc_mode", "recommended": "protected", "reason": "Avoids exposing direct network candidates."})
    if not advanced.get("block_service_workers"):
        recommendations.append({"setting": "block_service_workers", "recommended": True, "reason": "Prevents persistent background storage and requests."})
    return recommendations
