"""Tests del sub-pipeline documental (detección + delegación a Escriba) y su ruteo."""
from __future__ import annotations

from app.extractors.documents import DocumentError, is_document
from app.fetchers.base import FetchResult
from app.models import JobStatus, PrivacyMode, Rol, Sobre
from app.pipeline import PipelineDeps, process_job


def test_is_document_by_content_type():
    assert is_document("application/pdf", "https://x.com/a") is True
    assert is_document("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "https://x.com/a") is True


def test_is_document_by_extension():
    assert is_document("application/octet-stream", "https://x.com/informe.pdf") is True
    assert is_document("", "https://x.com/hoja.xlsx") is True


def test_is_not_document_for_html():
    assert is_document("text/html; charset=utf-8", "https://x.com/a") is False


def test_pipeline_routes_pdf_to_escriba():
    pdf = FetchResult(url="https://x.com/doc.pdf", status_code=200, content=b"%PDF-1.7...",
                      text="", content_type="application/pdf", tier=0)
    calls = {}

    def fake_convert_document(content, filename):
        calls["filename"] = filename
        return "# Documento convertido por Escriba\n\ncontenido"

    sobre = Sobre(job_id="t", source_url="https://x.com/doc.pdf",
                  privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    deps = PipelineDeps(
        fetch=lambda url, tier_hint=None: pdf,
        extract=lambda html, url: "NO debería usarse",
        anonymize_opaco=lambda md: (md, 0),
        convert_document=fake_convert_document,
    )
    out = process_job(sobre, deps)
    assert out.status is JobStatus.OK
    assert "Escriba" in out.content_md
    assert calls["filename"] == "doc.pdf"


def test_pipeline_document_error_is_handled():
    pdf = FetchResult(url="https://x.com/doc.pdf", status_code=200, content=b"%PDF",
                      text="", content_type="application/pdf", tier=0)

    def boom(content, filename):
        raise DocumentError("Escriba caído")

    sobre = Sobre(job_id="t", source_url="https://x.com/doc.pdf",
                  privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    deps = PipelineDeps(
        fetch=lambda url, tier_hint=None: pdf,
        extract=lambda html, url: "x",
        anonymize_opaco=lambda md: (md, 0),
        convert_document=boom,
    )
    out = process_job(sobre, deps)
    assert out.status.value == "error"
    assert "Escriba" in out.error
