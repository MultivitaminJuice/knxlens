"""Mitgeführte Produktdaten eines Projekts für den Katalog bereitstellen.

Die Produktdateien werden einmalig in einen Cache-Ordner entpackt (Schlüssel: Hash über
Namen und Inhalt), damit Katalog und Parameterauswertung sie wie einen Ordner lesen können.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..catalog import APP_KEY, Application, Catalog
from . import Project

CACHE = Path(os.environ.get("KNXLENS_CACHE", Path.home() / ".cache" / "knxlens")) / "products"


def project_catalog(p: Project) -> Catalog:
    h = hashlib.sha256()
    for name in sorted(p.products):
        h.update(name.encode())
        h.update(hashlib.sha256(p.products[name]).digest())
    folder = CACHE / h.hexdigest()[:16]
    done = folder / ".complete"
    if not done.exists():
        for name, data in p.products.items():
            target = (folder / name).resolve()
            if not str(target).startswith(str(folder.resolve())):
                raise ValueError(f"Ungültiger Pfad in Produktdaten: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        done.touch()
    cat = Catalog()
    cat.add(folder)
    return cat


def app_for(cat: Catalog, full_id: str) -> Application | None:
    m = APP_KEY.match(full_id or "")
    return cat.apps.get(m[1]) if m else None
