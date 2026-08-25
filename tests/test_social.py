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


# ---------------------------------------------------------------------------
# LinkedIn (Voyager)
# ---------------------------------------------------------------------------
def _li_update(pid="7123456789012345678", texto="Buscamos backend senior"):
    return {
        "$type": "com.linkedin.voyager.feed.render.UpdateV2",
        "entityUrn": "urn:li:activity:" + pid,
        "commentary": {"text": {"text": texto}},
        "actor": {
            "name": {"text": "Ana Perez"},
            "navigationContext": {"actionTarget": "https://www.linkedin.com/in/ana-perez/"},
        },
        "socialDetail": {"totalSocialActivityCounts": {
            "numLikes": 42, "numComments": 7, "numShares": 3}},
    }


def test_linkedin_extrae_post():
    posts = social.extract_posts("linkedin", _ep({"elements": [_li_update()]}))
    assert len(posts) == 1
    p = posts[0]
    assert p["platform"] == "linkedin"
    assert p["text"] == "Buscamos backend senior"
    assert p["author_name"] == "Ana Perez"
    assert p["author"] == "ana-perez"                    # sale del link al perfil
    assert p["likes"] == 42 and p["replies"] == 7 and p["reposts"] == 3
    assert p["id"] == "7123456789012345678"
    assert "urn:li:activity:7123456789012345678" in p["url"]


def test_linkedin_sobrevive_a_ruta_cambiada():
    hondo = {"data": {"x": {"y": [{"z": {"elements": [_li_update()]}}]}}}
    assert len(social.extract_posts("linkedin", _ep(hondo))) == 1


def test_linkedin_acepta_commentaryV2_sin_type():
    """Algunas respuestas vienen sin `$type`; ahí ancla en commentary + actor."""
    nodo = {"commentaryV2": {"text": {"text": "sin type"}},
            "urn": "urn:li:share:999", "actor": {"name": {"text": "Beto"}}}
    p = social.extract_posts("linkedin", _ep({"e": [nodo]}))[0]
    assert p["text"] == "sin type" and p["id"] == "999"


def test_linkedin_ignora_lo_que_no_es_post():
    ruido = {"elements": [{"$type": "com.linkedin.voyager.common.Image", "url": "x.jpg"},
                          {"text": "un boton"}]}
    assert social.extract_posts("linkedin", _ep(ruido)) == []


def test_linkedin_deduplica():
    eps = _ep({"a": [_li_update()]}) + _ep({"b": [_li_update()]})
    assert len(social.extract_posts("linkedin", eps)) == 1


# ---------------------------------------------------------------------------
# Facebook (GraphQL + mbasic)
# ---------------------------------------------------------------------------
def _fb_story(pid="10160000000000000", texto="Hola a todos"):
    return {
        "__typename": "Story", "post_id": pid,
        "message": {"text": texto},
        "creation_time": 1739000000,
        "url": "https://www.facebook.com/story.php?story_fbid=" + pid,
        "actors": [{"id": "100001", "name": "Carlos Gomez"}],
        "feedback": {"reaction_count": {"count": 15}},
    }


def test_facebook_extrae_de_graphql():
    posts = social.extract_posts("facebook", _ep({"data": {"node": _fb_story()}}))
    assert len(posts) == 1
    p = posts[0]
    assert p["platform"] == "facebook"
    assert p["text"] == "Hola a todos"
    assert p["author_name"] == "Carlos Gomez"
    assert p["likes"] == 15
    assert p["created_at"].startswith("2025-")            # epoch → ISO


def test_facebook_ignora_lo_que_no_es_story():
    ruido = {"data": [{"__typename": "Comment", "message": {"text": "un comentario"}}]}
    assert social.extract_posts("facebook", _ep(ruido)) == []


FB_MBASIC = """<html><body>
<div data-ft='{"top_level_post_id":"555"}'>
  <h3><a href="/carlos">Carlos Gomez</a></h3>
  <p>Post desde mbasic</p>
  <a href="/story.php?story_fbid=555&amp;id=100001">Me gusta</a>
</div>
<div data-ft='{"x":1}'><h3><a href="/ana">Ana</a></h3><p>Otro post</p>
  <a href="/story.php?story_fbid=666&amp;id=2">Comentar</a></div>
</body></html>"""


def test_facebook_cae_al_html_de_mbasic():
    """Sin JSON (mbasic no corre GraphQL), el extractor lee el HTML plano."""
    posts = social.extract_posts("facebook", [], html=FB_MBASIC)
    assert len(posts) == 2
    assert posts[0]["id"] == "555"
    assert "Post desde mbasic" in posts[0]["text"]
    assert posts[0]["author_name"] == "Carlos Gomez"


def test_facebook_prefiere_el_json_sobre_el_html():
    """Si hay JSON, ese manda: es más confiable que parsear HTML."""
    posts = social.extract_posts("facebook", _ep({"n": _fb_story()}), html=FB_MBASIC)
    assert posts[0]["text"] == "Hola a todos"            # el del JSON, primero


def test_facebook_html_roto_no_rompe():
    assert social.extract_posts("facebook", [], html="<<<no soy html>>>") == []


def test_prefer_url_manda_a_mbasic():
    assert social.prefer_url("https://www.facebook.com/grupo/123") \
        == "https://mbasic.facebook.com/grupo/123"
    assert social.prefer_url("https://mbasic.facebook.com/x") == "https://mbasic.facebook.com/x"
    assert social.prefer_url("https://x.com/user") == "https://x.com/user"   # otras redes no


def test_las_tres_redes_estan_soportadas():
    assert social.supported() == ["facebook", "linkedin", "x"]


# ---------------------------------------------------------------------------
# Extractor generico por anclas (ADR-014): la base de la auto-reparacion
# ---------------------------------------------------------------------------
ANCLAS_X = {"texto": "full_text", "autor": "screen_name", "autor_nombre": "name",
            "id": "id_str", "fecha": "created_at", "likes": "favorite_count"}


def test_anclas_extraen_igual_que_el_extractor_propio():
    posts = social.extract_with_anchors(_ep({"d": [_tuit()]}), ANCLAS_X, platform="x")
    assert len(posts) == 1
    assert posts[0]["text"] == "hola mundo"
    assert posts[0]["author"] == "diego" and posts[0]["likes"] == 12


def test_anclas_sin_texto_no_extraen_nada():
    """`texto` es obligatorio: sin el no hay forma de saber que objeto es un post."""
    assert social.extract_with_anchors(_ep({"d": [_tuit()]}), {"autor": "screen_name"}) == []
    assert social.extract_with_anchors(_ep({"d": [_tuit()]}), {}) == []


def test_anclas_inventadas_no_sacan_nada():
    """Si el modelo alucina nombres de campo, el resultado es vacio — nunca datos falsos."""
    assert social.extract_with_anchors(_ep({"d": [_tuit()]}),
                                       {"texto": "campo_que_no_existe"}) == []


def test_anclas_respetan_el_tope():
    muchos = [_tuit(tid=str(1750000000000000000 + i), texto=f"post {i}") for i in range(40)]
    assert len(social.extract_with_anchors(_ep({"d": muchos}), ANCLAS_X, max_posts=5)) == 5


# ---------------------------------------------------------------------------
# Reparacion con IA: la IA dice DONDE, nunca QUE
# ---------------------------------------------------------------------------
def test_la_muestra_no_manda_la_respuesta_entera():
    """Esas APIs devuelven megas: mandarlas enteras seria carisimo y encima peor."""
    from app.net import social_ai
    gigante = {"basura": ["x" * 500 for _ in range(500)], "d": [_tuit()]}
    m = social_ai.muestra(_ep(gigante), max_chars=6000)
    assert 0 < len(m) <= 6000


def test_ia_descubre_y_valida_las_anclas():
    from app.net import social_ai

    def modelo_falso(sistema, pedido):
        return '```json\n{"texto": "full_text", "autor": "screen_name", "id": "id_str"}\n```'

    anclas, posts = social_ai.descubrir(_ep({"d": [_tuit()]}), "x", modelo_falso)
    assert anclas["texto"] == "full_text"
    assert len(posts) == 1 and posts[0]["text"] == "hola mundo"


def test_ia_que_alucina_campos_se_descarta():
    """La validacion es la red de seguridad: anclas que no sacan nada NO se aceptan."""
    from app.net import social_ai

    def modelo_mentiroso(sistema, pedido):
        return '{"texto": "campo_inventado", "autor": "otro_invento"}'

    anclas, posts = social_ai.descubrir(_ep({"d": [_tuit()]}), "x", modelo_mentiroso)
    assert anclas == {} and posts == []


def test_ia_no_puede_inventar_contenido():
    """Aunque el modelo DEVUELVA posts en vez de nombres de campo, se ignoran: solo se
    aceptan nombres, y los valores salen siempre del JSON real."""
    from app.net import social_ai

    def modelo_que_inventa_datos(sistema, pedido):
        return '{"texto": "Este post no existe en la respuesta real", "autor": "fantasma"}'

    anclas, posts = social_ai.descubrir(_ep({"d": [_tuit()]}), "x", modelo_que_inventa_datos)
    assert posts == []                                    # no se colo nada inventado
    assert all("fantasma" not in str(p) for p in posts)


def test_ia_caida_no_rompe_el_job():
    from app.net import social_ai

    def modelo_roto(sistema, pedido):
        raise RuntimeError("sin credito")

    assert social_ai.descubrir(_ep({"d": [_tuit()]}), "x", modelo_roto) == ({}, [])


def test_respuesta_no_json_se_ignora():
    from app.net import social_ai
    assert social_ai.descubrir(_ep({"d": [_tuit()]}), "x",
                               lambda s, u: "perdon, no entendi") == ({}, [])


def test_anclas_se_guardan_y_se_releen(fake_redis):
    from app.net import social_ai
    assert social_ai.guardar_anclas(fake_redis, "x", ANCLAS_X) is True
    assert social_ai.anclas_guardadas(fake_redis, "x")["texto"] == "full_text"
    assert social_ai.anclas_guardadas(fake_redis, "linkedin") == {}      # por plataforma
    assert social_ai.anclas_guardadas(None, "x") == {}                   # sin redis, sin drama


# ---------------------------------------------------------------------------
# La cascada completa: que se repare sola cuando la plataforma cambia
# ---------------------------------------------------------------------------
def _formato_nuevo(tid="999", texto="X cambio todo"):
    """Un tuit con nombres de campo DISTINTOS: el extractor propio no lo reconoce."""
    return {"post_id": tid, "cuerpo": texto, "handle": "diego", "me_gusta": 7}


def test_cascada_ia_repara_y_aprende(fake_redis):
    """El escenario que importa: la plataforma cambia, el extractor de siempre no saca nada,
    la IA descubre donde quedaron los campos, y eso QUEDA APRENDIDO para la proxima."""
    from app.net import social_ai
    from app.pipeline import _social_branch

    llamadas = {"n": 0}

    def modelo(sistema, pedido):
        llamadas["n"] += 1
        return '{"texto": "cuerpo", "autor": "handle", "id": "post_id", "likes": "me_gusta"}'

    eps = _ep({"data": [_formato_nuevo()]})
    deps = _deps_falsas(eps, {})
    deps.llm_complete = modelo
    deps.redis = fake_redis

    sobre = _social_branch(_sobre_social(), deps)
    assert len(sobre.meta["records"]) == 1
    assert sobre.meta["records"][0]["text"] == "X cambio todo"
    assert sobre.meta["records"][0]["author"] == "diego"
    assert sobre.meta["social_reparado_por_ia"] is True
    assert llamadas["n"] == 1

    # Segunda vuelta: ya aprendido → NO se vuelve a llamar al modelo (gratis).
    sobre2 = _social_branch(_sobre_social(), _con(deps, fake_redis, modelo))
    assert len(sobre2.meta["records"]) == 1
    assert llamadas["n"] == 1, "la segunda vez no debe gastar IA"
    assert "social_reparado_por_ia" not in sobre2.meta


def _con(deps, redis, modelo):
    """Mismas deps, para la segunda pasada."""
    deps.redis, deps.llm_complete = redis, modelo
    return deps


def test_sin_ia_configurada_no_rompe(fake_redis):
    """La IA es opcional: sin LLM el job termina igual, avisando que no reconocio nada."""
    from app.pipeline import _social_branch
    deps = _deps_falsas(_ep({"data": [_formato_nuevo()]}), {})
    deps.redis, deps.llm_complete = fake_redis, None
    sobre = _social_branch(_sobre_social(), deps)
    assert "social_note" in sobre.meta
    assert "records" not in sobre.meta


def test_la_ia_no_se_llama_si_el_extractor_anduvo(fake_redis):
    """Lo determinista primero: si saco posts, no se gasta un centavo."""
    from app.pipeline import _social_branch
    llamadas = {"n": 0}

    def modelo(s, u):
        llamadas["n"] += 1
        return "{}"

    deps = _deps_falsas(_ep({"d": [_tuit()]}), {})
    deps.redis, deps.llm_complete = fake_redis, modelo
    sobre = _social_branch(_sobre_social(), deps)
    assert len(sobre.meta["records"]) == 1
    assert llamadas["n"] == 0


def test_depuracion_guarda_una_muestra():
    """Sin esto, un '0 publicaciones' es un callejon sin salida."""
    from app.pipeline import _social_branch
    sobre = _social_branch(_sobre_social(), _deps_falsas(_ep({"data": [_formato_nuevo()]}), {}))
    assert sobre.meta.get("social_muestra")
    assert "cuerpo" in sobre.meta["social_muestra"]      # se ve el nombre del campo real
    assert sobre.meta.get("social_urls")
