"""Unit tests for backend.network_coherence module."""

import pytest
from backend.network_coherence import (
    validate_network_coherence,
    validate_webrtc_candidates,
)


def test_webrtc_leak_detection():
    leaky_candidates = [
        "candidate:1 1 UDP 2122260223 192.168.1.105 54321 typ host",
        "candidate:2 1 UDP 1686052607 198.51.100.1 54322 typ srflx",
    ]
    res = validate_webrtc_candidates(leaky_candidates)
    assert not res["is_safe"]
    assert "192.168.1.105" in res["leaked_ips"]


def test_webrtc_mdns_safe():
    mdns_candidates = [
        "candidate:1 1 UDP 2122260223 7e3b90aa-9a99-4d6d-9b51-9efb7b0a1d4a.local 54321 typ host",
        "candidate:2 1 UDP 1686052607 198.51.100.1 54322 typ srflx",
    ]
    res = validate_webrtc_candidates(mdns_candidates)
    assert res["is_safe"]
    assert len(res["leaked_ips"]) == 0


def test_network_coherence_direct_mode():
    profile = {
        "timezone": "America/New_York",
        "locale": "en-US",
        "proxy": None,
    }
    result = validate_network_coherence(
        profile_data=profile,
        observed_public_ip="198.51.100.5",
        observed_country="US",
        observed_webrtc_ip="198.51.100.5",
    )
    assert result["status"] == "COHERENT"
    assert not result["is_proxied"]
    assert not result["webrtc_leak_detected"]


def test_network_coherence_webrtc_divergence_detected():
    profile = {
        "timezone": "America/New_York",
        "locale": "en-US",
        "proxy": "http://10.0.0.1:8080",
    }
    result = validate_network_coherence(
        profile_data=profile,
        observed_public_ip="198.51.100.5",
        observed_country="US",
        observed_webrtc_ip="203.0.113.99",
    )
    assert result["is_proxied"]
    assert result["webrtc_leak_detected"]
    assert any("WebRTC IP" in note for note in result["notes"])

