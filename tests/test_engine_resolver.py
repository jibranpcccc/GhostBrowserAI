import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import engine_resolver


class _FakePlaywrightBrowser:
    def __init__(self, executable_path: str):
        self._executable_path = executable_path

    @property
    def executable_path(self) -> str:
        return self._executable_path


class _FakeSyncPlaywright:
    def __init__(self, executable_path: str):
        self.chromium = _FakePlaywrightBrowser(executable_path)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeAsyncPlaywright:
    def __init__(self, executable_path: str):
        self.chromium = _FakePlaywrightBrowser(executable_path)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class EngineResolverTests(unittest.TestCase):
    def setUp(self):
        self._env_var = "GHOSTBROWSER_CHROMIUM_BINARY"
        self._old_env = os.environ.get(self._env_var)
        os.environ.pop(self._env_var, None)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop(self._env_var, None)
        else:
            os.environ[self._env_var] = self._old_env

    def _make_executable(self, path: Path) -> None:
        path.touch()
        path.chmod(0o700)

    def test_env_variable_existing_file_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_binary = Path(tmpdir) / ("custom-chrome.exe" if os.name == "nt" else "custom-chrome")
            self._make_executable(custom_binary)
            os.environ[self._env_var] = str(custom_binary)

            result = engine_resolver.get_chromium_executable_path()
            self.assertEqual(result, str(custom_binary.resolve()))

    def test_missing_env_variable_falls_back_to_playwright(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playwright_path = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            self._make_executable(playwright_path)

            def fake_sync_playwright():
                return _FakeSyncPlaywright(str(playwright_path))

            with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
                result = engine_resolver.get_chromium_executable_path()

        self.assertEqual(result, str(playwright_path))

    def test_env_variable_missing_file_falls_back_to_playwright(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_binary = Path(tmpdir) / "missing-chrome.exe"
            # Do not create the file; just set the env var.
            os.environ[self._env_var] = str(missing_binary)

            playwright_path = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            self._make_executable(playwright_path)

            def fake_sync_playwright():
                return _FakeSyncPlaywright(str(playwright_path))

            with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
                result = engine_resolver.get_chromium_executable_path()

        self.assertEqual(result, str(playwright_path))

    def test_env_variable_non_executable_file_falls_back_to_playwright(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # A .txt file is not considered executable on either POSIX or
            # Windows, so it should fall back to Playwright.
            non_exec = Path(tmpdir) / "custom-chrome.txt"
            non_exec.write_text("not executable", encoding="utf-8")
            non_exec.chmod(0o444)
            os.environ[self._env_var] = str(non_exec)

            playwright_path = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            self._make_executable(playwright_path)

            def fake_sync_playwright():
                return _FakeSyncPlaywright(str(playwright_path))

            with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
                result = engine_resolver.get_chromium_executable_path()

        self.assertEqual(result, str(playwright_path))

    def test_empty_env_variable_ignored(self):
        os.environ[self._env_var] = "   "

        with tempfile.TemporaryDirectory() as tmpdir:
            playwright_path = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            self._make_executable(playwright_path)

            def fake_sync_playwright():
                return _FakeSyncPlaywright(str(playwright_path))

            with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
                result = engine_resolver.get_chromium_executable_path()

        self.assertEqual(result, str(playwright_path))

    def test_resolve_chromium_version_parses_version_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            binary.write_text("mock", encoding="utf-8")
            binary.chmod(0o700)

            def fake_check_output(cmd, **kwargs):
                if str(cmd[0]) == str(binary) and cmd[1] == "--version":
                    return "Chromium 132.0.6834.110 (official build)"
                raise RuntimeError("unexpected command")

            with patch("subprocess.check_output", fake_check_output):
                version = engine_resolver.resolve_chromium_version(str(binary))

        self.assertEqual(version, "132.0.6834.110")

    def test_resolve_chromium_version_missing_file_returns_none(self):
        version = engine_resolver.resolve_chromium_version("/does/not/exist")
        self.assertIsNone(version)


class EngineResolverAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env_var = "GHOSTBROWSER_CHROMIUM_BINARY"
        self._old_env = os.environ.get(self._env_var)
        os.environ.pop(self._env_var, None)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop(self._env_var, None)
        else:
            os.environ[self._env_var] = self._old_env

    def _make_executable(self, path: Path) -> None:
        path.touch()
        path.chmod(0o700)

    async def test_async_env_variable_existing_file_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_binary = Path(tmpdir) / ("custom-chrome.exe" if os.name == "nt" else "custom-chrome")
            self._make_executable(custom_binary)
            os.environ[self._env_var] = str(custom_binary)

            result = await engine_resolver.get_chromium_executable_path_async()
            self.assertEqual(result, str(custom_binary.resolve()))

    async def test_async_missing_env_variable_falls_back_to_playwright(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playwright_path = Path(tmpdir) / ("chrome.exe" if os.name == "nt" else "chrome")
            self._make_executable(playwright_path)

            def fake_async_playwright():
                return _FakeAsyncPlaywright(str(playwright_path))

            with patch("playwright.async_api.async_playwright", fake_async_playwright):
                result = await engine_resolver.get_chromium_executable_path_async()

        self.assertEqual(result, str(playwright_path))


if __name__ == "__main__":
    unittest.main()
