"""Pure launch-policy helpers kept separate from browser process orchestration."""

from __future__ import annotations

from dataclasses import dataclass


ALLOWED_DEVICE_SCALE_FACTORS = (1.0, 1.25, 1.5, 2.0)
MOBILE_DEVICE_SCALE_FACTORS = (2.0, 2.25, 2.5, 2.75, 3.0, 3.5)


def get_privacy_launch_flags(advanced: dict) -> list[str]:
    """Return Chromium privacy restrictions requested by a high-privacy profile."""
    if advanced.get("privacy_mode") != "high":
        return []
    # Do not disable UserAgentClientHint: real Chrome always exposes
    # navigator.userAgentData, and CDP/UA metadata spoofing depends on it.
    return [
        "--disable-features=ThirdPartyCookies,GenericSensorExtraClasses,WebGPU,ServiceWorker",
        "--disable-blink-features=BatteryStatus",
    ]


@dataclass(frozen=True)
class SurfaceLaunchPolicy:
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    block_service_workers: bool
    service_worker_policy: str


def build_surface_launch_policy(advanced: dict, has_proxy: bool) -> SurfaceLaunchPolicy:
    is_mobile = advanced.get("device_type") == "mobile"
    default_resolution = "390x844" if is_mobile else "1920x1080"
    resolution = str(advanced.get("screen_resolution", default_resolution))
    parts = resolution.split("x", 1)
    try:
        width, height = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        width, height = (390, 844) if is_mobile else (1920, 1080)
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        width, height = (390, 844) if is_mobile else (1920, 1080)

    default_scale = 2.0 if is_mobile else 1.0
    allowed_scales = MOBILE_DEVICE_SCALE_FACTORS if is_mobile else ALLOWED_DEVICE_SCALE_FACTORS
    try:
        scale = float(advanced.get("device_scale_factor", default_scale))
    except (TypeError, ValueError):
        scale = default_scale
    if scale not in allowed_scales:
        scale = default_scale

    block_service_workers = bool(advanced.get("block_service_workers", has_proxy))
    return SurfaceLaunchPolicy(
        viewport_width=width,
        viewport_height=height,
        device_scale_factor=scale,
        block_service_workers=block_service_workers,
        service_worker_policy="blocked_for_proxy" if block_service_workers else "native_direct",
    )
