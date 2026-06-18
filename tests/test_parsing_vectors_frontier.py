"""Tests de parsing auto-reparable, vector store y frontera persistente."""
from __future__ import annotations

import fakeredis
import pytest

from app.crawl.frontier import RedisFrontier, SessionStore
from app.parsing.adaptive import SelfHealingExtractor
from app.store.vectors import EmbeddingClient, EmbeddingError, InMemoryVectorIndex, cosine


# --------------------------------------------------------------------------- parsing
def test_selector_matches_and_records_fingerprint():
    store = {}
    ext = SelfHealingExtractor(store, profile="x.com")
    html = "<html><body><h1 id='t' class='title big'>Hola Mundo</h1></body></html>"
    r = ext.extract(html, "titulo", "h1.title")
    assert r.value == "Hola Mundo"
    assert r.healed is False
    assert store["x.com::titulo"]["el_id"] == "t"


def test_self_heals_when_selector_breaks():
    store = {}
    ext = SelfHealingExtractor(store, profile="x.com")
    # 1ra corrida: el selector funciona y guarda el fingerprint.
    ext.extract("<h1 id='t' class='title'>Precio: 100</h1>", "campo", "h1.title")
    # 2da corrida: cambió la clase (.title → .headline), el selector rompe.
    nuevo = "<h2 id='t' class='headline'>Precio: 100</h2>"
    r = ext.extract(nuevo, "campo", "h1.title")
    assert r.healed is True
    assert r.value == "Precio: 100"
    assert r.score > 0


def test_no_fingerprint_no_value():
    ext = SelfHealingExtractor({}, profile="x.com")
    r = ext.extract("<p>nada</p>", "campo", "h1.inexistente")
    assert r.value is None
    assert r.healed is False


# --------------------------------------------------------------------------- vectors
def test_cosine_identical_is_one():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_in_memory_index_ranks_by_similarity():
    idx = InMemoryVectorIndex()
    idx.add("a", [1.0, 0.0, 0.0])
    idx.add("b", [0.0, 1.0, 0.0])
    idx.add("c", [0.9, 0.1, 0.0])
    top = idx.search([1.0, 0.0, 0.0], k=2)
    assert [jid for jid, _ in top] == ["a", "c"]


def test_embedding_client_with_fake_http(monkeypatch):
    client = EmbeddingClient("https://api.x.com/v1", "key", "model")

    class _Resp:
        is_success = True
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    out = client.embed(["hola"])
    assert out == [[0.1, 0.2, 0.3]]


def test_embedding_client_unconfigured_raises():
    with pytest.raises(EmbeddingError):
        EmbeddingClient("", "", "m").embed(["x"])


# --------------------------------------------------------------------------- frontier
def test_frontier_dedup_and_resume():
    r = fakeredis.FakeStrictRedis()
    f = RedisFrontier(r, "crawl1")
    assert f.push("https://x.com/a", 0) is True
    assert f.push("https://x.com/a", 0) is False   # dedup
    assert f.push("https://x.com/b", 1) is True
    assert f.pending() == 2

    # Otro worker retoma la MISMA frontera (persistente).
    f2 = RedisFrontier(r, "crawl1")
    url, depth = f2.pop()
    assert (url, depth) == ("https://x.com/a", 0)
    assert f2.seen("https://x.com/a") is True
    assert f2.pending() == 1


def test_session_store_roundtrip():
    r = fakeredis.FakeStrictRedis()
    s = SessionStore(r)
    s.set_cookies("x.com", {"sid": "abc"})
    assert s.get_cookies("x.com") == {"sid": "abc"}
    assert s.get_cookies("otro.com") == {}
