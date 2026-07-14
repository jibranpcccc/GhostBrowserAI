import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.browser_manager import parse_proxy_string


class TestProxyVerification(unittest.TestCase):
    def test_empty_and_none(self):
        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("")
        self.assertEqual(str(ctx.exception), "Empty proxy string")

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string(None)
        self.assertEqual(str(ctx.exception), "Empty proxy string")

    def test_standard_parsing(self):
        res = parse_proxy_string("1.2.3.4:8080")
        self.assertEqual(res, {
            "server": "http://1.2.3.4:8080",
            "username": None,
            "password": None,
            "host": "1.2.3.4",
            "port": 8080,
            "scheme": "http"
        })

    def test_schemes(self):
        for scheme in ["http", "https", "socks4", "socks5"]:
            res = parse_proxy_string(f"{scheme}://1.2.3.4:8080")
            self.assertEqual(res["server"], f"{scheme}://1.2.3.4:8080")
            self.assertEqual(res["scheme"], scheme)

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("ftp://1.2.3.4:8080")
        self.assertIn("Unsupported proxy scheme", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("socks://1.2.3.4:8080")
        self.assertIn("Unsupported proxy scheme", str(ctx.exception))

    def test_legacy_format_4_parts(self):
        res = parse_proxy_string("1.2.3.4:8080:user123:pass123")
        self.assertEqual(res, {
            "server": "http://1.2.3.4:8080",
            "username": "user123",
            "password": "pass123",
            "host": "1.2.3.4",
            "port": 8080,
            "scheme": "http"
        })

    def test_credential_percent_decoding(self):
        res = parse_proxy_string("http://user%3Anaming:pwd%40word123@1.2.3.4:8080")
        self.assertEqual(res["username"], "user:naming")
        self.assertEqual(res["password"], "pwd@word123")
        self.assertEqual(res["server"], "http://1.2.3.4:8080")

        res2 = parse_proxy_string("http://hello%20world:secret%23123@1.2.3.4:8080")
        self.assertEqual(res2["username"], "hello world")
        self.assertEqual(res2["password"], "secret#123")

    def test_ipv6_brackets_preservation(self):
        res = parse_proxy_string("[2001:db8::1]:8080")
        self.assertEqual(res["server"], "http://[2001:db8::1]:8080")
        self.assertEqual(res["host"], "2001:db8::1")
        self.assertEqual(res["port"], 8080)

        res2 = parse_proxy_string("http://[2001:db8::1]:8080")
        self.assertEqual(res2["server"], "http://[2001:db8::1]:8080")
        self.assertEqual(res2["host"], "2001:db8::1")

        res3 = parse_proxy_string("socks5://user:pass@[2001:db8::1]:8080")
        self.assertEqual(res3, {
            "server": "socks5://[2001:db8::1]:8080",
            "username": "user",
            "password": "pass",
            "host": "2001:db8::1",
            "port": 8080,
            "scheme": "socks5"
        })

        res4 = parse_proxy_string("[2001:db8::1]:8080:user:pass")
        self.assertEqual(res4, {
            "server": "http://[2001:db8::1]:8080",
            "username": "user",
            "password": "pass",
            "host": "2001:db8::1",
            "port": 8080,
            "scheme": "http"
        })

    def test_ipv6_errors_and_unbracketed(self):
        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("2001:db8::1:8080")
        self.assertIn("Ambiguous unbracketed IPv6 address", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("[2001:db8::1:8080")
        self.assertTrue(issubclass(type(ctx.exception), ValueError))

    def test_invalid_urls(self):
        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("http://1.2.3.4:8080/some/path")
        self.assertIn("Proxy URL contains invalid path", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("http://1.2.3.4:8080?query=1")
        self.assertIn("Proxy URL contains invalid query", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            parse_proxy_string("http://1.2.3.4:8080#fragment")
        self.assertIn("Proxy URL contains invalid fragment", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
