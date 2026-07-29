import sys
import os
import asyncio
import uuid
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.getcwd())

from backend import browser_manager

async def test_direct_port_0_binding():
    print("\n--- Testing Direct Port 0 Binding in run_metadata_probe ---")

    mock_server_instance = MagicMock()
    # Mock server_address to simulate dynamic port allocation (e.g., port 54321)
    mock_server_instance.server_address = ("127.0.0.1", 54321)

    # We need to mock playwright launch/goto to avoid running actual browser
    with patch("http.server.HTTPServer", return_value=mock_server_instance) as mock_httpserver, \
         patch("playwright.async_api.async_playwright") as mock_playwright:

        # Setup mock_playwright hierarchy
        mock_p = AsyncMock()
        mock_playwright.return_value.start = AsyncMock(return_value=mock_p)
        mock_browser = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_page = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
        # Mock evaluate to return valid metadata
        mock_page.evaluate = AsyncMock(return_value={
            "isSecure": True,
            "ua": "test-ua",
            "uadata": {"platformVersion": "10.0.0"}
        })

        # Run probe
        res = await browser_manager.run_metadata_probe("/dummy/path", force_headless=True)

        # Assert HTTPServer was constructed with ("127.0.0.1", 0)
        mock_httpserver.assert_called_once()
        args, kwargs = mock_httpserver.call_args
        assert args[0] == ("127.0.0.1", 0), f"Expected bind to ('127.0.0.1', 0), got {args[0]}"
        print(" -> PASS: Verified server bound directly to port 0.")

        # Ensure server was shut down and closed cleanly
        mock_server_instance.serve_forever.assert_called_once()
        mock_server_instance.shutdown.assert_called_once()
        mock_server_instance.server_close.assert_called_once()
        print(" -> PASS: Verified server served and shutdown cleanly.")


async def test_concurrency_lock_release_and_deduplication():
    print("\n--- Testing Concurrency Lock Release and Deduplication ---")
    browser_manager._probed_metadata_cache.clear()
    browser_manager.probe_futures.clear()

    # Create an event to control when the simulated metadata probe returns
    event = asyncio.Event()
    probe_called = 0

    async def mock_run_metadata_probe(exe_path, force_headless):
        nonlocal probe_called
        probe_called += 1
        await event.wait()  # Yield control
        return {
            "isSecure": True,
            "ua": "mocked-ua",
            "uadata": {"platformVersion": "10.0.0", "bitness": "64"}
        }

    # A unique resolver result prevents an unrelated in-flight probe from a
    # prior test from completing into this test's cache key.
    executable_path = f"/dummy/path-{uuid.uuid4()}"

    # Patch the resolver imported by browser_manager, rather than the legacy
    # config path resolver no longer used by probe_native_metadata.
    with patch("backend.browser_manager.run_metadata_probe", side_effect=mock_run_metadata_probe), \
         patch("backend.browser_manager.get_chromium_executable_path_async", new=AsyncMock(return_value=executable_path)), \
         patch("backend.config.get_installed_chromium_version", return_value="149.0.0.0"):

        # 1. Spawn the first probe task
        t1 = asyncio.create_task(browser_manager._probe_native_metadata_impl(force_headless=True))

        # Give t1 a chance to run and reach the awaited event
        await asyncio.sleep(0.05)

        # Ensure probe_lock is NOT held while the probe runs in the background.
        # This is critical so other concurrent tasks don't block on the lock.
        assert not browser_manager.probe_lock.locked(), "Concurrency lock remained held during metadata probe!"
        print(" -> PASS: Verified lock was released during execution of the probe.")

        # 2. Spawn a second concurrent probe task
        t2 = asyncio.create_task(browser_manager._probe_native_metadata_impl(force_headless=True))

        # Give t2 a chance to run
        await asyncio.sleep(0.05)

        # Verify that only 1 probe call was initiated (deduplication)
        assert probe_called == 1, f"Expected 1 probe invocation, got {probe_called}"
        print(" -> PASS: Verified concurrent requests are deduplicated to a single probe.")

        # 3. Resolve the event to let the probe finish
        event.set()

        res1 = await t1
        res2 = await t2

        # Both must return the same correct mocked result
        assert res1 == res2
        assert res1["ua"] == "mocked-ua"
        print(" -> PASS: Verified both concurrent tasks received the correct metadata.")

        # Verify cleanup of the future from the mapping
        assert len(browser_manager.probe_futures) == 0, f"Expected probe_futures to be empty, got {browser_manager.probe_futures}"
        print(" -> PASS: Verified future was removed from active mapping upon resolution.")


async def test_cache_lookup():
    print("\n--- Testing Cache Lookup and Isolation ---")
    browser_manager._probed_metadata_cache.clear()
    browser_manager.probe_futures.clear()

    probe_called = 0

    async def mock_run_metadata_probe(exe_path, force_headless):
        nonlocal probe_called
        probe_called += 1
        return {
            "isSecure": True,
            "ua": f"mocked-ua-headless-{force_headless}",
            "uadata": {"platformVersion": "10.0.0"}
        }

    executable_path = f"/dummy/path-{uuid.uuid4()}"
    with patch("backend.browser_manager.run_metadata_probe", side_effect=mock_run_metadata_probe), \
         patch("backend.browser_manager.get_chromium_executable_path_async", new=AsyncMock(return_value=executable_path)), \
         patch("backend.config.get_installed_chromium_version", return_value="149.0.0.0"):

        # 1. Warm the cache for force_headless=True
        res1 = await browser_manager._probe_native_metadata_impl(force_headless=True)
        assert probe_called == 1
        print(" -> PASS: First probe invoked successfully.")

        # 2. Call again with force_headless=True: should hit cache directly
        res2 = await browser_manager._probe_native_metadata_impl(force_headless=True)
        assert probe_called == 1  # Probe count must NOT increment
        assert res1 == res2
        print(" -> PASS: Cache hit succeeded (returned cached metadata directly).")

        # 3. Call with force_headless=False: should result in a cache miss and trigger a new probe
        res3 = await browser_manager._probe_native_metadata_impl(force_headless=False)
        assert probe_called == 2  # Probe count must increment
        assert res3["ua"] == "mocked-ua-headless-False"
        print(" -> PASS: Cache isolation works (different parameters trigger new probe).")


async def main():
    try:
        await test_direct_port_0_binding()
        await test_concurrency_lock_release_and_deduplication()
        await test_cache_lookup()
        print("\nALL METADATA PROBE VERIFICATION TESTS PASSED SUCCESSFULLY!")
    except AssertionError as e:
        print(f"\nTEST FAILURE: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
