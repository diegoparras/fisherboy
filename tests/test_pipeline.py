"""Tests del pipeline: happy path y fail-closed."""
from __future__ import annotations

import pytest

from app.fetchers.static import FetchResult
from app.models import JobStatus, PrivacyMode, Rol, Sobre
from app.pipeline import PipelineDeps, process_job
from app.privacy.anonimal_client import AnonimalError


def _sobre(**kw) -> Sobre:
    base = dict(
        job_id="t1",
        source_url="https://1.1.1.1/articulo",
        privacy_mode=PrivacyMode.OPACO,
        rol=Rol.DIOS,
    )
    base.update(kw)
    return Sobre(**base)


def _html_result() -> FetchResult:
    return FetchResult(
        url="https://1.1.1.1/articulo",
        status_code=200,
        content=b"<html><body><article>Hola juan@mail.com</article></body></html>",
        text="<html><body><article>Hola juan@mail.com</article></body></html>",
        content_type="text/html; charset=utf-8",
    )


def test_happy_path_static_to_markdown_opaco():
    deps = PipelineDeps(
        fetch=lambda url, tier_hint=None: _html_result(),
        extract=lambda html, url: "Hola juan@mail.com",
        anonymize_opaco=lambda md: ("Hola «EMAIL_1»", 1),
    )
    sobre = process_job(_sobre(), deps)
    assert sobre.status is JobStatus.OK
    assert sobre.anonimizado is True
    assert sobre.content_md == "Hola «EMAIL_1»"
    assert sobre.meta["entidades_anonimizadas"] == 1
    assert sobre.tier_usado == 0


def test_fail_closed_on_anonimization_failure():
    def _boom(_md):
        raise AnonimalError("Anonimal caído")

    deps = PipelineDeps(
        fetch=lambda url, tier_hint=None: _html_result(),
        extract=lambda html, url: "contenido con PII real",
        anonymize_opaco=_boom,
    )
    sobre = process_job(_sobre(), deps)
    assert sobre.status is JobStatus.ERROR
    assert sobre.content_md is None       # nunca se devuelve crudo
    assert sobre.anonimizado is False
    assert "Anonim" in sobre.error


def test_fetch_error_marks_error_no_content():
    from app.fetchers.static import FetchError

    def _fetch(url, tier_hint=None):
        raise FetchError("404")

    deps = PipelineDeps(
        fetch=_fetch,
        extract=lambda html, url: "x",
        anonymize_opaco=lambda md: (md, 0),
    )
    sobre = process_job(_sobre(), deps)
    assert sobre.status is JobStatus.ERROR
    assert sobre.content_md is None
