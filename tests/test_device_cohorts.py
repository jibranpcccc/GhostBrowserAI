import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.device_cohorts import COHORTS, apply_cohort, require_host_compatible, select_cohort


class DeviceCohortTests(unittest.TestCase):
    def test_selection_is_stable_and_not_unique_per_profile(self):
        first = select_cohort("profile-a", "Windows")
        self.assertEqual(first, select_cohort("profile-a", "Windows"))
        self.assertIn(first, COHORTS["Windows"])

    def test_normalization_keeps_correlated_fields_together(self):
        original = {
            "cpu_cores": 3,
            "hardwareConcurrency": 3,
            "memory_gb": 64,
            "deviceMemory": 64,
            "screen_resolution": "9999x9999",
            "webgl_vendor": "impossible",
            "webgl_renderer": "impossible",
        }
        normalized, cohort = apply_cohort(original, "profile-b", "Windows")
        self.assertEqual(normalized["cpu_cores"], normalized["hardwareConcurrency"])
        self.assertEqual(normalized["memory_gb"], normalized["deviceMemory"])
        self.assertEqual(normalized["screen_resolution"], cohort["screen_resolution"])
        self.assertEqual(normalized["device_scale_factor"], cohort["device_scale_factor"])
        self.assertEqual(normalized["device_cohort"], cohort["id"])

    def test_resolution_preference_only_selects_valid_template(self):
        cohort = select_cohort("profile-c", "Windows", "2560x1440")
        self.assertEqual(cohort["screen_resolution"], "2560x1440")

    @patch("backend.device_cohorts.platform.system", return_value="Windows")
    def test_cross_os_profile_is_rejected(self, _platform):
        self.assertEqual(require_host_compatible("Windows"), "Windows")
        with self.assertRaisesRegex(ValueError, "Mac on Windows host"):
            require_host_compatible("Mac")

    def test_every_cohort_uses_common_native_scale(self):
        for templates in COHORTS.values():
            for cohort in templates:
                self.assertIn(cohort["device_scale_factor"], (1.0, 1.25, 1.5, 2.0))
                self.assertGreaterEqual(cohort["memory_gb"], 8)
                self.assertGreaterEqual(cohort["cpu_cores"], 4)


class BrowserSurfaceContractTests(unittest.TestCase):
    def test_dpr_and_network_are_not_javascript_fabricated(self):
        path = os.path.join(os.path.dirname(__file__), "..", "backend", "browser_manager.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("Object.defineProperty(window, 'devicePixelRatio'", source)
        # NetworkInformation is now intentionally spoofed by the anti-detect init script.
        self.assertIn('device_scale_factor=config["device_scale_factor"]', source)


if __name__ == "__main__":
    unittest.main()
