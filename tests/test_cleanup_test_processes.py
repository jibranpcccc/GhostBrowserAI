"""Cleanup script must only ever target this project's own Chromium processes.

Regression coverage for the reviewer findings: quoted/space user-data-dir
paths must match, and prefix siblings like ``profiles_data_backup`` must NOT
match (a false positive would kill the user's real Chrome).
"""
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts import cleanup_test_processes as ctp


class GhostBrowserProcDetectionTests(unittest.TestCase):
    def test_bundled_dist_chromium_matches(self):
        args = [os.path.join(PROJECT_ROOT, "dist", "GhostBrowser", "chrome.exe"), "--type=renderer"]
        self.assertTrue(ctp._is_ghostbrowser_proc(args))

    def test_user_data_dir_under_profiles_data_matches(self):
        ud = os.path.join(PROJECT_ROOT, "profiles_data", "p123")
        args = ["/usr/lib/chromium/chrome", f"--user-data-dir={ud}"]
        self.assertTrue(ctp._is_ghostbrowser_proc(args))

    def test_user_data_dir_with_spaces_matches(self):
        ud = os.path.join(PROJECT_ROOT, "profiles_data", "profile with spaces")
        args = ["C:/Program Files/Google/Chrome/chrome.exe", f"--user-data-dir={ud}", "about:blank"]
        self.assertTrue(ctp._is_ghostbrowser_proc(args))

    def test_separated_flag_value_matches(self):
        ud = os.path.join(PROJECT_ROOT, "profiles_data", "p456")
        args = ["/usr/bin/chromium", "--user-data-dir", ud]
        self.assertTrue(ctp._is_ghostbrowser_proc(args))

    def test_prefix_sibling_profiles_data_does_not_match(self):
        ud = os.path.join(PROJECT_ROOT, "profiles_data_backup", "p1")
        args = ["/usr/bin/chromium", f"--user-data-dir={ud}"]
        self.assertFalse(ctp._is_ghostbrowser_proc(args))

    def test_sibling_contains_segment_does_not_match(self):
        ud = os.path.join(PROJECT_ROOT, "profiles_data2", "p1")
        args = ["/usr/bin/chromium", f"--user-data-dir={ud}"]
        self.assertFalse(ctp._is_ghostbrowser_proc(args))

    def test_unrelated_chrome_does_not_match(self):
        args = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "--user-data-dir=C:/Users/me/AppData/Local/Google/Chrome"]
        self.assertFalse(ctp._is_ghostbrowser_proc(args))

    def test_empty_cmdline_does_not_match(self):
        self.assertFalse(ctp._is_ghostbrowser_proc([]))
        self.assertFalse(ctp._is_ghostbrowser_proc(None))

    def test_no_user_data_dir_does_not_match(self):
        args = ["/usr/bin/chromium", "--headless"]
        self.assertFalse(ctp._is_ghostbrowser_proc(args))

    def test_main_refuses_without_psutil(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            with mock.patch.object(ctp.subprocess, "run") as run:
                ctp.main()
        # No subprocess kill command should ever be issued.
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
