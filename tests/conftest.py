"""Fixtures de test. Todo hermético: fakeredis, sin red real, sin Anonimal real."""
from __future__ import annotations

from pathlib import Path

import fakeredis
import pytest

from app.config import Settings
from app.main import create_app
from app.queue import JobQueue

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX = REPO_ROOT / "privacy_matrix.yaml"


def make_settings(**overrides) -> Settings:
    env = {
        "APP_MODE": "standalone",
        "PRIVACY_MATRIX_PATH": str(MATRIX),
        "ALLOW_PRIVATE_TARGETS": "0",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings(env=env)


@pytest.fixture
def fake_queue() -> JobQueue:
    return JobQueue(fakeredis.FakeStrictRedis())


@pytest.fixture
def client_factory(fake_queue):
    from fastapi.testclient import TestClient

    def _factory(**setting_overrides):
        settings = make_settings(**setting_overrides)
        app = create_app(settings, queue=fake_queue)
        return TestClient(app)

    return _factory
