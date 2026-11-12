"""Schutz der lokalen Weboberflächen (Editor, Rekonstruktion) gegen fremde Webseiten.

Die Server lauschen standardmäßig nur auf 127.0.0.1. Das allein reicht nicht: Eine Webseite
im selben Browser kann Anfragen an 127.0.0.1 schicken (CSRF) oder ihren eigenen Namen auf
127.0.0.1 umbiegen und dann Antworten lesen (DNS-Rebinding). Daher:

- Host-Prüfung: nur bekannte Namen (Loopback, ausdrücklich freigegebene Hosts).
  Damit scheitert DNS-Rebinding, denn dort trägt die Anfrage den Namen der fremden Seite.
- Herkunftsprüfung für ändernde Anfragen: Origin bzw. Sec-Fetch-Site müssen zur eigenen
  Adresse passen. Browser setzen diese Köpfe selbst; eine fremde Seite kann sie nicht fälschen.
  Werkzeuge ohne diese Köpfe (curl, Tests) bleiben möglich – sie sind keine Webseiten.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _host_only(value: str) -> str:
    """'127.0.0.1:8770' → '127.0.0.1', '[::1]:8770' → '::1'."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def allowed_hosts(bind_host: str, extra: list[str] | None = None) -> set[str] | None:
    """Erlaubte Host-Namen; None = jeder Host (nur wenn auf alle Schnittstellen gebunden und
    nichts ausdrücklich freigegeben ist – dann warnt die Kommandozeile)."""
    hosts = set(LOOPBACK) | {_host_only(h) for h in (extra or [])}
    if bind_host in ("0.0.0.0", "::", ""):
        return hosts if extra else None
    return hosts | {_host_only(bind_host)}


class WebSafety(BaseHTTPMiddleware):
    def __init__(self, app, hosts: set[str] | None):
        super().__init__(app)
        self.hosts = hosts

    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if self.hosts is not None and _host_only(host) not in self.hosts:
            return JSONResponse({"detail": f"Unbekannter Host: {host}"}, status_code=421)
        if request.method in UNSAFE:
            site = request.headers.get("sec-fetch-site")
            if site and site not in ("same-origin", "none"):
                return JSONResponse({"detail": "Anfrage von fremder Webseite abgewiesen"}, status_code=403)
            origin = request.headers.get("origin")
            if origin is not None and (origin == "null" or urlsplit(origin).netloc.lower() != host.lower()):
                return JSONResponse({"detail": "Anfrage von fremder Herkunft abgewiesen"}, status_code=403)
        return await call_next(request)


def install(app, hosts: set[str] | None) -> None:
    app.add_middleware(WebSafety, hosts=hosts)
