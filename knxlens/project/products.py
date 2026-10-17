"""Mitgeführte Produktdaten eines Projekts für den Katalog bereitstellen.

Die Produktdateien werden einmalig in einen Cache-Ordner entpackt (Schlüssel: Hash über
Namen und Inhalt), damit Katalog und Parameterauswertung sie wie einen Ordner lesen können.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
import xml.etree.ElementTree as XET
from pathlib import Path

from defusedxml import ElementTree as ET

from ..catalog import APP_FILE, APP_KEY, Application, Catalog
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
        folder.mkdir(parents=True, exist_ok=True)  # auch für Projekte ohne Produktdaten
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


# --- Produktdateien (.knxprod) ins Projekt übernehmen ------------------------------------

MAX_PRODUCT_FILE = 400 * 1024 * 1024  # Obergrenze für die ausgepackte Gesamtgröße
_KEEP = re.compile(r"^(M-[0-9A-F]{4})(/.+|\.signature)$")
# Diese Attribute machen ein Element unter Geschwistern eindeutig (für das Zusammenführen)
_KEY_ATTRS = ("Id", "RefId", "Identifier", "AttributeName")


class ProductError(ValueError):
    pass


def add_products(p: Project, data: bytes, name: str = "") -> dict:
    """Eine .knxprod (ZIP) prüfen und in die Produktdaten des Projekts übernehmen.

    Neue Dateien werden unverändert übernommen. Gibt es Hardware.xml/Catalog.xml (o. ä.) des
    Herstellers schon, werden die Einträge nach Id zusammengeführt – vorhandene bleiben, wie sie
    sind. Liefert die neu verfügbaren Produkte.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ProductError(f"{name or 'Datei'} ist keine .knxprod (kein ZIP-Archiv)") from None
    files: dict[str, bytes] = {}
    total = 0
    for info in z.infolist():
        n = info.filename
        if n.endswith("/"):
            continue
        if n.startswith("/") or ".." in n.split("/") or "\\" in n:
            raise ProductError(f"Ungültiger Pfad im Archiv: {n}")
        if not (_KEEP.match(n) or n == "knx_master.xml"):
            continue
        if info.flag_bits & 0x1:
            raise ProductError(f"{n} ist verschlüsselt (alte ETS4-Produktdatei) – bitte in der ETS neu exportieren")
        total += info.file_size
        if total > MAX_PRODUCT_FILE:
            raise ProductError("Produktdatei ist zu groß")
        files[n] = z.read(info)

    # Vorab einlesen: nur übernehmen, was der Katalog auch versteht
    probe = Catalog()
    with tempfile.TemporaryDirectory() as tmp:
        for n, b in files.items():
            t = Path(tmp) / n
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(b)
        try:
            probe.add(Path(tmp))
        except Exception as e:  # defusedxml/ParseError – Inhalt ist nicht brauchbar
            raise ProductError(f"Produktdaten nicht lesbar: {e}") from None
    if not probe.apps:
        raise ProductError("Keine Applikation in der Datei gefunden")

    known = set(p.products)
    for n, b in files.items():
        old = p.products.get(n)
        if old is None:
            p.products[n] = b
        elif old != b and n.endswith(".xml") and n != "knx_master.xml" and not APP_FILE.match(n):
            p.products[n] = _merge_xml(old, b)
        # Applikationen, Signaturen und knx_master.xml: vorhandene Fassung bleibt
    added = []
    for app in probe.apps.values():
        for (pid, _), (pname, order) in zip(app.product_refs, app.products):
            added.append({"product_ref": pid, "name": pname, "order": order, "app_id": app.full_id,
                          "new": app.member not in known})
    return {"products": added, "applications": len(probe.apps)}


def _merge_xml(old: bytes, new: bytes) -> bytes:
    a, b = ET.fromstring(old), ET.fromstring(new)
    if a.tag != b.tag:
        raise ProductError(f"Unverträgliche Produktdaten ({a.tag} ≠ {b.tag})")
    if not _merge_el(a, b):
        return old
    ns = re.match(r"\{(.*)\}", a.tag)
    if ns:
        XET.register_namespace("", ns[1])
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + XET.tostring(a, encoding="utf-8", xml_declaration=False)


def _key(el) -> tuple:
    return (el.tag, *(el.get(k) for k in _KEY_ATTRS))


def _merge_el(a, b) -> bool:
    """Kinder aus b, die in a fehlen, anhängen; gleiche (nach Tag und Id-Attributen) rekursiv."""
    changed = False
    index = {_key(c): c for c in a}
    for c in b:
        k = _key(c)
        if k in index:
            if len(c):
                changed |= _merge_el(index[k], c)
        else:
            a.append(c)
            index[k] = c
            changed = True
    return changed
