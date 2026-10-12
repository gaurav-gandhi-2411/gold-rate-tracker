"""A deploy must reach a returning visitor's next visit, and a host outage must stay quiet.

Reproduced 2026-10-12 with a real browser: the v77 build installed, then the v92 build served with
Pages' Cache-Control max-age=600. The new worker's cache.add() was answered from the HTTP cache, so
its fresh cache held the OLD app.js and the controllerchange reload ran stale code (404s for files
the old app.js still requested, plus an uncaught promise). Also, registration.update() had no catch,
so a Pages outage logged an uncaught error on a page that rendered fine from cache.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW = (ROOT / "service-worker.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


def test_precache_bypasses_the_http_cache() -> None:
    install = SW.split('addEventListener("install"', 1)[1].split('addEventListener("activate"', 1)[
        0
    ]
    assert 'cache: "reload"' in install  # c.add() would read a stale copy from the HTTP cache
    assert "c.add(f)" not in install  # the old call (the comment may mention c.add())


def test_page_and_scripts_are_network_first_with_a_cache_fallback() -> None:
    handler = SW.split('addEventListener("fetch"', 1)[1]
    assert "isShellCode(e.request, url)" in handler and "networkFirstShell(e, url)" in handler
    assert 'e.request.method !== "GET"' in handler  # only GETs are ever cached
    fn = SW.split("function networkFirstShell", 1)[1].split("self.addEventListener", 1)[0]
    assert 'cache: "no-cache"' in fn  # always revalidate, never trust max-age
    assert "SHELL_NET_TIMEOUT_MS" in fn and "caches.match(" in fn  # offline / slow -> cached shell
    assert "if (!res.ok) return cachedOr(res)" in fn
    assert "url.origin + url.pathname" in fn and "ignoreSearch" not in fn  # no key per ?query
    assert "e.waitUntil(" in fn  # a 404/5xx must not replace a working page


def test_third_party_and_font_requests_stay_cache_first() -> None:
    tail = SW.split("e.respondWith(networkFirstShell(e, url))", 1)[1]
    assert "caches.match(e.request).then((cached) => cached || fetch(e.request))" in tail
    assert (
        "url.origin === self.location.origin"
        in SW.split("e.respondWith(networkFirstShell(e, url))", 1)[0]
    )


def test_worker_still_takes_over_immediately_and_the_page_reloads_once() -> None:
    assert "self.skipWaiting()" in SW and "self.clients.claim()" in SW
    assert "controllerchange" in INDEX and "refreshing" in INDEX  # reload once, no loop


def test_a_failed_worker_update_check_is_not_an_uncaught_error() -> None:
    assert "registration.update();" not in INDEX
    assert "registration.update().catch(() => {});" in INDEX


def test_both_web_app_capable_meta_tags_are_present() -> None:
    assert '<meta name="mobile-web-app-capable" content="yes"' in INDEX
    assert '<meta name="apple-mobile-web-app-capable" content="yes"' in INDEX
