"""Redes sociales (ADR-012): detección, extracción por forma y gating del job."""
from __future__ import annotations

import pytest

from app.net import social
from app.security import auth


@pytest.mark.parametrize("url,plat", [
    ("https://x.com/usuario", "x"),
    ("https://twitter.com/usuario/status/123", "x"),
    ("https://www.linkedin.com/in/alguien/", "linkedin"),
    ("https://mbasic.facebook.com/grupo", "facebook"),
    ("https://www.instagram.com/cuenta/", "instagram"),
    ("https://ejemplo.com/blog", None),
    ("", None),
    ("https://notx.com.evil.com/", None),      # no se cuela por substring
])
def test_deteccion_de_plataforma(url, plat):
    assert social.social_platform(url) == plat


def test_scroll_escala_con_los_posts_pedidos():
    pocas = social.scroll_actions(10)[0]["max_rounds"]
    muchas = social.scroll_actions(500)[0]["max_rounds"]
    assert pocas < muchas <= 60                    # crece, pero con tope
    assert social.scroll_actions(1)[0]["max_rounds"] >= 3


def test_scroll_tiene_pausa_anti_deteccion():
    """Bajar a toda velocidad es la forma más rápida de que te detecten."""
    assert social.scroll_actions(50)[0]["pause_s"] > 0


# ---------------------------------------------------------------------------
# X: el extractor busca por FORMA, no por ruta
# ---------------------------------------------------------------------------
def _tuit(tid="1750000000000000001", texto="hola mundo", **extra):
    legacy = {"full_text": texto, "created_at": "Wed Oct 10 20:19:24 +0000 2018",
              "favorite_count": 12, "reply_count": 3, "retweet_count": 5, "id_str": tid}
    legacy.update(extra)
    return {
        "rest_id": tid, "legacy": legacy, "views": {"count": "999"},
        "core": {"user_results": {"result": {"legacy": {
            "screen_name": "diego", "name": "Diego P."}}}},
    }


def _ep(payload):
    return [{"url": "https://x.com/i/api/graphql/abc/UserTweets", "json": payload}]


def test_extrae_tuit_completo():
    posts = social.extract_posts("x", _ep({"data": {"user": {"timeline": [_tuit()]}}}))
    assert len(posts) == 1
    p = posts[0]
    assert p["platform"] == "x"
    assert p["text"] == "hola mundo"
    assert p["author"] == "diego" and p["author_name"] == "Diego P."
    assert p["likes"] == 12 and p["replies"] == 3 and p["reposts"] == 5
    assert p["views"] == 999
    assert p["url"].endswith("/status/1750000000000000001")
    assert p["created_at"].startswith("2018-10-10T")     # normalizado a ISO


def test_sobrevive_a_que_cambien_la_ruta():
    """El punto del diseño: si X mueve el tuit de lugar, el extractor lo encuentra igual.
    Por eso se busca por forma (`full_text`) y no por una ruta fija."""
    hondo = {"data": {"otra": {"cosa": {"nueva": {"rara": [{"items": [_tuit()]}]}}}}}
    assert len(social.extract_posts("x", _ep(hondo))) == 1


def test_no_levanta_cualquier_cosa_con_text():
    """Hay miles de objetos con un campo `text` en esas respuestas; sin señales de tuit
    no se toman (si no, el resultado sería basura)."""
    ruido = {"data": [{"text": "soy un botón"}, {"text": "otro", "label": "x"}]}
    assert social.extract_posts("x", _ep(ruido)) == []


def test_deduplica_por_id():
    """El mismo tuit aparece repetido entre respuestas (timeline + hilo); va una sola vez."""
    eps = _ep({"a": [_tuit()]}) + _ep({"b": [_tuit()]})
    assert len(social.extract_posts("x", eps)) == 1


def test_respeta_el_tope_de_posts():
    muchos = [_tuit(tid=str(1750000000000000000 + i)) for i in range(50)]
    assert len(social.extract_posts("x", _ep({"d": muchos}), max_posts=10)) == 10


def test_saca_las_imagenes():
    t = _tuit(extended_entities={"media": [{"media_url_https": "https://pbs.twimg.com/a.jpg"}]})
    assert social.extract_posts("x", _ep({"d": [t]}))[0]["media"] == ["https://pbs.twimg.com/a.jpg"]


def test_tuit_sin_legacy_tambien_sale():
    """Formato aplanado (sin el envoltorio `legacy`): también tiene que reconocerse."""
    plano = {"rest_id": "999", "full_text": "plano", "created_at": "Wed Oct 10 20:19:24 +0000 2018",
             "favorite_count": 1, "screen_name": "diego"}
    assert social.extract_posts("x", _ep({"d": [plano]}))[0]["text"] == "plano"


def test_plataforma_sin_extractor_devuelve_vacio_sin_romper():
    assert social.extract_posts("linkedin", _ep({"d": [_tuit()]})) == []
    assert social.extract_posts("", _ep({})) == []


def test_json_corrupto_no_tumba_el_job():
    """Un cambio de formato no puede hacer fallar el job entero."""
    assert social.extract_posts("x", [{"url": "u", "json": None}]) == []
    assert social.extract_posts("x", []) == []


def test_needs_session():
    assert social.needs_session("x") and social.needs_session("linkedin")
    assert not social.needs_session("reddit")


def test_recorrido_tiene_presupuesto():
    """Sin tope, una respuesta enorme (o con ciclos) podría colgar al worker."""
    hondo = {"a": 1}
    for _ in range(200):
        hondo = {"n": [hondo]}
    assert social.extract_posts("x", _ep(hondo)) == []      # termina, no cuelga


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _as_role(monkeypatch, role, jti="jti-test"):
    monkeypatch.setattr(auth, "identity_from_request", lambda req: (role, jti if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_job_social_se_encola_y_detecta_plataforma(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    r = c.post("/api/jobs", json={"url": "https://x.com/usuario", "social": True,
                                  "max_posts": 50, "session": "mi-x"})
    assert r.status_code == 202
    d = c.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert d["meta"]["social"] is True
    assert d["meta"]["social_platform"] == "x"
    assert d["meta"]["max_posts"] == 50


def test_max_posts_tiene_tope(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    r = c.post("/api/jobs", json={"url": "https://x.com/u", "social": True, "max_posts": 99999})
    assert r.status_code == 422        # Pydantic lo corta antes de encolar


# ---------------------------------------------------------------------------
# Pipeline: que el branch arme el scroll, capture y entregue registros
# ---------------------------------------------------------------------------
def _deps_falsas(endpoints, capturado):
    from app.pipeline import PipelineDeps

    def _capture(url, tier_hint=None, **kw):
        capturado.update(kw, url=url)
        return endpoints

    return PipelineDeps(
        fetch=lambda *a, **k: None,
        extract=lambda h, u: "",
        anonymize_opaco=lambda t: (t.replace("Diego P.", "«PERSONA_1»"), 1),
        capture=_capture,
    )


def _sobre_social(**meta):
    from app.models import PrivacyMode, Rol, Sobre
    s = Sobre(job_id="s1", source_url="https://x.com/usuario",
              privacy_mode=meta.pop("privacy", PrivacyMode.DIRECTO), rol=Rol.DIOS)
    s.meta.update({"social": True, "max_posts": 30}, **meta)
    return s


def test_branch_social_entrega_registros():
    from app.pipeline import _social_branch
    cap = {}
    sobre = _social_branch(_sobre_social(), _deps_falsas(_ep({"d": [_tuit()]}), cap))
    assert sobre.status.value == "ok"
    assert sobre.meta["social_platform"] == "x"
    assert len(sobre.meta["records"]) == 1
    assert sobre.meta["records"][0]["author"] == "diego"
    assert sobre.content_json["posts"][0]["text"] == "hola mundo"


def test_branch_social_agrega_el_scroll():
    """Sin scroll, la timeline entrega solo la primera tanda."""
    from app.pipeline import _social_branch
    cap = {}
    _social_branch(_sobre_social(), _deps_falsas(_ep({"d": [_tuit()]}), cap))
    acts = cap["actions"]
    assert any(a["do"] == "scroll_until" for a in acts)


def test_branch_social_respeta_las_acciones_del_usuario():
    """Si el usuario puso un login a mano, corre ANTES del scroll."""
    from app.pipeline import _social_branch
    cap = {}
    sobre = _sobre_social(actions=[{"do": "click", "sel": "#entrar"}])
    _social_branch(sobre, _deps_falsas(_ep({"d": [_tuit()]}), cap))
    acts = cap["actions"]
    assert acts[0]["do"] == "click"                       # el del usuario primero
    assert acts[-1]["do"] == "scroll_until"


def test_branch_social_avisa_si_no_reconocio_nada():
    from app.pipeline import _social_branch
    sobre = _social_branch(_sobre_social(), _deps_falsas(_ep({"nada": 1}), {}))
    assert "social_note" in sobre.meta
    assert "sesion" in sobre.meta["social_note"].lower()


def test_branch_social_anonimiza_en_modo_opaco():
    """Los posts son datos de personas: en opaco NO puede salir nada crudo."""
    from app.models import PrivacyMode
    from app.pipeline import _social_branch
    sobre = _social_branch(_sobre_social(privacy=PrivacyMode.OPACO),
                           _deps_falsas(_ep({"d": [_tuit()]}), {}))
    assert sobre.anonimizado is True
    assert "Diego P." not in (sobre.content_md or "")
    assert sobre.content_json is None                      # ni el JSON crudo
    assert "records" not in sobre.meta                     # ni los registros
