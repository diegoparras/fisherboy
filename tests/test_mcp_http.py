"""El MCP publicado por HTTP en /mcp (ADR-001 + la puerta de token).

Lo que se cuida acá:
  * la PUERTA: sin el API_TOKEN no se entra, y sin API_TOKEN configurado no entra nadie
    (fail-closed; un deploy olvidado no debe dejar el MCP abierto a internet);
  * que /mcp y /mcp/ sean lo mismo SIN redirect (no todo cliente sigue un 307 en un POST);
  * que el rol lo fije el operador con MCP_ROLE y que el caller no pueda escalar.
"""
from __future__ import annotations

import asyncio

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from app.main import create_app
from app.privacy_policy import get_policy
from app.security import auth

TOKEN = "token-mcp-de-prueba-0123456789"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
CABECERAS = {"Accept": "application/json, text/event-stream",
             "Content-Type": "application/json"}


@pytest.fixture
def app_mcp(fake_queue, monkeypatch):
    """La app con el MCP montado y el token activo."""
    monkeypatch.setattr(auth, "API_TOKEN", TOKEN)
    monkeypatch.setattr(auth, "API_TOKEN_ROLE", "angel")
    monkeypatch.delenv("MCP_HTTP", raising=False)
    app = create_app(make_settings(), queue=fake_queue)
    if not getattr(app.state, "mcp_http", False):
        pytest.skip("fastmcp no instalado: el MCP por HTTP es opcional")
    return app


@pytest.mark.parametrize("ruta", ["/mcp", "/mcp/"])
def test_sin_token_no_entra(app_mcp, ruta):
    with TestClient(app_mcp) as c:
        r = c.post(ruta, json=INITIALIZE, headers=CABECERAS)
    assert r.status_code == 401
    assert "API_TOKEN" in r.text


def test_token_equivocado_no_entra(app_mcp):
    with TestClient(app_mcp) as c:
        r = c.post("/mcp/", json=INITIALIZE,
                   headers=dict(CABECERAS, Authorization="Bearer otro-token"))
    assert r.status_code == 401


@pytest.mark.parametrize("llave", [{"Authorization": f"Bearer {TOKEN}"}, {"X-API-Key": TOKEN}])
def test_con_el_token_arranca_la_sesion(app_mcp, llave):
    """El handshake real: tiene que devolver un mcp-session-id, no solo un 200."""
    with TestClient(app_mcp) as c:
        r = c.post("/mcp/", json=INITIALIZE, headers=dict(CABECERAS, **llave))
    assert r.status_code == 200, r.text
    assert r.headers.get("mcp-session-id")


def test_sin_barra_no_redirige(app_mcp):
    """Starlette mandaría un 307 de /mcp a /mcp/; acá se atiende derecho."""
    with TestClient(app_mcp, follow_redirects=False) as c:
        r = c.post("/mcp", json=INITIALIZE,
                   headers=dict(CABECERAS, Authorization=f"Bearer {TOKEN}"))
    assert r.status_code == 200, f"dio {r.status_code} (307 = volvió el redirect)"
    assert r.headers.get("mcp-session-id")


def test_sin_api_token_configurado_nadie_entra(fake_queue, monkeypatch):
    """Fail-closed: con el token vacío la puerta no se abre ni con el header correcto."""
    monkeypatch.setattr(auth, "API_TOKEN", None)
    monkeypatch.delenv("MCP_HTTP", raising=False)
    app = create_app(make_settings(), queue=fake_queue)
    if not getattr(app.state, "mcp_http", False):
        pytest.skip("fastmcp no instalado")
    with TestClient(app) as c:
        r = c.post("/mcp/", json=INITIALIZE,
                   headers=dict(CABECERAS, Authorization=f"Bearer {TOKEN}"))
    assert r.status_code == 401


def test_apagado_con_mcp_http_0(fake_queue, monkeypatch):
    monkeypatch.setattr(auth, "API_TOKEN", TOKEN)
    monkeypatch.setenv("MCP_HTTP", "0")
    app = create_app(make_settings(), queue=fake_queue)
    assert app.state.mcp_http is False
    with TestClient(app) as c:
        r = c.post("/mcp/", json=INITIALIZE,
                   headers=dict(CABECERAS, Authorization=f"Bearer {TOKEN}"))
    assert r.status_code == 404


def test_el_rest_sigue_funcionando_con_el_mcp_montado(app_mcp):
    with TestClient(app_mcp) as c:
        assert c.get("/healthz").status_code == 200


# --- El rol: lo fija el operador, no el que llama -----------------------------------
def _llamar_tool(fake_queue, nombre, argumentos):
    """Llama una herramienta del servidor MCP por el transporte en memoria de fastmcp."""
    pytest.importorskip("fastmcp")
    from fastmcp import Client

    from app.mcp_server import build_server
    mcp = build_server(queue=fake_queue,
                       policy=get_policy(make_settings().privacy_matrix_path))

    async def _correr():
        async with Client(mcp) as cli:
            res = await cli.call_tool(nombre, argumentos)
            return res.data if hasattr(res, "data") else res

    return asyncio.run(_correr())


@pytest.mark.parametrize("techo", ["humano", "angel", "dios"])
def test_sin_rol_pedido_manda_mcp_role(fake_queue, monkeypatch, techo):
    """Sin `rol` en el pedido vale el techo. Antes el default del parámetro era 'humano'
    fijo, así que MCP_ROLE no servía para nada."""
    monkeypatch.setenv("MCP_ROLE", techo)
    datos = _llamar_tool(fake_queue, "submit_job", {"url": "https://example.com/una"})
    sobre = fake_queue.get(datos["job_id"])
    assert sobre is not None and sobre.rol.value == techo


def test_el_caller_no_puede_escalar(fake_queue, monkeypatch):
    monkeypatch.setenv("MCP_ROLE", "humano")
    datos = _llamar_tool(fake_queue, "submit_job",
                         {"url": "https://example.com/dos", "rol": "dios"})
    assert fake_queue.get(datos["job_id"]).rol.value == "humano"


def test_el_caller_si_puede_pedir_menos(fake_queue, monkeypatch):
    monkeypatch.setenv("MCP_ROLE", "dios")
    datos = _llamar_tool(fake_queue, "submit_job",
                         {"url": "https://example.com/tres", "rol": "humano"})
    assert fake_queue.get(datos["job_id"]).rol.value == "humano"


def test_un_rol_inventado_cae_al_techo(fake_queue, monkeypatch):
    monkeypatch.setenv("MCP_ROLE", "angel")
    datos = _llamar_tool(fake_queue, "submit_job",
                         {"url": "https://example.com/cuatro", "rol": "pirata"})
    assert fake_queue.get(datos["job_id"]).rol.value == "angel"
