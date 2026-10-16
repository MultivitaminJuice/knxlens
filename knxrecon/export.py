"""Export für die ETS.

Gruppenadressen im XML-Format, das die ETS selbst exportiert und wieder importiert
(GroupAddress-Export, Namensraum http://knx.org/xml/ga-export/01). Nur gesicherte
DPTs (Referenz/Produktdaten) – Vorschläge aus der Nutzlastlänge bleiben draußen.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from xknx.telegram import GroupAddress

NS = "http://knx.org/xml/ga-export/01"


def _dpt_attr(dpt: dict | None) -> str | None:
    if not dpt or not dpt.get("main"):
        return None
    if dpt.get("sub") is not None:
        return f"DPST-{dpt['main']}-{dpt['sub']}"
    return f"DPT-{dpt['main']}"


def ga_xml(gas: list[str], names: dict[str, str], dpts: dict[str, dict], range_names: dict[str, str]) -> bytes:
    """gas: alle GAs (3-stufig). names: GA → Name. range_names: '1' bzw. '1/1' → Name."""
    root = Element("GroupAddress-Export", xmlns=NS)
    mains: dict[int, Element] = {}
    middles: dict[tuple[int, int], Element] = {}
    for ga in sorted(set(gas), key=lambda g: GroupAddress(g).raw):
        main, middle, sub = (int(x) for x in ga.split("/"))
        if main not in mains:
            mains[main] = SubElement(
                root, "GroupRange", Name=range_names.get(str(main)) or f"Hauptgruppe {main}",
                RangeStart=str(main << 11), RangeEnd=str((main << 11) | 0x7FF),
            )
        key = (main, middle)
        if key not in middles:
            start = (main << 11) | (middle << 8)
            middles[key] = SubElement(
                mains[main], "GroupRange", Name=range_names.get(f"{main}/{middle}") or f"Mittelgruppe {main}/{middle}",
                RangeStart=str(start), RangeEnd=str(start | 0xFF),
            )
        attrs = {"Name": names.get(ga) or f"GA {ga}", "Address": ga}
        if dpt := _dpt_attr(dpts.get(ga)):
            attrs["DPTs"] = dpt
        SubElement(middles[key], "GroupAddress", **attrs)
    indent(root)
    return b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n' + tostring(root, encoding="utf-8")
