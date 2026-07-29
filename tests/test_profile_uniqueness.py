import sys
import os
import tempfile
import asyncio
import hashlib
from unittest.mock import patch

# ---------------------------------------------------------------------------
# 1. Keep TemporaryDirectory and environment setup before production imports.
# ---------------------------------------------------------------------------
orig_env = os.environ.copy()
orig_sys_path = list(sys.path)

td = tempfile.TemporaryDirectory()
os.environ["GHOSTBROWSER_TEST_PROFILES_DIR"] = td.name
os.environ["GHOSTBROWSER_TEST_ENV"] = "1"

# Add project root to sys.path (run this from the repo root)
sys.path.append(os.getcwd())

from backend.ai_generator import generate_fingerprint_ai
from backend.ai_coherence_validator import coherence_validator


# ---------------------------------------------------------------------------
# Deterministic, coherent offline fingerprint generator.
# ---------------------------------------------------------------------------
_NUM_PROFILES = 50

_LOCATION_PAIRS = [
    ("en-US", "America/New_York"),
    ("en-CA", "America/Toronto"),
    ("en-GB", "Europe/London"),
    ("de-DE", "Europe/Berlin"),
    ("fr-FR", "Europe/Paris"),
    ("es-ES", "Europe/Madrid"),
    ("it-IT", "Europe/Rome"),
    ("nl-NL", "Europe/Amsterdam"),
    ("ja-JP", "Asia/Tokyo"),
    ("zh-CN", "Asia/Shanghai"),
    ("zh-SG", "Asia/Singapore"),
    ("ar-AE", "Asia/Dubai"),
    ("en-AU", "Australia/Sydney"),
    ("en-NZ", "Pacific/Auckland"),
    ("pt-BR", "America/Sao_Paulo"),
    ("en-ZA", "Africa/Johannesburg"),
    ("en-US", "America/Chicago"),
    ("en-US", "America/Denver"),
    ("en-US", "America/Los_Angeles"),
    ("en-GB", "Europe/Lisbon"),
    ("fr-FR", "Europe/Brussels"),
    ("de-DE", "Europe/Vienna"),
    ("es-ES", "America/Mexico_City"),
    ("en-CA", "America/Vancouver"),
    ("en-GB", "Asia/Hong_Kong"),
]

_SCREEN_RESOLUTIONS = [
    "1920x1080", "2560x1440", "3840x2160", "1366x768",
    "1920x1200", "2560x1080", "3440x1440", "2880x1800",
    "1600x900", "1280x720", "2560x1600", "3840x2400",
]

_GPU_MODELS = [
    "3060", "3070", "3080", "3090",
    "4060", "4070", "4080", "4090",
    "3060 Ti", "3070 Ti", "3080 Ti", "4070 Ti",
    "4080 SUPER", "4090", "3050", "3050 Ti",
    "2060", "2070", "2080", "2080 Ti",
    "1660", "1660 Ti", "1070", "1080",
    "1080 Ti", "A2000", "A4000", "A4500",
]

_HW_COMBOS = [
    (4, 8), (6, 12), (8, 16), (8, 32),
    (10, 16), (12, 32), (12, 64), (16, 32),
    (16, 64), (20, 64), (24, 64), (32, 64),
    (4, 16), (6, 16), (10, 32), (14, 32),
    (18, 64), (22, 64), (28, 64), (8, 24),
]


def _build_coherent_fingerprint(i: int) -> dict:
    locale, timezone = _LOCATION_PAIRS[i % len(_LOCATION_PAIRS)]
    cores, memory_gb = _HW_COMBOS[i % len(_HW_COMBOS)]
    resolution = _SCREEN_RESOLUTIONS[i % len(_SCREEN_RESOLUTIONS)]
    gpu_model = _GPU_MODELS[i % len(_GPU_MODELS)]

    sec_ch_ua = (
        '"Not)A;Brand";v="8", '
        '"Chromium";v="149", '
        '"Google Chrome";v="149"'
    )

    return {
        "userAgent": (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/149.0.{i}.0 Safari/537.36"
        ),
        "platform": "Win32",
        "os": "Windows",
        "hardwareConcurrency": cores,
        "deviceMemory": memory_gb,
        "cpu_cores": cores,
        "memory_gb": memory_gb,
        "screen_resolution": resolution,
        "screen_color_depth": 24,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": (
            f"ANGLE (NVIDIA, NVIDIA GeForce RTX {gpu_model} "
            f"Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "timezone": timezone,
        "locale": locale,
        "languages": [locale, locale.split("-")[0]],
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": '"Windows"',
        "client_hints": {
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "platformVersion": "10.0.0",
            "uaFullVersion": "149.0.0.0",
        },
        "canvas_noise": True,
        "webgl_noise": True,
        "audio_noise": True,
    }


# ---------------------------------------------------------------------------
async def main():
    loop = asyncio.get_running_loop()
    old_exception_handler = loop.get_exception_handler()
    unhandled_loop_errors = []

    def loop_exception_handler(loop, context):
        unhandled_loop_errors.append(
            context.get("exception") or context.get("message")
        )

    loop.set_exception_handler(loop_exception_handler)

    errors = []
    network_call_count = [0]

    def assert_no_network(*args, **kwargs):
        network_call_count[0] += 1
        raise AssertionError("NETWORK_CALL_ATTEMPTED")

    prod_meta_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "profiles_data", "profiles_meta.json")
    )

    def file_hash(path):
        if not os.path.exists(path):
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    orig_prod_meta_hash = file_hash(prod_meta_path)

    generated = []
    next_index = [0]

    async def mock_cloudflare_call(*args, **kwargs):
        """Deterministic offline source of coherent fingerprints."""
        i = next_index[0]
        next_index[0] += 1
        return _build_coherent_fingerprint(i)

    try:
        with patch('backend.ai_generator._call_direct_cloudflare', side_effect=mock_cloudflare_call), \
             patch('backend.ai_generator._call_via_racing_proxy', side_effect=assert_no_network), \
             patch('backend.ai_generator._shared_client.post', side_effect=assert_no_network), \
             patch('httpx.AsyncClient.post', side_effect=assert_no_network):

            for _ in range(_NUM_PROFILES):
                fp = await generate_fingerprint_ai(
                    target_os="Windows",
                    target_browser="Chrome",
                    chrome_major_version=149,
                )
                generated.append(fp)

    except AssertionError as e:
        if "NETWORK_CALL_ATTEMPTED" in str(e):
            errors.append("A live network call was attempted during offline generation")
        else:
            errors.append(f"Assertion error: {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        errors.append(f"Unhandled exception during generation: {e}")

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    if len(generated) != _NUM_PROFILES:
        errors.append(f"Expected {_NUM_PROFILES} fingerprints, got {len(generated)}")

    coherence_failures = []
    coherence_scores = []
    for idx, fp in enumerate(generated):
        result = coherence_validator.validate(fp)
        coherence_scores.append(result.get("score", 0))
        if not result.get("passed"):
            coherence_failures.append((idx, result))

    if coherence_failures:
        for idx, result in coherence_failures:
            errors.append(
                f"Fingerprint {idx} failed coherence (score={result.get('score')}): "
                f"{result.get('issues')}"
            )

    pass_rate = (len(generated) - len(coherence_failures)) / max(1, len(generated))

    # -----------------------------------------------------------------------
    # Uniqueness statistics
    # -----------------------------------------------------------------------
    key_fields = [
        "userAgent", "webgl_vendor", "webgl_renderer",
        "screen_resolution", "timezone", "locale",
        "cpu_cores", "memory_gb",
    ]

    missing_fields = []
    for fp in generated:
        for field in key_fields:
            if field not in fp:
                missing_fields.append(field)
    if missing_fields:
        errors.append(f"Missing key fields in generated fingerprints: {set(missing_fields)}")

    stats = {}
    for field in key_fields:
        values = [fp[field] for fp in generated if field in fp]
        stats[field] = len(set(values))

    # Pairwise uniqueness: no two fingerprints share the exact same values for
    # every key field.
    identity_tuples = []
    for fp in generated:
        identity_tuples.append(tuple(fp.get(field) for field in key_fields))

    unique_tuples = set(identity_tuples)
    if len(unique_tuples) != len(identity_tuples):
        errors.append(
            f"Pairwise uniqueness violated: {len(identity_tuples) - len(unique_tuples)} "
            f"duplicate identity tuple(s) found"
        )

    # -----------------------------------------------------------------------
    # Production metadata integrity check
    # -----------------------------------------------------------------------
    if file_hash(prod_meta_path) != orig_prod_meta_hash:
        errors.append("Production profiles_meta.json was modified during the test")

    # -----------------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------------
    print(f"Generated fingerprints: {len(generated)}")
    print(f"Coherence pass rate: {pass_rate * 100:.1f}%")
    print(f"Coherence score min / mean / max: "
          f"{min(coherence_scores) if coherence_scores else 'N/A'} / "
          f"{sum(coherence_scores) / max(1, len(coherence_scores)):.1f} / "
          f"{max(coherence_scores) if coherence_scores else 'N/A'}")
    print("Unique values per field:")
    for field, count in stats.items():
        print(f"  {field}: {count}")
    print(f"Unique identity tuples: {len(unique_tuples)} / {len(identity_tuples)}")
    print(f"Network call attempts: {network_call_count[0]}")

    # -----------------------------------------------------------------------
    # Cleanup and assertions
    # -----------------------------------------------------------------------
    loop.set_exception_handler(old_exception_handler)
    if unhandled_loop_errors:
        errors.append(f"Unhandled asyncio exceptions: {unhandled_loop_errors}")

    sys.path[:] = orig_sys_path
    if sys.path != orig_sys_path:
        errors.append("sys.path was not restored exactly")

    os.environ.clear()
    os.environ.update(orig_env)
    if dict(os.environ) != orig_env:
        errors.append("os.environ was not restored exactly")

    try:
        td.cleanup()
    except Exception as e:
        errors.append(f"TemporaryDirectory cleanup failed: {e}")

    if network_call_count[0] != 0:
        errors.append(f"Expected 0 network calls, saw {network_call_count[0]}")

    if errors:
        print("FAILURES:")
        for err in errors:
            print(f"- {err}")
        sys.exit(1)

    print("PASS: Profile uniqueness and realism verified offline.")


if __name__ == "__main__":
    asyncio.run(main())
