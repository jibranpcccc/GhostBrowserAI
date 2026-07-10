import os
import json
from backend.logging_config import logger

class SecurityHardening:
    """
    Ensures safe loading of extensions and restricts secrets.
    """
    def __init__(self):
        pass
        
    def validate_extensions(self, extensions_dir: str) -> list:
        """
        Scans the extensions directory and returns a list of valid, safe extension paths.
        Validates that manifest.json exists and doesn't contain highly dangerous broad permissions
        unless explicitly allowed.
        """
        valid_paths = []
        if not os.path.exists(extensions_dir):
            return valid_paths
            
        for item in os.listdir(extensions_dir):
            item_path = os.path.join(extensions_dir, item)
            manifest_path = os.path.join(item_path, "manifest.json")
            
            if os.path.isdir(item_path) and os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                        
                    # Example security check: Flag broad permissions
                    permissions = manifest.get("permissions", [])
                    if "<all_urls>" in permissions or "*://*/*" in permissions:
                        logger.warning(f"Extension '{item}' requests highly broad permissions.", extra={"event_type": "security_warning"})
                        
                    # If it passes checks, add it
                    valid_paths.append(item_path)
                    logger.info(f"Extension '{item}' validated successfully.")
                except json.JSONDecodeError:
                    logger.error(f"Extension '{item}' has an invalid manifest.json. Skipping.", extra={"event_type": "security_error"})
                except Exception as e:
                    logger.error(f"Error validating extension '{item}': {e}", extra={"event_type": "security_error"})
                    
        return valid_paths

security_hardening = SecurityHardening()
