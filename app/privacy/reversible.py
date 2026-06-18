"""Modo reversible: pseudonimización con tabla de mapeo cifrada. Ver ADR-002/003/005.

Reversible permite que la extracción por LLM corra sobre un proveedor externo sin
exponer PII real: el texto sale pseudonimizado («PERSONA_1»…), el LLM razona sobre
los marcadores, y la respuesta se RE-HIDRATA local con la tabla de mapeo.

Implementación del lado de Fisherboy (el contrato `/privacy/process` de Anonimal del
ADR-003 todavía no existe; acá se cumple la misma semántica mientras tanto):

- La tabla token→original se cifra en reposo (Fernet) y se guarda bajo un
  `mapping_ref` opaco, atado al ROL que lo creó, con TTL (ciclo de vida del job).
- `revert` valida el rol del solicitante contra el atado al ref Y contra la matriz,
  rehidrata, y BORRA la tabla (un solo uso). Se audita sin loggear el contenido.
- Fail-closed (ADR-005): sin cripto o sin store, no hay reversible → error. La PII de
  alto riesgo ya va cubierta por la pasada determinística de detectors.py.

Amenaza primaria (ADR-005 T1): la garantía está acotada por la recall de detección,
no por el cifrado. Un span no detectado viaja al LLM sin enmascarar. Por eso la
pasada determinística corre además del modelo, y el sesgo es conservador.
"""
from __future__ import annotations

import json
import secrets

from ..logging import get_logger
from ..models import PrivacyMode, Rol
from .anonimal_client import AnonimalError, build_reversible

log = get_logger("fisherboy.reversible")

_REF_PREFIX = "fisherboy:revmap:"


class ReversibleError(AnonimalError):
    """Falla del flujo reversible (cripto, store, autorización)."""


def _make_fernet(key: str):
    """Construye Fernet. Si no hay clave en env, genera una por proceso (con aviso).

    SEGURIDAD: sin REVERSIBLE_KEY los mapas no se pueden descifrar tras un reinicio
    (la clave muere con el proceso). Para reversible estable entre workers/reinicios,
    seteá REVERSIBLE_KEY con una clave Fernet (urlsafe base64 de 32 bytes).
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover
        raise ReversibleError(
            "El modo reversible necesita 'cryptography'. Instalá: pip install cryptography."
        ) from e
    if key:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    generated = Fernet.generate_key()
    log.warning(
        "REVERSIBLE_KEY no seteada: clave Fernet generada por proceso. Los mapping_ref "
        "no sobreviven reinicios ni se comparten entre workers. Seteá REVERSIBLE_KEY."
    )
    return Fernet(generated)


class ReversibleStore:
    """Guarda tablas de mapeo cifradas. Redis (compartido) o memoria (1 proceso)."""

    def __init__(self, fernet, *, redis_client=None, ttl_s: int = 24 * 3600) -> None:
        self._f = fernet
        self._r = redis_client
        self._ttl = ttl_s
        self._mem: dict[str, bytes] = {}

    def put(self, mapping: dict[str, str], rol: Rol) -> str:
        ref = secrets.token_urlsafe(24)
        blob = self._f.encrypt(json.dumps({"rol": rol.value, "map": mapping}).encode("utf-8"))
        key = f"{_REF_PREFIX}{ref}"
        if self._r is not None:
            self._r.set(key, blob, ex=self._ttl)
        else:
            self._mem[key] = blob
        return ref

    def pop(self, ref: str) -> dict | None:
        """Lee y BORRA (un solo uso). Devuelve {rol, map} descifrado o None."""
        key = f"{_REF_PREFIX}{ref}"
        blob = None
        if self._r is not None:
            blob = self._r.get(key)
            if blob is not None:
                self._r.delete(key)
        else:
            blob = self._mem.pop(key, None)
        if blob is None:
            return None
        try:
            data = json.loads(self._f.decrypt(blob).decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — token inválido/expirado/corrupto
            raise ReversibleError("No se pudo descifrar la tabla de mapeo.") from e
        return data


def _restore(content: str, mapping: dict[str, str]) -> str:
    """Reemplaza «TIPO_N» por sus valores originales. Un solo paso, sin reprocesar."""
    if not mapping:
        return content
    # Reemplazo más largo primero evita que «X_1» pise a «X_10».
    for tok in sorted(mapping, key=len, reverse=True):
        content = content.replace(tok, mapping[tok])
    return content


class ReversibleAnonymizer:
    """Orquesta el flujo reversible sobre un AnonimalClient y un ReversibleStore."""

    def __init__(self, anonimal_client, store: ReversibleStore, policy=None) -> None:
        self.client = anonimal_client
        self.store = store
        self.policy = policy  # PrivacyPolicy, para validar rol en revert

    def process(self, text: str, rol: Rol) -> tuple[str, str | None, int]:
        """Pseudonimiza y guarda el mapa cifrado. Devuelve (texto, mapping_ref, n)."""
        if not text or not text.strip():
            return text, None, 0
        opf_spans = self.client.detect_spans(text)          # falla cerrado si Anonimal cae
        pseudo, mapping, n = build_reversible(text, opf_spans)
        ref = self.store.put(mapping, rol) if mapping else None
        log.info("reversible: pseudonimizado", extra={"entidades": n, "mapping_ref": bool(ref)})
        return pseudo, ref, n

    def revert(self, content: str, mapping_ref: str, rol: Rol) -> str:
        """Rehidrata `content` con el mapa de `mapping_ref`. Valida rol y borra el mapa."""
        # El rol del solicitante debe habilitar reversible (matriz).
        if self.policy is not None:
            allowed = self.policy.allowed_modes(rol)
            if PrivacyMode.REVERSIBLE not in allowed:
                raise ReversibleError(f"El rol '{rol.value}' no puede revertir (no habilita reversible).")

        data = self.store.pop(mapping_ref)   # lee y borra (un solo uso)
        if data is None:
            raise ReversibleError("mapping_ref inexistente o expirado.")

        owner = data.get("rol")
        if owner != rol.value:
            # Auditar el intento sin loggear contenido (ADR-005 T3).
            log.warning("reversible: revert denegado por rol", extra={"owner": owner, "solicitante": rol.value})
            raise ReversibleError("El rol no coincide con el que creó el mapeo.")

        out = _restore(content, data.get("map") or {})
        log.info("reversible: revert ok", extra={"rol": rol.value})
        return out


def build_reversible_anonymizer(settings, anonimal_client, *, policy=None, redis_client=None):
    """Construye el orquestador reversible desde la config. Falla cerrado si falta cripto."""
    fernet = _make_fernet(settings.reversible_key)
    store = ReversibleStore(fernet, redis_client=redis_client, ttl_s=settings.reversible_ttl_s)
    return ReversibleAnonymizer(anonimal_client, store, policy=policy)
