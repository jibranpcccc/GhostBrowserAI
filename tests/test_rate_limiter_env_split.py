"""A18: rate-limit config must split between prod and isolated test env.

Outside GHOSTBROWSER_TEST_ENV the auth/pin/default limits must stay tight so a
remote attacker cannot brute-force the admin token; inside the test env they
are relaxed so the suite does not self-throttle.
"""
import importlib
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import backend.auth as auth


class RateLimiterEnvSplitTests(unittest.TestCase):
    def tearDown(self):
        os.environ["GHOSTBROWSER_TEST_ENV"] = "1"
        importlib.reload(auth)

    def test_test_env_relaxed_limits(self):
        with mock.patch.dict(os.environ, {"GHOSTBROWSER_TEST_ENV": "1"}):
            mod = importlib.reload(auth)
        self.assertEqual(mod.RATE_LIMITERS["default"].max_requests, 1000)
        self.assertEqual(mod.RATE_LIMITERS["auth"].max_requests, 100)
        self.assertEqual(mod.RATE_LIMITERS["pin"].max_requests, 50)

    def test_prod_tight_limits_when_test_env_absent(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GHOSTBROWSER_TEST_ENV", None)
            mod = importlib.reload(auth)
        self.assertEqual(mod.RATE_LIMITERS["default"].max_requests, 100)
        self.assertEqual(mod.RATE_LIMITERS["auth"].max_requests, 10)
        self.assertEqual(mod.RATE_LIMITERS["pin"].max_requests, 5)

    def test_prod_limits_when_test_env_falsy(self):
        with mock.patch.dict(os.environ, {"GHOSTBROWSER_TEST_ENV": "0"}):
            mod = importlib.reload(auth)
        self.assertEqual(mod.RATE_LIMITERS["auth"].max_requests, 10)

    def test_unknown_limiter_raises_value_error(self):
        request = mock.Mock()
        request.client = mock.Mock()
        request.client.host = "127.0.0.1"
        with self.assertRaises(ValueError):
            auth.check_rate_limit(request, "does-not-exist")


if __name__ == "__main__":
    unittest.main()
