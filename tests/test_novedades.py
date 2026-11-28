"""Novedades por versión: salen del CHANGELOG.md (una sola fuente) y se muestran en el cartel de
arranque de cada proceso (registros de EasyPanel), en la UI al entrar después de una
actualización y en el Release de GitHub que arma el CI."""
from __future__ import annotations

import tomllib
from pathlib import Path

from app import __version__
from app import novedades as nov
from app.security import auth

REPO = Path(__file__).resolve().parent.parent

MUESTRA = """# Changelog

Formato basado en Keep a Changelog.

## [0.3.0] - 2026-11-21

### Nuevo
- **Novedades** en la UI: se ven al entrar
  después de una actualización (`GET /api/novedades`).

### Corregido
- Un arreglo corto.

## [0.2.1] - 2026-11-20

### Corregido
- Otro arreglo.

## [0.2.0] - 2026-11-19

### Nuevo
- Algo grande.
"""


def test_parsea_versiones_fechas_secciones_e_items_multilinea():
    v = nov.parsear(MUESTRA)
    assert [x.version for x in v] == ["0.3.0", "0.2.1", "0.2.0"]
    assert v[0].fecha == "2026-11-21"
    assert [s.titulo for s in v[0].secciones] == ["Nuevo", "Corregido"]
    assert v[0].secciones[0].items == [
        "**Novedades** en la UI: se ven al entrar después de una actualización (`GET /api/novedades`)."
    ]


def test_parsear_tolera_texto_vacio_y_secciones_sin_items():
    assert nov.parsear("") == []
    assert nov.parsear(None) == []
    v = nov.parsear("## [1.0.0]\n\n### Nuevo\n\n### Corregido\n- algo\n")
    assert v[0].fecha is None
    assert [s.titulo for s in v[0].secciones] == ["Corregido"], "las secciones vacías no cuentan"


def test_desde_la_ultima_vista_hasta_la_actual():
    v = nov.parsear(MUESTRA)
    assert [x.version for x in nov.desde(v, "0.2.0", "0.3.0")] == ["0.3.0", "0.2.1"]
    assert nov.desde(v, "0.3.0", "0.3.0") == [], "ya vista: nada que mostrar"
    assert [x.version for x in nov.desde(v, None, "0.3.0")] == ["0.3.0"], "primera vez: solo la actual"
    assert [x.version for x in nov.desde(v, "0.2.0", "0.2.1")] == ["0.2.1"], "nunca posteriores a la que corre"
    assert nov.desde(v, "0.10.0", "0.3.0") == [], "compara como versión, no como texto"


def test_texto_plano_para_el_log_y_markdown_para_el_release():
    v = nov.parsear(MUESTRA)[0]
    assert nov.lineas_log(v) == [
        "Nuevo: Novedades en la UI: se ven al entrar después de una actualización (GET /api/novedades).",
        "Corregido: Un arreglo corto.",
    ]
    md = nov.notas_markdown(v)
    assert md.startswith("### Nuevo\n- **Novedades** en la UI")
    assert "### Corregido\n- Un arreglo corto." in md


def test_cartel_de_arranque_version_proceso_y_que_cambia_cortado_al_ancho():
    v = nov.parsear(MUESTRA)
    lineas = nov.cartel_arranque("0.3.0", v, proceso="API, modo sidekick", ancho=60)
    assert all(len(x) <= 60 for x in lineas), "ningún renglón pasa el ancho"
    assert set(lineas[0]) == {"="}
    assert lineas[1] == "FISHERBOY v0.3.0  (2026-11-21)"
    assert lineas[2] == "Proceso: API, modo sidekick"
    assert lineas[3] == "Que cambia en esta version:"
    assert lineas[4] == "- Nuevo: Novedades en la UI: se ven al entrar después de una"
    assert lineas[5].startswith("    actualización"), "la continuación queda sangrada"
    assert set(lineas[-1]) == {"="}
    assert not any("**" in x or "`" in x for x in lineas), "sin markdown en el log"


def test_cartel_sin_notas_para_la_version():
    sin = nov.cartel_arranque("9.9.9", nov.parsear(MUESTRA), proceso="worker")
    assert sin[1] == "FISHERBOY v9.9.9"
    assert sin[2] == "Proceso: worker"
    assert sin[3] == "Sin notas para esta version en el CHANGELOG."


def test_imprimir_cartel_es_la_salida_con_prefijo_y_no_rompe_sin_changelog(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(nov, "CHANGELOG", tmp_path / "no-existe.md")
    nov.imprimir_cartel("worker")
    out = capsys.readouterr().out.splitlines()
    assert out and all(x.startswith("[fisherboy.worker] ") for x in out)
    assert out[1] == f"[fisherboy.worker] FISHERBOY v{__version__}"
    assert out[2].startswith("[fisherboy.worker] Proceso: worker")
    assert "Sin notas para esta version en el CHANGELOG." in out[3]


def test_imprimir_cartel_nunca_tira(monkeypatch, capsys):
    def explota(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(nov, "cartel_arranque", explota)
    nov.imprimir_cartel("api")   # no levanta: el arranque sigue
    assert capsys.readouterr().out == ""


def test_cli_notas_de_la_version_actual(monkeypatch, capsys, tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(f"## [{__version__}] - 2026-11-20\n\n### Nuevo\n- **Algo** nuevo.\n", encoding="utf-8")
    monkeypatch.setattr(nov, "CHANGELOG", cl)
    assert nov.main(["--version"]) == 0
    assert capsys.readouterr().out == __version__
    assert nov.main([]) == 0
    assert capsys.readouterr().out == "### Nuevo\n- **Algo** nuevo.\n"
    cl.write_text("## [0.0.1] - 2020-01-01\n\n### Nuevo\n- viejo\n", encoding="utf-8")
    assert nov.main([]) == 1, "sin la sección de la versión el CI no publica"


def test_el_changelog_real_tiene_la_version_actual_primero():
    v = nov.parsear((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    actual = next((x for x in v if x.version == __version__), None)
    assert actual is not None, f'falta "## [{__version__}]" en CHANGELOG.md'
    assert any(s.items for s in actual.secciones), f"la sección {__version__} del CHANGELOG está vacía"
    assert v[0].version == __version__, "la versión de la app es la primera del CHANGELOG"


def test_la_version_de_pyproject_coincide_con_la_de_la_app():
    py = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert py["project"]["version"] == __version__


def test_dockerfile_lleva_el_changelog_a_la_imagen():
    assert "COPY CHANGELOG.md" in (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "!CHANGELOG.md" in (REPO / ".dockerignore").read_text(encoding="utf-8")


# --- API: GET /api/novedades (misma auth que el resto) -------------------------------

def _con_changelog(monkeypatch, tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(
        "## [99.0.0] - 2030-01-01\n\n### Nuevo\n- del futuro\n\n"
        f"## [{__version__}] - 2026-11-20\n\n### Nuevo\n- **la actual**\n\n"
        "## [0.0.2] - 2020-01-02\n\n### Corregido\n- vieja 2\n\n"
        "## [0.0.1] - 2020-01-01\n\n### Nuevo\n- vieja 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nov, "CHANGELOG", cl)


def test_api_novedades_requiere_sesion(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "role_from_request", lambda req: None)
    assert client_factory().get("/api/novedades").status_code == 401


def test_api_novedades_desde_la_ultima_vista(client_factory, monkeypatch, tmp_path):
    _con_changelog(monkeypatch, tmp_path)
    c = client_factory()
    d = c.get("/api/novedades").json()
    assert d["version"] == __version__
    assert [x["version"] for x in d["versiones"]] == [__version__], "primera vez: solo la actual"
    assert d["versiones"][0]["secciones"] == [{"titulo": "Nuevo", "items": ["**la actual**"]}]
    d = c.get("/api/novedades", params={"desde": "0.0.1"}).json()
    assert [x["version"] for x in d["versiones"]] == [__version__, "0.0.2"]
    d = c.get("/api/novedades", params={"desde": __version__}).json()
    assert d["versiones"] == []
    d = c.get("/api/novedades", params={"desde": "<script>"}).json()
    assert [x["version"] for x in d["versiones"]] == [__version__], "desde inválido: se ignora"
    d = c.get("/api/novedades", params={"todas": "1"}).json()
    assert [x["version"] for x in d["versiones"]] == [__version__, "0.0.2", "0.0.1"], "nunca las futuras"


def test_ui_tiene_novedades_en_el_menu_y_en_acerca_de(client_factory):
    html = client_factory(APP_MODE="standalone").get("/").text
    assert 'id="newsBtn"' in html, "ítem Novedades en el menú kebab"
    assert 'id="about-news"' in html, "enlace Qué cambió en Acerca de"
    assert 'id="novModal"' in html, "modal Novedades"
    assert "/api/novedades" in html
