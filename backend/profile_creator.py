import asyncio
import json
import os
import platform
import random
import re
import shutil
import uuid
import zoneinfo
from types import SimpleNamespace
from urllib.parse import urlparse

from backend.profile_manager import profile_manager
from backend.ai_generator import generate_fingerprint_ai, sanitize_native_surface_fields
from backend.ai_coherence_validator import coherence_validator
from backend.config import AI_GENERATION_TIMEOUT, get_installed_chromium_major_version
from backend.ai_data_sanitizer import data_sanitizer
from backend.error_codes import PUBLIC_CODE_MESSAGES
from backend.logging_config import logger

QUARANTINE_DIR = os.path.join(os.path.dirname(__file__), "..", "quarantined_profiles")
os.makedirs(QUARANTINE_DIR, exist_ok=True)
QUARANTINE_META = os.path.join(QUARANTINE_DIR, "quarantine_meta.json")


def get_real_host_os() -> str:
    """Return the host operating system using canonical fingerprint OS names."""
    name = platform.system()
    if name == "Darwin":
        return "Mac"
    if name in ("Windows", "Linux"):
        return name
    return "Linux"


def _validate_inputs(name: str, proxy: dict = None, advanced_ui: dict = None) -> dict:
    """Validate and normalize frontend-provided inputs. Raises ValueError on failure."""
    # 1. Name validation
    if not name or not isinstance(name, str) or not name.strip():
        raise ValueError("Profile name cannot be empty or whitespace-only")
    if len(name) > 100:
        raise ValueError("Profile name is too long (maximum 100 characters)")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Profile name contains invalid characters or path traversal sequences")

    for p in profile_manager.list_profiles():
        if p["name"].strip().lower() == name.strip().lower():
            raise ValueError("Profile name already exists")

    # 2. Proxy validation
    if proxy is not None:
        if not isinstance(proxy, dict):
            raise ValueError("Proxy must be a key-value dictionary")

        server = proxy.get("server")
        if not server or not isinstance(server, str):
            raise ValueError("Proxy server URL is required")

        parsed = urlparse(server)
        if parsed.scheme not in ["http", "https", "socks5", "socks4"]:
            raise ValueError("Proxy protocol must be http, https, socks5, or socks4")

        if parsed.path and parsed.path != "/":
            raise ValueError("Proxy URL must not contain a path component")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError("Proxy URL must not contain query strings, parameters, or fragments")

        netloc = parsed.netloc
        if not netloc:
            raise ValueError("Proxy server must contain a host and port")

        host_port = netloc
        if "@" in netloc:
            user_pass, host_port = netloc.split("@", 1)
            if ":" not in user_pass:
                raise ValueError("Embedded proxy credentials must be in format user:password")

        if ":" not in host_port:
            raise ValueError("Proxy host and port are required (e.g. host:port)")

        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Proxy port must be an integer between 1 and 65535")

        if not host:
            raise ValueError("Proxy host cannot be empty")

    # 3. Advanced UI normalization (mutates the caller's dict for compatibility)
    if advanced_ui is None:
        advanced_ui = {}
    if not isinstance(advanced_ui, dict):
        raise ValueError("Advanced settings must be a key-value dictionary")

    for forbidden in ("fonts", "plugins"):
        if forbidden in advanced_ui:
            raise ValueError(f"{forbidden} cannot be specified; browser surface is inherited from host")

    if "os" in advanced_ui:
        if advanced_ui["os"] not in ["Windows", "Mac", "Linux"]:
            raise ValueError("Operating system must be one of: Windows, Mac, Linux")
        from backend.device_cohorts import require_host_compatible
        require_host_compatible(advanced_ui["os"])

    if "screen_resolution" in advanced_ui:
        screen = advanced_ui["screen_resolution"]
        if not isinstance(screen, str):
            raise ValueError("Screen resolution must be a string")
        if not re.match(r"^(\d+)x(\d+)$", screen):
            raise ValueError("Screen resolution must be in format WIDTHxHEIGHT (e.g. 1920x1080)")

    if "cpu_cores" in advanced_ui:
        try:
            cores = int(advanced_ui["cpu_cores"])
            if cores < 1 or cores > 32:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("CPU cores must be an integer between 1 and 32")

    if "memory_gb" in advanced_ui:
        try:
            mem = int(advanced_ui["memory_gb"])
            if mem < 1 or mem > 64:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Device memory must be an integer between 1 and 64 GB")

    if "timezone" in advanced_ui and advanced_ui["timezone"] != "":
        if advanced_ui["timezone"] not in zoneinfo.available_timezones():
            raise ValueError("Timezone is invalid")

    if "locale" in advanced_ui:
        locale = advanced_ui["locale"]
        if not isinstance(locale, str) or not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z]{2})?$", locale):
            raise ValueError("Locale must be a valid locale string (e.g., en-US)")

    if "webrtc_mode" in advanced_ui:
        mode = str(advanced_ui["webrtc_mode"]).strip().lower()
        if mode not in ("protected", "altered", "real", "disabled"):
            raise ValueError("webrtc_mode must be one of: protected, altered, real, disabled")
        if mode not in ("protected", "altered"):
            raise ValueError("webrtc_mode must be protected or altered")
        advanced_ui["webrtc_mode"] = "protected"

    if "privacy_mode" in advanced_ui:
        mode = str(advanced_ui["privacy_mode"]).strip().lower()
        if mode not in ("standard", "strict", "ephemeral", "high"):
            raise ValueError("privacy_mode must be one of: standard, strict, ephemeral, high")
        advanced_ui["privacy_mode"] = mode

    if "experimental_measuretext" in advanced_ui:
        if not isinstance(advanced_ui["experimental_measuretext"], bool):
            raise ValueError("experimental_measuretext must be a boolean")

    return advanced_ui


def _quarantine_profile(profile: dict, issues: list):
    quarantined_data = []
    if os.path.exists(QUARANTINE_META):
        try:
            with open(QUARANTINE_META, "r") as f:
                quarantined_data = json.load(f)
        except json.JSONDecodeError:
            pass

    sanitized = sanitize_native_surface_fields(dict(profile))
    from backend.proxy_manager import redact_proxy_record
    if sanitized.get("proxy"):
        sanitized["proxy"] = redact_proxy_record(sanitized["proxy"])
    sanitized.pop("proxy_pin", None)
    sanitized["quarantine_reason"] = issues
    quarantined_data.append(sanitized)

    with open(QUARANTINE_META, "w") as f:
        json.dump(quarantined_data, f, indent=4)

    if os.path.exists(profile["path"]):
        target_path = os.path.join(QUARANTINE_DIR, profile["id"])
        try:
            shutil.move(profile["path"], target_path)
        except Exception as e:
            logger.error(f"Failed to move physical profile to quarantine: {e}")


async def create_zero_leak_profile(
    name: str,
    proxy: dict = None,
    advanced_ui: dict = None,
    skip_warming: bool = False,
    tags: list = None,
    pin: str = None,
) -> dict:
    print(f"[Orchestrator] Starting Zero-Leak Creation for: {name}")

    test_env = os.environ.get("GHOSTBROWSER_TEST_ENV", "").strip().lower()
    require_proxy = os.environ.get("GHOSTBROWSER_REQUIRE_PROXY", "1").strip().lower()
    in_test_env = test_env in ("1", "true")
    proxy_explicitly_required = require_proxy in ("1", "true", "yes")
    proxy_explicitly_disabled = require_proxy in ("0", "false", "no")

    if not in_test_env and proxy_explicitly_disabled:
        logger.warning("GHOSTBROWSER_REQUIRE_PROXY=0: creating profile without a verified proxy.")

    if not in_test_env and proxy_explicitly_required and not proxy:
        return {
            "status": "error",
            "message": "Validation failed: production mode requires a verified proxy. Set GHOSTBROWSER_REQUIRE_PROXY=0 only for local testing.",
        }

    try:
        advanced_ui = _validate_inputs(name, proxy, advanced_ui)
    except ValueError as e:
        return {"status": "error", "message": f"Validation failed: {e}"}

    try:
        get_installed_chromium_major_version()
    except Exception:
        logger.error("Cannot determine installed Chromium version", exc_info=True)
        return {
            "status": "error",
            "message": PUBLIC_CODE_MESSAGES["CHROMIUM_VERSION_MISSING"],
            "code": "CHROMIUM_VERSION_MISSING",
        }

    max_attempts = 3
    host_os = get_real_host_os()

    for attempt in range(max_attempts):
        print(f"[Orchestrator] Attempt {attempt + 1}/{max_attempts}")
        print("[Orchestrator] Step 1: Generating Fingerprint via Kimi AI...")

        try:
            fp = await asyncio.wait_for(generate_fingerprint_ai(), timeout=AI_GENERATION_TIMEOUT)
        except asyncio.TimeoutError:
            return {"status": "error", "code": "KIMI_TIMEOUT", "message": PUBLIC_CODE_MESSAGES["KIMI_TIMEOUT"]}
        except Exception as e:
            logger.error("Kimi fingerprint generation failed for attempt %s: %s", attempt + 1, type(e).__name__)
            return {
                "status": "error",
                "code": "KIMI_UNAVAILABLE",
                "message": PUBLIC_CODE_MESSAGES["KIMI_UNAVAILABLE"],
            }

        fp_os = fp.get("os")
        if fp_os not in ("Windows", "Mac", "Linux"):
            return {"status": "error", "code": "FINGERPRINT_MISMATCH", "message": PUBLIC_CODE_MESSAGES["FINGERPRINT_MISMATCH"]}
        if fp_os != host_os:
            return {"status": "error", "code": "FINGERPRINT_MISMATCH", "message": PUBLIC_CODE_MESSAGES["FINGERPRINT_MISMATCH"]}

        if fp.get("_is_fallback"):
            print("[Orchestrator] STRICT MODE: Kimi AI unavailable. All Cloudflare accounts exhausted.")
            print("[Orchestrator] Profile creation REFUSED. No profile is ever made without Kimi AI.")
            return {
                "status": "error",
                "message": PUBLIC_CODE_MESSAGES["KIMI_UNAVAILABLE"],
                "code": "KIMI_UNAVAILABLE",
            }

        advanced = {
            "os": fp.get("os"),
            "screen_resolution": fp.get("screen_resolution"),
            "cpu_cores": fp.get("cpu_cores"),
            "memory_gb": fp.get("memory_gb"),
            "webgl_vendor": fp.get("webgl_vendor"),
            "webgl_renderer": fp.get("webgl_renderer"),
            "audio_noise": fp.get("audio_noise", True),
            "sec_ch_ua": fp.get("sec_ch_ua"),
            "sec_ch_ua_platform": fp.get("sec_ch_ua_platform"),
            "disable_automation": True,
            "headless": False,
        }

        if advanced_ui:
            for key in ("headless", "canvas_noise", "webgl_noise", "audio_noise", "webrtc_mode", "device_type", "privacy_mode", "experimental_measuretext"):
                if key in advanced_ui:
                    advanced[key] = advanced_ui[key]

        resolved_tz = fp.get("timezone", "UTC")
        resolved_locale = fp.get("locale", "en-US")

        from backend.profile_manager import _normalize_privacy_advanced
        advanced = _normalize_privacy_advanced(advanced, resolved_tz, resolved_locale)

        if proxy:
            print(f"[Orchestrator] Synchronizing Timezone and Locale for proxy {proxy.get('server')}")
            from backend.proxy_manager import apply_geo_to_profile
            geo_profile = apply_geo_to_profile(
                {"timezone": resolved_tz, "locale": resolved_locale, "advanced": advanced_ui or {}},
                proxy["server"],
            )
            resolved_tz = geo_profile["timezone"]
            resolved_locale = geo_profile["locale"]
            if geo_profile["advanced"].get("timezone"):
                advanced["timezone"] = geo_profile["advanced"]["timezone"]
            if geo_profile["advanced"].get("locale"):
                advanced["locale"] = geo_profile["advanced"]["locale"]

        print("[Orchestrator] Step 2: Running Coherence Validation...")
        coherence_result = coherence_validator.validate(fp)

        if not coherence_result["passed"]:
            print(f"[Orchestrator] Coherence failed: {coherence_result['issues']}")
            continue

        print(f"[Orchestrator] Coherence passed with score: {coherence_result['score']}")

        print("[Orchestrator] Step 3: Initializing Isolated Directory...")
        final_id = str(uuid.uuid4())
        final_path = os.path.join(profile_manager.PROFILES_DIR, final_id)
        try:
            os.makedirs(final_path, exist_ok=True)
            final_profile = profile_manager.register_profile(
                profile_id=final_id,
                name=name,
                proxy=proxy,
                timezone=resolved_tz,
                locale=resolved_locale,
                advanced=advanced,
                behavior=fp.get("behavior", {}),
                tags=tags,
                pin=pin,
            )
            fp = sanitize_native_surface_fields(fp)
            final_profile["fingerprint"] = fp
            if not fp.get("userAgent"):
                fp["userAgent"] = profile_manager.build_user_agent(fp.get("os"))
            final_profile["user_agent"] = fp["userAgent"]
            provenance = fp.get("_provenance") or {}
            if provenance:
                final_profile["ai_provenance"] = provenance
            profile_manager._save_metadata()
        except Exception as e:
            print(f"[Orchestrator] Profile registration failed, rolling back: {e}")
            logger.error("Profile registration failed for %s: %s", final_id, type(e).__name__, exc_info=True)
            profile_manager.profiles.pop(final_id, None)
            try:
                profile_manager._save_metadata()
            except Exception:
                pass
            if os.path.exists(final_path):
                try:
                    shutil.rmtree(final_path)
                except Exception:
                    pass
            return {"status": "error", "code": "CREATE_FAILED", "message": PUBLIC_CODE_MESSAGES["CREATE_FAILED"]}

        print("[Orchestrator] Step 4: Running AI Auto Validator...")
        from backend.ai_auto_validator import auto_validator
        try:
            validation_result = await auto_validator.validate_profile(final_profile, fp)
        except Exception as e:
            print(f"[Orchestrator] Mid-lifecycle validation failed, rolling back: {e}")
            profile_manager.profiles.pop(final_id, None)
            try:
                profile_manager._save_metadata()
            except Exception:
                pass
            if os.path.exists(final_path):
                try:
                    shutil.rmtree(final_path)
                except Exception:
                    pass
            return {"status": "error", "code": "CREATE_FAILED", "message": PUBLIC_CODE_MESSAGES["CREATE_FAILED"]}

        if validation_result["decision"] != "ACCEPT":
            issues = validation_result.get("issues", [])
            only_ai_fallback = all("AI analysis failed" in i or "Fallback" in i for i in issues) if issues else False

            if only_ai_fallback:
                print("[Orchestrator] AI validator unreachable but technical checks PASSED. Accepting profile.")
            else:
                print(f"[Orchestrator] Validator flagged real issues: {issues}")
                print("[Orchestrator] Step 5: Sanitizing Data...")
                data_sanitizer.sanitize(final_profile["path"])

                re_validation = await auto_validator.validate_profile(final_profile, fp)
                re_issues = re_validation.get("issues", [])
                re_only_ai = all("AI analysis failed" in i or "Fallback" in i for i in re_issues) if re_issues else False

                if re_validation["decision"] != "ACCEPT" and not re_only_ai:
                    print("[Orchestrator] Sanitization failed. Quarantining profile.")
                    logger.warning(f"Profile {final_profile['id']} quarantined due to real leak: {re_issues}")
                    _quarantine_profile(final_profile, re_issues)
                    profile_manager.delete_profile(final_profile["id"])
                    continue

            mode = advanced.get("privacy_mode", "standard")
        _test_env = os.environ.get("GHOSTBROWSER_TEST_ENV", "").strip().lower() in ("1", "true")
        if not skip_warming and not _test_env and mode not in ("strict", "ephemeral"):
            print("[Orchestrator] Step 5: AI Headless Cookie Warmer...")
            from backend.cookie_robot import cookie_robot
            await cookie_robot.start_warming([final_profile["id"]], 3, 5)
        else:
            print("[Orchestrator] Skipping Cookie Warmer as requested.")

        print(f"[Orchestrator] Profile is Zero-Leak! (Score: {validation_result.get('final_score', 'N/A')})")
        return {"status": "success", "profile": final_profile}

    return {"status": "error", "message": "Failed to create a coherent, leak-free profile after maximum attempts."}


async def create_healed_profile(
    name: str,
    proxy: dict = None,
    advanced_ui: dict = None,
    max_attempts: int = 3,
):
    """Create a profile and iteratively mutate its fingerprint until the offline scan passes."""
    result = await create_zero_leak_profile(
        name=name,
        proxy=proxy,
        advanced_ui=advanced_ui,
        skip_warming=True,
    )
    if result["status"] != "success":
        return result

    profile = result["profile"]
    fingerprint = profile.get("fingerprint") or {}
    advanced = profile.get("advanced") or {}

    try:
        from scripts.detection_check import _run_scans
    except Exception as exc:
        logger.warning(f"Healing scan unavailable: {exc}")
        return {
            "status": "success",
            "profile": profile,
            "heal": {"skipped": True, "reason": "Healing scan unavailable"},
        }

    last_scan = None
    passed = False
    for attempt in range(max_attempts):
        last_scan = await _run_scans(profile)
        passed = last_scan.get("status") == "passed"
        if passed or attempt == max_attempts - 1:
            break

        _mutate_profile_fingerprint(fingerprint, advanced)
        coherence = coherence_validator.validate(fingerprint)
        if not coherence["passed"]:
            logger.warning(
                f"Healing coherence check failed on attempt {attempt + 1}: {coherence['issues']}"
            )

        profile_manager.update_profile(
            profile["id"],
            {"fingerprint": fingerprint, "advanced": advanced},
        )

    return {
        "status": "success",
        "profile": profile,
        "heal": {
            "attempts": attempt + 1,
            "passed": passed,
            "scan": last_scan,
        },
    }


def _mutate_profile_fingerprint(fingerprint: dict, advanced: dict):
    """Mutate a profile fingerprint's hardware, noise, and WebGL fields."""
    os_name = fingerprint.get("os", "Windows")
    revision = fingerprint.get("revision", 0) + 1
    fingerprint["revision"] = revision
    advanced["revision"] = revision

    if os_name == "Mac":
        fingerprint["webgl_vendor"] = "Apple Inc."
        fingerprint["webgl_renderer"] = random.choice([
            "Apple GPU",
            "ANGLE (Apple, Apple M2, Unspecified)",
        ])
    elif os_name == "Linux":
        fingerprint["webgl_vendor"] = random.choice([
            "Google Inc. (NVIDIA)",
            "Google Inc. (AMD)",
        ])
        fingerprint["webgl_renderer"] = random.choice([
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660, OpenGL)",
            "ANGLE (AMD, AMD Radeon RX 6700 XT, OpenGL)",
        ])
    else:
        fingerprint["webgl_vendor"] = random.choice([
            "Google Inc. (NVIDIA)",
            "Google Inc. (AMD)",
            "Google Inc. (Intel)",
        ])
        fingerprint["webgl_renderer"] = random.choice([
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ])

    cores_opts = [2, 4, 6, 8, 12, 16]
    memory_opts = [4, 6, 8, 12, 16, 24, 32]
    for _ in range(10):
        cores = random.choice(cores_opts)
        memory = random.choice(memory_opts)
        if cores <= memory * 2:
            break
    else:
        cores, memory = 8, 16

    fingerprint["cpu_cores"] = cores
    fingerprint["memory_gb"] = memory
    fingerprint["hardwareConcurrency"] = cores
    fingerprint["deviceMemory"] = memory
    advanced["cpu_cores"] = cores
    advanced["memory_gb"] = memory

    scale = random.choice([1.0, 1.25, 1.5, 2.0])
    fingerprint["device_scale_factor"] = scale
    advanced["device_scale_factor"] = scale

    seed = random.randint(1_000_000, 9_999_999)
    fingerprint["canvas_noise_seed"] = seed
    fingerprint["audio_noise_seed"] = seed + 1
    advanced["canvas_noise_seed"] = seed
    advanced["audio_noise_seed"] = seed + 1


profile_creator = SimpleNamespace(
    create_zero_leak_profile=create_zero_leak_profile,
    create_healed_profile=create_healed_profile,
    _quarantine_profile=_quarantine_profile,
)
