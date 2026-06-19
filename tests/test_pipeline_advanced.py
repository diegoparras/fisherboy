"""Tests del pipeline avanzado: rama JSON (LLM) por modo, crawl y llms.txt."""
from __future__ import annotations

import json

from cryptography.fernet import Fernet

from app.fetchers.base import FetchResult
from app.models import OutputFormat, PrivacyMode, Rol, Sobre
from app.pipeline import PipelineDeps, process_job
from app.privacy.reversible import ReversibleAnonymizer, ReversibleStore
from app.privacy_policy import load_policy

from pathlib import Path

MATRIX = Path(__file__).resolve().parent.parent / "privacy_matrix.yaml"


def _sobre(**kw) -> Sobre:
    base = dict(job_id="t", source_url="https://1.1.1.1/a",
                privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    base.update(kw)
    return Sobre(**base)


def _html(text: str) -> FetchResult:
    return FetchResult(url="https://1.1.1.1/a", status_code=200, content=text.encode(),
                       text=text, content_type="text/html", tier=0)


def _base_deps(**over) -> PipelineDeps:
    d = dict(
        fetch=lambda url, tier_hint=None: _html("contenido"),
        extract=lambda html, url: html,
        anonymize_opaco=lambda md: (md, 0),
    )
    d.update(over)
    return PipelineDeps(**d)


class _FakeAnon:
    def detect_spans(self, text):
        return []


def _reversible():
    store = ReversibleStore(Fernet(Fernet.generate_key()))
    return ReversibleAnonymizer(_FakeAnon(), store, policy=load_policy(str(MATRIX)))


# --------------------------------------------------------------------------- JSON branch
def test_json_directo():
    sobre = _sobre(output_format=OutputFormat.JSON, privacy_mode=PrivacyMode.DIRECTO)
    sobre.meta["extract_schema"] = {"type": "object"}
    deps = _base_deps(llm_complete=lambda s, u: '{"ok": true}')
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.content_json == {"ok": True}
    assert out.anonimizado is False   # directo no anonimiza


def test_json_opaco_anonymizes_before_llm():
    sobre = _sobre(output_format=OutputFormat.JSON, privacy_mode=PrivacyMode.OPACO)
    sobre.meta["extract_schema"] = {"type": "object"}
    seen = {}

    def fake_llm(system, user):
        seen["user"] = user
        return '{"got": "x"}'

    deps = _base_deps(
        fetch=lambda url, tier_hint=None: _html("Mail juan@x.com"),
        anonymize_opaco=lambda md: ("Mail «EMAIL_1»", 1),
        llm_complete=fake_llm,
    )
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.anonimizado is True
    assert "juan@x.com" not in seen["user"]   # el LLM nunca vio la PII real
    assert "«EMAIL_1»" in seen["user"]


def test_json_reversible_roundtrips_output():
    sobre = _sobre(output_format=OutputFormat.JSON, privacy_mode=PrivacyMode.REVERSIBLE, rol=Rol.ANGEL)
    sobre.meta["extract_schema"] = {"type": "object"}

    # El LLM "extrae" el contacto, que viene pseudonimizado como «EMAIL_1».
    deps = _base_deps(
        fetch=lambda url, tier_hint=None: _html("Contacto: juan@x.com"),
        extract=lambda html, url: "Contacto: juan@x.com",
        llm_complete=lambda s, u: '{"contacto": "«EMAIL_1»"}',
        reversible=_reversible(),
    )
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    # La salida se re-hidrata local: el valor real vuelve, el LLM nunca lo vio.
    assert out.content_json["contacto"] == "juan@x.com"


def test_json_without_llm_errors():
    sobre = _sobre(output_format=OutputFormat.JSON)
    sobre.meta["extract_schema"] = {"type": "object"}
    out = process_job(sobre, _base_deps())   # sin llm_complete
    assert out.status.value == "error"
    assert "LLM" in out.error


# --------------------------------------------------------------------------- crawl + formats
def test_crawl_bundles_pages():
    pages = [
        FetchResult(url="https://x.com/1", status_code=200, content=b"uno", text="uno",
                    content_type="text/html", tier=0),
        FetchResult(url="https://x.com/2", status_code=200, content=b"dos", text="dos",
                    content_type="text/html", tier=0),
    ]
    sobre = _sobre(source_url="https://x.com/1")
    sobre.meta["crawl_depth"] = 1
    sobre.meta["max_pages"] = 5
    deps = _base_deps(crawl=lambda seed, **kw: pages)
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.meta["paginas"] == 2
    assert "## https://x.com/1" in out.content_md
    assert "## https://x.com/2" in out.content_md


def test_directo_markdown_is_raw():
    # directo → el markdown sale CRUDO, sin pasar por Anonimal.
    pii = "Mail juan@x.com y CUIT 20-12345678-9"
    called = {"anon": False}

    def anon(md):
        called["anon"] = True
        return ("NO debería llamarse", 9)

    sobre = _sobre(privacy_mode=PrivacyMode.DIRECTO)
    deps = _base_deps(
        fetch=lambda url, tier_hint=None: _html(pii),
        extract=lambda html, url: pii,
        anonymize_opaco=anon,
    )
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.anonimizado is False
    assert out.content_md == pii          # crudo, intacto
    assert called["anon"] is False        # Anonimal NO se invocó


def test_opaco_markdown_is_masked():
    pii = "Mail juan@x.com"
    sobre = _sobre(privacy_mode=PrivacyMode.OPACO)
    deps = _base_deps(
        fetch=lambda url, tier_hint=None: _html(pii),
        extract=lambda html, url: pii,
        anonymize_opaco=lambda md: ("Mail «EMAIL_1»", 1),
    )
    out = process_job(sobre, deps)
    assert out.anonimizado is True
    assert out.content_md == "Mail «EMAIL_1»"
    assert "juan@x.com" not in out.content_md


def test_llms_txt_output_wraps_header():
    sobre = _sobre(output_format=OutputFormat.LLMS_TXT)
    deps = _base_deps(
        fetch=lambda url, tier_hint=None: _html("# Mi Doc\n\ncuerpo"),
        extract=lambda html, url: "# Mi Doc\n\ncuerpo",
        anonymize_opaco=lambda md: (md, 0),
    )
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.content_md.startswith("# Mi Doc")
    assert "Fuente:" in out.content_md


def test_capture_api_branch_directo():
    eps = [{"url": "https://api.x.com/items?p=1", "status": 200, "content_type": "application/json",
            "bytes": 1234, "json": {"items": [{"id": 1}, {"id": 2}]}}]
    sobre = _sobre(privacy_mode=PrivacyMode.DIRECTO)
    sobre.meta["capture_api"] = True
    deps = _base_deps(capture=lambda u, tier_hint=None, **kw: eps)
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.content_json["endpoints"][0]["json"]["items"][0]["id"] == 1
    assert out.meta["api_endpoints"] == 1
    assert out.anonimizado is False


def test_capture_api_empty_errors():
    sobre = _sobre(privacy_mode=PrivacyMode.DIRECTO)
    sobre.meta["capture_api"] = True
    deps = _base_deps(capture=lambda u, tier_hint=None, **kw: [])
    out = process_job(sobre, deps)
    assert out.status.value == "error"
    assert "endpoint" in out.error.lower()


def test_tarantula_builds_data_tree():
    pages = {
        "https://x.com/": ("<a href='/a'>a</a>",
                           [{"url": "https://api.x.com/home", "status": 200, "bytes": 100, "json": {"items": [1, 2]}}]),
        "https://x.com/a": ("hoja", [{"url": "https://api.x.com/a", "status": 200, "bytes": 50, "json": {"x": 1}}]),
    }
    sobre = _sobre(source_url="https://x.com/", privacy_mode=PrivacyMode.DIRECTO)
    sobre.meta.update({"tarantula": True, "crawl_depth": 1, "max_pages": 5})
    deps = _base_deps(capture_page=lambda u, **kw: pages.get(u, ("", [])))
    out = process_job(sobre, deps)
    assert out.status.value == "ok"
    assert out.meta["nodos"] == 2
    t = out.content_json["tree"]
    assert t["url"] == "https://x.com/"
    assert t["children"][0]["url"] == "https://x.com/a"
    assert t["endpoints"][0]["json"]["items"] == [1, 2]      # dato real en el nodo
    # el árbol de display (meta) NO lleva cuerpos
    assert out.meta["tree"]["endpoints_count"] == 1
    assert "json" not in json.dumps(out.meta["tree"])
