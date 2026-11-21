"""Fixtures shared by the tests: isolated data and a local demo service."""

import socket
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import demo_app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Give each test a fresh data folder so local run history stays untouched."""
    monkeypatch.setenv("PHONEREALITY_DATA_DIR", str(tmp_path / "phonereality"))
    return tmp_path / "phonereality"


@pytest.fixture(scope="session")
def demo_server(tmp_path_factory):
    """Start one demo service for the test session and return its base URL."""
    server, base_url = demo_app.start_in_background(tmp_path_factory.mktemp("server"), port=0)
    yield base_url
    server.shutdown()


@pytest.fixture
def free_port():
    """Reserve an unused local port for tests that need an unavailable service."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
