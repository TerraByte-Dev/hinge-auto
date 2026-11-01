import pytest

import adb


@pytest.fixture(autouse=True)
def _adb_is_plain_adb(monkeypatch, request):
    """Pin the adb binary to "adb" so fake-subprocess tests see stable
    commands. Tests of the resolver itself opt out with @real_adb_binary."""
    if "real_adb_binary" in request.keywords:
        return
    monkeypatch.setattr(adb, "_adb_binary", lambda: "adb")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_adb_binary: use adb._adb_binary as-is")
