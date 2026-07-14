import sys
import os
import asyncio
import pytest
from unittest.mock import patch, AsyncMock

sys.path.append(os.getcwd())

from backend.profile_creator import profile_creator
from backend.profile_manager import profile_manager, PROFILES_DIR


@pytest.mark.asyncio
async def test_invalid_inputs():
    res = await profile_creator.create_zero_leak_profile(name="   ")
    assert res["status"] == "error"
    assert "Validation failed" in res["message"]

    res = await profile_creator.create_zero_leak_profile(name="test/../../hacked")
    assert res["status"] == "error"
    assert "traversal" in res["message"]

    res = await profile_creator.create_zero_leak_profile(name="ValidName", advanced_ui={"cpu_cores": 256})
    assert res["status"] == "error"
    assert "CPU cores" in res["message"]

    res = await profile_creator.create_zero_leak_profile(name="ValidName", advanced_ui={"timezone": "NotATimezone"})
    assert res["status"] == "error"
    assert "Timezone" in res["message"]

    res = await profile_creator.create_zero_leak_profile(name="ValidName", advanced_ui={"locale": "invalid-locale-format!!!"})
    assert res["status"] == "error"
    assert "locale" in res["message"]


@pytest.mark.asyncio
async def test_creation_transaction():
    async def mock_validate_fail(profile, fp):
        raise RuntimeError("Simulated validation failure mid-lifecycle")

    with patch("backend.ai_auto_validator.auto_validator.validate_profile", side_effect=mock_validate_fail):
        res = await profile_creator.create_zero_leak_profile(name="Transactional-Test-Profile")

    assert res["status"] == "error"
    assert "Creation failed" in res["message"]

    temp_dirs = [d for d in os.listdir(PROFILES_DIR) if d.startswith("temp_")]
    assert len(temp_dirs) == 0, "Temp directories were not cleaned up!"

    matching_db = [p for p in profile_manager.list_profiles() if "Transactional-Test" in p["name"]]
    assert len(matching_db) == 0, "DB record was not cleaned up!"


if __name__ == "__main__":
    asyncio.run(test_invalid_inputs())
    asyncio.run(test_creation_transaction())
    print("ALL CREATION/TRANSACTION TESTS PASSED!")
