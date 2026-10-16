"""Referenz-.knxproj (via xknxproject-JSON) auf das Vergleichsformat bringen."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

APP_RE = re.compile(r"M-([0-9A-F]{4})_A-([0-9A-F]{4})-([0-9A-F]{2})")


@dataclass
class RefDevice:
    pa: str
    name: str
    order_number: str
    manufacturer: int | None
    app_number: int | None
    app_version: int | None
    links: set[tuple[int, str]] = field(default_factory=set)  # (Objektnummer, GA)


def load(path: str | Path) -> dict[str, RefDevice]:
    proj = json.loads(Path(path).read_text())
    out: dict[str, RefDevice] = {}
    for pa, d in proj["devices"].items():
        m = APP_RE.match(d.get("application") or "")
        dev = RefDevice(
            pa=pa,
            name=d["name"],
            order_number=d.get("order_number", ""),
            manufacturer=int(m[1], 16) if m else None,
            app_number=int(m[2], 16) if m else None,
            app_version=int(m[3], 16) if m else None,
        )
        for co_id in d["communication_object_ids"]:
            co = proj["communication_objects"][co_id]
            for ga in co["group_address_links"]:
                dev.links.add((co["number"], ga))
        out[pa] = dev
    return out
