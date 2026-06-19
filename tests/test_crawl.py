"""Tests de crawling: discovery (links/sitemap/rss), robots y BFS con dedup."""
from __future__ import annotations

from app.crawl.crawler import crawl
from app.crawl.discovery import extract_links, parse_rss, parse_sitemap
from app.crawl.robots import RobotsChecker
from app.fetchers.base import FetchResult


# --------------------------------------------------------------------------- discovery
def test_extract_links_same_domain_only():
    html = "<a href='/b'>b</a> <a href='http://otro.com/c'>c</a> <a href='#x'>frag</a>"
    got = extract_links(html, "http://x.com/a")
    assert got == ["http://x.com/b"]


def test_extract_links_allow_external():
    html = "<a href='http://otro.com/c'>c</a>"
    assert extract_links(html, "http://x.com/a", same_domain=False) == ["http://otro.com/c"]


def test_parse_sitemap():
    xml = "<urlset><url><loc>http://x.com/1</loc></url><url><loc>http://x.com/2</loc></url></urlset>"
    assert parse_sitemap(xml) == ["http://x.com/1", "http://x.com/2"]


def test_parse_rss_atom():
    xml = "<feed><entry><link href='http://x.com/p1'/></entry></feed>"
    assert parse_rss(xml) == ["http://x.com/p1"]


# --------------------------------------------------------------------------- robots
def test_robots_allows_and_blocks():
    robots = RobotsChecker(lambda u: "User-agent: *\nDisallow: /private")
    assert robots.allowed("http://x.com/public") is True
    assert robots.allowed("http://x.com/private/secreto") is False


def test_robots_missing_allows():
    robots = RobotsChecker(lambda u: None)
    assert robots.allowed("http://x.com/cualquiera") is True


# --------------------------------------------------------------------------- crawler BFS
def _fetch_from(pages: dict):
    def _f(url):
        html = pages.get(url, "")
        return FetchResult(url=url, status_code=200, content=html.encode(),
                           text=html, content_type="text/html", tier=0)
    return _f


def test_crawl_bfs_bounded_by_max_pages():
    pages = {
        "http://x.com/": "<a href='/a'>a</a><a href='/b'>b</a>",
        "http://x.com/a": "<a href='/c'>c</a>",
        "http://x.com/b": "hoja b",
        "http://x.com/c": "hoja c",
    }
    got = crawl("http://x.com/", fetch=_fetch_from(pages), max_pages=3, max_depth=3)
    assert len(got) == 3
    assert got[0].url == "http://x.com/"


def test_crawl_dedup_by_content():
    # /a y /b sirven contenido idéntico → la segunda se descarta por hash.
    pages = {
        "http://x.com/": "<a href='/a'>a</a><a href='/b'>b</a>",
        "http://x.com/a": "MISMO CONTENIDO",
        "http://x.com/b": "MISMO CONTENIDO",
    }
    got = crawl("http://x.com/", fetch=_fetch_from(pages), max_pages=10, max_depth=1)
    urls = [p.url for p in got]
    assert "http://x.com/" in urls
    # solo una de /a /b sobrevive (mismo hash de contenido)
    assert len([u for u in urls if u in ("http://x.com/a", "http://x.com/b")]) == 1


def test_crawl_respects_robots():
    pages = {"http://x.com/": "<a href='/no'>x</a>", "http://x.com/no": "secreto"}
    robots = RobotsChecker(lambda u: "User-agent: *\nDisallow: /no")
    got = crawl("http://x.com/", fetch=_fetch_from(pages),
                robots_allowed=robots.allowed, max_pages=10, max_depth=1)
    assert all(p.url != "http://x.com/no" for p in got)


def test_build_tree_hierarchy():
    from app.crawl.crawler import build_tree, CrawlPage
    def fr(u, txt="x"):
        return FetchResult(url=u, status_code=200, content=txt.encode(), text=txt,
                           content_type="text/html", tier=0)
    pages = [
        CrawlPage(url="http://x.com/", result=fr("http://x.com/", "# Home"), depth=0, parent=None),
        CrawlPage(url="http://x.com/a", result=fr("http://x.com/a"), depth=1, parent="http://x.com/"),
        CrawlPage(url="http://x.com/b", result=fr("http://x.com/b"), depth=1, parent="http://x.com/"),
        CrawlPage(url="http://x.com/a1", result=fr("http://x.com/a1"), depth=2, parent="http://x.com/a"),
    ]
    tree = build_tree(pages)
    assert tree["url"] == "http://x.com/"
    assert tree["title"] == "Home"
    assert len(tree["children"]) == 2                       # /a y /b
    a = next(c for c in tree["children"] if c["url"].endswith("/a"))
    assert len(a["children"]) == 1 and a["children"][0]["url"].endswith("/a1")
