"""Routing must work however Vercel presents the rewritten /api/* path."""

import importlib

import pytest
from fastapi.testclient import TestClient

import api.index as server


@pytest.fixture
def vercel_client(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    module = importlib.reload(server)
    yield TestClient(module.app)
    monkeypatch.delenv("VERCEL")
    importlib.reload(server)


@pytest.mark.parametrize(
    "url",
    [
        "/api/health",                       # original path preserved
        "/api/health?__route=health",        # original path plus rewrite query
        "/api/index?__route=health",         # rewrite target
        "/api/index.py?__route=health",
        "/health",                           # prefix stripped
    ],
)
def test_health_reachable_on_vercel(vercel_client, url):
    response = vercel_client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_unknown_route_reports_path(vercel_client):
    response = vercel_client.get("/api/index?__route=nope")
    assert response.status_code == 404
    assert response.json()["path"] == "/api/nope"
