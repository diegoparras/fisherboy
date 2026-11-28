"""La receta que está detrás de la pantalla.

Muchos portales arman sus tablas con consultas AJAX que devuelven JSON. Mientras una sesión
de agente recorre la pantalla (una persona, la IA o una receta de clics), la página hace esas
consultas. Con lo que vio, este módulo propone una receta de `fetch` que baja lo mismo sin
tocar calendarios ni botones: más rápida y mucho menos frágil que la de pantalla.

Es una propuesta para revisar, no una receta que se corre sola:

- empieza con un `goto` a la página desde donde salieron las consultas (el `fetch` corre
  adentro de la página, con su sesión, su Origin y su Referer);
- un valor de la query que es una variable de la sesión va como `{{var:nombre}}`, nunca
  literal: la receta sugerida no puede llevarse un CUIT o un rango de fechas puestos a mano;
- un valor que salió de una respuesta anterior (el id de una consulta) va como
  `{{res:paso_N.ruta}}`, y ese paso lleva `guardar`;
- un POST queda en `omitidos`: `fetch` solo hace GET.

Los valores muy cortos ("1", "E") no se toman por ids ni por variables: aparecen en cualquier
JSON, y encadenarlos sería inventar una relación que no existe.
"""
from __future__ import annotations

from urllib.parse import unquote_plus, urlsplit

MIN_LARGO_VARIABLE = 3
MIN_LARGO_ID = 4


def _buscar(obj, valor: str, ruta: tuple = ()) -> tuple | None:
    """Ruta al primer escalar de `obj` que vale `valor` (recorrido en orden)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            r = _buscar(v, valor, (*ruta, str(k)))
            if r is not None:
                return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = _buscar(v, valor, (*ruta, str(i)))
            if r is not None:
                return r
    elif obj is not None and not isinstance(obj, bool) and str(obj) == valor:
        return ruta
    return None


def _relativa(url: str, pagina: str) -> str:
    """Lo más corto que, resuelto contra la página, vuelve a dar `url`."""
    u, p = urlsplit(url), urlsplit(pagina)
    if (u.scheme, u.netloc) != (p.scheme, p.netloc):
        return url
    query = f"?{u.query}" if u.query else ""
    carpeta_u, _, archivo = u.path.rpartition("/")
    carpeta_p = p.path.rpartition("/")[0]
    if carpeta_u == carpeta_p and archivo:
        return archivo + query
    return u.path + query


def _plantilla_query(query: str, variables: dict, previas: list[tuple[int, object]],
                     guardar: set[int], notas: list[str]) -> str:
    partes = []
    for parte in query.split("&") if query else []:
        clave, igual, crudo = parte.partition("=")
        valor = unquote_plus(crudo)
        nuevo = None
        # Si dos variables valen lo mismo (el CUIT de login y el representado), gana la que se
        # llama como el parámetro: es la que quien escribió la receta quiso decir.
        candidatas = sorted(variables.items(), key=lambda kv: kv[0] != unquote_plus(clave))
        for nombre, v in candidatas:
            if len(str(v)) >= MIN_LARGO_VARIABLE and valor == str(v):
                nuevo = f"{{{{var:{nombre}}}}}"
                if crudo != valor:
                    nota = (f"La variable `{nombre}` va en una URL: mandala ya codificada "
                            "(como hace encodeURIComponent), porque `{{var:...}}` no se codifica.")
                    if nota not in notas:
                        notas.append(nota)
                break
        if nuevo is None and len(valor) >= MIN_LARGO_ID:
            for n, datos in reversed(previas):   # la respuesta más cercana primero
                ruta = _buscar(datos, valor)
                if ruta is not None:
                    guardar.add(n)
                    nuevo = "{{res:" + ".".join((f"paso_{n}", *ruta)) + "}}"
                    break
        partes.append(f"{clave}{igual}{nuevo}" if nuevo is not None else parte)
    return "&".join(partes)


def _plantilla(url: str, variables: dict, previas, guardar, notas) -> str:
    base, signo, query = url.partition("?")
    if not signo:
        return url
    return f"{base}?{_plantilla_query(query, variables, previas, guardar, notas)}"


def sugerir_receta(intercambios: list[dict], variables: dict) -> dict:
    """De las consultas JSON que vio la sesión, una receta de `fetch` que baja lo mismo.

    Cada intercambio es `{"metodo", "url", "status", "json", "pagina"}`, en el orden en que
    llegaron. Devuelve `{"receta": [...], "omitidos": [{"url", "motivo"}], "notas": [...]}`.
    """
    receta: list[dict] = []
    omitidos: list[dict] = []
    notas: list[str] = []
    previas: list[tuple[int, object]] = []
    guardar: set[int] = set()
    vistos: set[tuple[str, str]] = set()
    pagina = ""
    for x in intercambios:
        metodo, url = str(x.get("metodo") or "GET").upper(), str(x.get("url") or "")
        if not url or (metodo, url) in vistos:
            continue
        vistos.add((metodo, url))
        if metodo != "GET":
            omitidos.append({"url": _plantilla(url, variables, [], set(), []),
                             "motivo": "fetch solo hace GET"})
            continue
        if not receta:
            pagina = str(x.get("pagina") or url)
            receta.append({"do": "goto", "url": _plantilla(pagina, variables, [], set(), notas)})
        n = len(previas) + 1
        relativa = _relativa(url, pagina)
        receta.append({"do": "fetch", "url": _plantilla(relativa, variables, previas, guardar, notas),
                       "name": f"respuesta-{n}.json"})
        previas.append((n, x.get("json")))
    # `guardar` se decide después: un paso se guarda porque uno POSTERIOR usa su respuesta.
    fetches = [p for p in receta if p["do"] == "fetch"]
    for n in sorted(guardar):
        fetches[n - 1]["guardar"] = f"paso_{n}"
    if not receta and not omitidos:
        notas.append("La sesión no vio consultas JSON de la página: esta pantalla no se arma con "
                     "AJAX, o las consultas son de un dominio que la sesión no permite.")
    return {"receta": receta, "omitidos": omitidos, "notas": notas}
