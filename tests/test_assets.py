"""Manifiesto de archivos/media: extractor + cableo en el pipeline + privacidad."""
from __future__ import annotations

from app.extractors.assets import harvest_assets
from app.fetchers.base import FetchResult
from app.models import PrivacyMode, Rol, Sobre
from app.net.download import safe_filename
from app.pipeline import PipelineDeps, _harvest_files

_HTML = """
<a href="/files/report.pdf">Informe de Juan Perez</a>
<a href="https://cdn.x.com/data.zip">backup</a>
<a href="/sin-ext" download="contrato.docx">Contrato</a>
<video poster="/p.jpg"><source src="https://x.com/v.mp4"></video>
<audio src="/audio/clip.mp3"></audio>
<img src="/img/a.png"><img src="/img/a.png">
<iframe src="https://docs.google.com/viewer?url=https%3A%2F%2Fx.com%2Fcontrato.pdf&embedded=true"></iframe>
<iframe src="https://www.youtube.com/embed/abc123"></iframe>
<embed src="https://x.com/manual.pdf">
"""


def test_harvest_classifies_and_resolves():
    out = harvest_assets(_HTML, "https://site.com/page/")
    docs = {d["url"] for d in out["document"]}
    assert "https://site.com/files/report.pdf" in docs
    assert "https://x.com/manual.pdf" in docs        # <embed> con extensión
    assert "https://x.com/contrato.pdf" in docs      # Google Docs viewer desincrustado
    assert {a["url"] for a in out["archive"]} == {"https://cdn.x.com/data.zip"}
    assert {a["url"] for a in out["audio"]} == {"https://site.com/audio/clip.mp3"}
    assert {v["url"] for v in out["video"]} == {"https://x.com/v.mp4"}
    assert out["embed"][0]["provider"] == "youtube"
    assert out["total"] >= 8


def test_harvest_dedups_images():
    out = harvest_assets('<img src="/a.png"><img src="/a.png"><img src="/b.png">',
                         "https://s.com/")
    assert len(out["image"]) == 2


def test_harvest_ignores_relative_without_base_scheme():
    out = harvest_assets('<a href="javascript:void(0)">x</a><a href="mailto:a@b.com">m</a>',
                         "https://s.com/")
    assert out["total"] == 0


def test_viewer_unwrap_office():
    out = harvest_assets(
        '<iframe src="https://view.officeapps.live.com/op/view.aspx?src=https%3A%2F%2Fx.com%2Fp.xlsx"></iframe>',
        "https://s.com/")
    assert any(d["url"] == "https://x.com/p.xlsx" for d in out["document"])


def _deps(mode="both"):
    def fake_anon(text):
        return text.replace("Juan Perez", "«PERSONA_1»"), 1
    return PipelineDeps(fetch=None, extract=None, anonymize_opaco=fake_anon,
                        file_download_mode=mode)


def _page():
    return FetchResult(url="https://site.com/page/", status_code=200,
                       content=_HTML.encode(), text=_HTML, content_type="text/html", tier=0)


def test_pipeline_directo_keeps_raw_urls():
    s = Sobre(job_id="1", source_url="https://site.com/page/",
              privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    _harvest_files(_deps(), s, [_page()])
    files = s.meta["files"]
    assert not files.get("masked")
    assert files["document"][0]["url"].startswith("https://")
    assert "Juan Perez" in files["document"][0]["name"]


def test_pipeline_opaco_masks_urls_and_names():
    s = Sobre(job_id="2", source_url="https://site.com/page/",
              privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    _harvest_files(_deps(), s, [_page()])
    files = s.meta["files"]
    assert files["masked"] is True
    assert "«PERSONA_1»" in files["document"][0]["name"]


def test_pipeline_off_skips_harvest():
    s = Sobre(job_id="3", source_url="https://site.com/page/",
              privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    _harvest_files(_deps("off"), s, [_page()])
    assert "files" not in s.meta


def test_pipeline_no_files_no_key():
    s = Sobre(job_id="4", source_url="https://site.com/page/",
              privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    page = FetchResult(url="https://site.com/page/", status_code=200,
                       content=b"<p>hola sin archivos</p>", text="<p>hola sin archivos</p>",
                       content_type="text/html", tier=0)
    _harvest_files(_deps(), s, [page])
    assert "files" not in s.meta


def test_safe_filename_strips_path_traversal():
    assert "/" not in safe_filename("https://x.com/../../etc/passwd")
    assert ".." not in safe_filename("https://x.com/a/..%2f..%2fevil")
    assert safe_filename("https://x.com/x", content_type="application/pdf").endswith(".pdf")
