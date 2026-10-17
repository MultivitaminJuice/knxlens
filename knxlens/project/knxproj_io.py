"""Projektmodell ⇄ .knxproj.

Import: unverschlüsselte .knxproj (ETS4–ETS6). Export: unsigniertes .knxproj – die ETS6
importiert das wegen fehlender Projektsignatur nicht; es dient dem Austausch mit
Werkzeugen, die den Inhalt lesen (xknxproject u. a.), und als verlustfreies Archiv.
"""

from __future__ import annotations

import base64
import re
import zipfile
from xml.etree.ElementTree import Element, SubElement, fromstring, indent, register_namespace, tostring

from defusedxml import ElementTree as SafeET
from xknx.telegram import GroupAddress as _GA

from . import ComLink, Device, GroupAddress, GroupRange, Line, ModuleInstance, Project, Space

MAX_MEMBER = 256 * 1024 * 1024
DEVICE_KNOWN = {"Id", "Address", "Name", "Description", "Comment", "ProductRefId", "Hardware2ProgramRefId",
                "SerialNumber", "Puid"}
DEVICE_CHILDREN = ("ParameterInstanceRefs", "ComObjectInstanceRefs", "ModuleInstances")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attrs(el, skip: set[str]) -> dict:
    return {k: v for k, v in el.attrib.items() if k not in skip and k != "Puid"}


def _ga_str(raw: int) -> str:
    return str(_GA(raw))


# --- Import ---------------------------------------------------------------------


def import_knxproj(path: str) -> Project:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if any(re.match(r"P-[0-9A-F]+\.zip$", n) for n in names):
            raise ValueError("Passwortgeschütztes Projekt – noch nicht unterstützt")
        inst_name = next((n for n in names if re.match(r"P-[0-9A-F]+/0\.xml$", n)), None)
        if inst_name is None:
            raise ValueError("Keine Installation (P-xxxx/0.xml) im Archiv")
        pid = inst_name.split("/")[0]

        def read(name: str) -> bytes:
            info = z.getinfo(name)
            if info.file_size > MAX_MEMBER:
                raise ValueError(f"{name} zu groß")
            return z.read(info)

        products = {n: read(n) for n in names
                    if (n.startswith("M-") or n == "knx_master.xml") and not n.endswith("/")}
        proj_root = SafeET.fromstring(read(f"{pid}/project.xml"))
        inst_root = SafeET.fromstring(read(inst_name))

    ns = inst_root.tag[1:].split("}")[0] if inst_root.tag.startswith("{") else ""
    register_namespace("", ns)
    info_el = next(e for e in proj_root.iter() if _local(e.tag) == "ProjectInformation")
    info = _attrs(info_el, {"Name", "LastUsedPuid"})  # Puid-Zähler wird beim Export neu gesetzt
    info["__xml__"] = [tostring(c, encoding="unicode") for c in info_el]
    p = Project(name=info_el.get("Name", pid), project_id=pid, info=info, namespace=ns, products=products)

    installation = next(e for e in inst_root.iter() if _local(e.tag) == "Installation")
    p.installation = _attrs(installation, set())
    p.installation["__xml__"] = [tostring(c, encoding="unicode") for c in installation
                                 if _local(c.tag) not in ("Topology", "Locations", "GroupAddresses")]

    # Gruppenadressen zuerst (Verknüpfungen verweisen per Id darauf)
    ga_by_id: dict[str, str] = {}
    gas_el = next((c for c in installation if _local(c.tag) == "GroupAddresses"), None)

    def walk_ranges(el, parent: str | None) -> None:
        for c in el:
            tag = _local(c.tag)
            if tag == "GroupRange":
                uid = c.get("Id")
                p.ranges.append(GroupRange(uid, c.get("Name", ""), int(c.get("RangeStart", "0")),
                                           int(c.get("RangeEnd", "0")), parent,
                                           _attrs(c, {"Id", "Name", "RangeStart", "RangeEnd"})))
                walk_ranges(c, uid)
            elif tag == "GroupAddress":
                addr = _ga_str(int(c.get("Address")))
                ga_by_id[c.get("Id")] = addr
                p.group_addresses[addr] = GroupAddress(
                    addr, c.get("Name", ""), c.get("DatapointType", ""), c.get("Description", ""), parent,
                    _attrs(c, {"Address", "Name", "DatapointType", "Description"}))
            elif tag == "GroupRanges":
                walk_ranges(c, parent)

    if gas_el is not None:
        walk_ranges(gas_el, None)

    prefix = f"{pid}-0_"
    topo = next((c for c in installation if _local(c.tag) == "Topology"), None)
    for area in (c for c in (topo if topo is not None else []) if _local(c.tag) == "Area"):
        for line in (c for c in area if _local(c.tag) == "Line"):
            segs = [s for s in line if _local(s.tag) == "Segment"]
            seg = segs[0] if segs else None
            ln = Line(int(area.get("Address")), int(line.get("Address")), line.get("Name", ""),
                      seg.get("MediumTypeRefId", "MT-0") if seg is not None else line.get("MediumTypeRefId", "MT-0"),
                      {"area": _attrs(area, {"Address"}), "line": _attrs(line, {"Address", "Name"}),
                       "segment": _attrs(seg, {"MediumTypeRefId"}) if seg is not None else None})
            p.lines.append(ln)
            holder = seg if seg is not None else line
            for di in (c for c in holder if _local(c.tag) == "DeviceInstance"):
                p.devices.append(_import_device(di, ln, ga_by_id, prefix))

    locs = next((c for c in installation if _local(c.tag) == "Locations"), None)

    def walk_spaces(el, parent: str | None) -> None:
        for c in el:
            if _local(c.tag) != "Space":
                continue
            s = Space(c.get("Id"), c.get("Type", ""), c.get("Name", ""), parent,
                      [r.get("RefId") for r in c if _local(r.tag) == "DeviceInstanceRef"],
                      _attrs(c, {"Id", "Type", "Name"}))
            p.spaces.append(s)
            walk_spaces(c, s.uid)

    if locs is not None:
        walk_spaces(locs, None)

    _resolve_apps(p)
    return p


def _import_device(di, ln: Line, ga_by_id: dict[str, str], prefix: str) -> Device:
    serial = di.get("SerialNumber", "")
    d = Device(
        uid=di.get("Id"), area=ln.area, line=ln.line,
        address=int(di.get("Address")) if di.get("Address") is not None else None,
        product_ref=di.get("ProductRefId", ""), h2p_ref=di.get("Hardware2ProgramRefId", ""),
        name=di.get("Name", ""), description=di.get("Description", ""), comment=di.get("Comment", ""),
        serial=base64.b64decode(serial).hex() if serial else "", origin="ets",
        extra=_attrs(di, DEVICE_KNOWN),
    )
    order = []
    for child in di:
        tag = _local(child.tag)
        order.append(tag)
        if tag == "ParameterInstanceRefs":
            d.params = {r.get("RefId"): r.get("Value", "") for r in child}
        elif tag == "ComObjectInstanceRefs":
            for r in child:
                links = [ga_by_id.get(prefix + x, ga_by_id.get(x, "")) for x in r.get("Links", "").split()]
                d.links.append(ComLink(r.get("RefId"), [g for g in links if g], None,
                                       _attrs(r, {"RefId", "Links"})))
        elif tag == "ModuleInstances":
            for m in child:
                args = [(a.get("RefId"), a.get("Value", "")) for a in m.iter() if _local(a.tag) == "Argument"]
                d.modules.append(ModuleInstance(m.get("Id"), m.get("RefId"), args, _attrs(m, {"Id", "RefId"})))
        else:
            d.extra_xml.append(tostring(child, encoding="unicode"))
    d.extra["__order__"] = order
    return d


def _resolve_apps(p: Project) -> None:
    """Hardware2Program → volle Applikations-Id aus den mitgeführten Produktdaten."""
    h2p_app: dict[str, str] = {}
    for name, data in p.products.items():
        if not name.endswith("/Hardware.xml"):
            continue
        root = SafeET.fromstring(data)
        for h2p in (e for e in root.iter() if _local(e.tag) == "Hardware2Program"):
            ref = next((e for e in h2p if _local(e.tag) == "ApplicationProgramRef"), None)
            if ref is not None:
                h2p_app[h2p.get("Id")] = ref.get("RefId")
    for d in p.devices:
        d.app_id = h2p_app.get(d.h2p_ref, d.app_id)


# --- Export ---------------------------------------------------------------------


def export_knxproj(p: Project, out_path: str) -> dict:
    ns = p.namespace
    register_namespace("", ns)
    q = lambda tag: f"{{{ns}}}{tag}" if ns else tag  # noqa: E731
    puid = iter(range(1, 10**9))
    nxt = lambda: str(next(puid))  # noqa: E731
    pid = p.project_id
    stats = {"devices": 0, "links": 0, "params": 0, "gas": len(p.group_addresses)}

    root = Element(q("KNX"), {"CreatedBy": "knxlens", "ToolVersion": "0.1"})
    project = SubElement(root, q("Project"), Id=pid)
    inst = SubElement(SubElement(project, q("Installations")), q("Installation"),
                      {k: v for k, v in p.installation.items() if k != "__xml__"})
    topo = SubElement(inst, q("Topology"))

    # GA-Ids: vorhandene übernehmen, fehlende fortlaufend vergeben
    used = {g.extra.get("Id") for g in p.group_addresses.values()}
    counter = iter(range(1, 10**9))
    ga_id: dict[str, str] = {}
    for g in sorted(p.group_addresses.values(), key=lambda g: g.raw):
        gid = g.extra.get("Id")
        while not gid:
            cand = f"{pid}-0_GA-{next(counter)}"
            gid = cand if cand not in used else None
        ga_id[g.address] = gid

    areas: dict[int, Element] = {}
    for ln in sorted(p.lines, key=lambda l: (l.area, l.line)):
        ex = ln.extra or {}
        if ln.area not in areas:
            a_attrs = {"Id": f"{pid}-0_A-{ln.area + 1}", **(ex.get("area") or {})}
            areas[ln.area] = SubElement(topo, q("Area"), {**a_attrs, "Address": str(ln.area), "Puid": nxt()})
        l_attrs = {"Id": f"{pid}-0_L-{ln.area}-{ln.line}", **(ex.get("line") or {}), "Address": str(ln.line)}
        if ln.name:
            l_attrs["Name"] = ln.name
        line_el = SubElement(areas[ln.area], q("Line"), {**l_attrs, "Puid": nxt()})
        s_attrs = {"Id": f"{pid}-0_S-{ln.area}-{ln.line}", "Number": "0", **(ex.get("segment") or {}),
                   "MediumTypeRefId": ln.medium}
        seg = SubElement(line_el, q("Segment"), {**s_attrs, "Puid": nxt()})
        for d in (d for d in p.devices if (d.area, d.line) == (ln.area, ln.line)):
            _export_device(seg, d, ga_id, q, nxt, stats, pid)

    # Gebäude
    if p.spaces:
        locs = SubElement(inst, q("Locations"))
        els: dict[str | None, Element] = {None: locs}
        for s in p.spaces:  # Eltern stehen in Importreihenfolge vor den Kindern
            parent = els.get(s.parent, locs)
            els[s.uid] = SubElement(parent, q("Space"), {"Type": s.type, "Id": s.uid, "Name": s.name, **s.extra,
                                                         "Puid": nxt()})
        for s in p.spaces:  # Gerätezuordnungen nach den Unterräumen (wie die ETS)
            for u in s.devices:
                SubElement(els[s.uid], q("DeviceInstanceRef"), RefId=u)

    # Gruppenadressen
    gas_root = SubElement(SubElement(inst, q("GroupAddresses")), q("GroupRanges"))
    rels: dict[str | None, Element] = {None: gas_root}
    for r in p.ranges:
        rels[r.uid] = SubElement(rels.get(r.parent, gas_root), q("GroupRange"),
                                 {"Id": r.uid, "RangeStart": str(r.start), "RangeEnd": str(r.end),
                                  "Name": r.name, **r.extra, "Puid": nxt()})
    for g in p.group_addresses.values():  # Reihenfolge des Projekts beibehalten
        attrs = {"Id": ga_id[g.address], "Address": str(g.raw), "Name": g.name, **g.extra}
        attrs["Id"] = ga_id[g.address]
        if g.dpt:
            attrs["DatapointType"] = g.dpt
        if g.description:
            attrs["Description"] = g.description
        attrs["Puid"] = nxt()
        SubElement(rels.get(g.range_uid, gas_root), q("GroupAddress"), attrs)

    for raw_xml in p.installation.get("__xml__", []):
        inst.append(fromstring(raw_xml))

    indent(root)
    last_puid = next(puid) - 1
    proj_xml = Element(q("KNX"), {"CreatedBy": "knxlens", "ToolVersion": "0.1"})
    info_attrs = {"Name": p.name, **{k: v for k, v in p.info.items() if k != "__xml__"}, "LastUsedPuid": str(last_puid)}
    info_el = SubElement(SubElement(proj_xml, q("Project"), Id=pid), q("ProjectInformation"), info_attrs)
    for raw_xml in p.info.get("__xml__", []):
        info_el.append(fromstring(raw_xml))
    indent(proj_xml)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(p.products.items()):
            z.writestr(name, data)
        z.writestr(f"{pid}/project.xml", b'<?xml version="1.0" encoding="utf-8"?>\n' + tostring(proj_xml, "utf-8"))
        z.writestr(f"{pid}/0.xml", b'<?xml version="1.0" encoding="utf-8"?>\n' + tostring(root, "utf-8"))
    return stats


def _export_device(seg: Element, d: Device, ga_id: dict[str, str], q, nxt, stats: dict, pid: str) -> None:
    attrs = {"Id": d.uid}
    if d.address is not None:
        attrs["Address"] = str(d.address)
    attrs.update({"Name": d.name, "ProductRefId": d.product_ref, "Hardware2ProgramRefId": d.h2p_ref,
                  "Comment": d.comment, "Description": d.description})
    if d.serial:
        attrs["SerialNumber"] = base64.b64encode(bytes.fromhex(d.serial)).decode()
    attrs.update({k: v for k, v in d.extra.items() if k != "__order__"})
    attrs["Puid"] = nxt()
    el = SubElement(seg, q("DeviceInstance"), attrs)
    stats["devices"] += 1

    def params(parent):
        c = SubElement(parent, q("ParameterInstanceRefs"))
        for ref, value in d.params.items():
            SubElement(c, q("ParameterInstanceRef"), RefId=ref, Value=value)
        stats["params"] += len(d.params)

    def links(parent):
        c = SubElement(parent, q("ComObjectInstanceRefs"))
        for link in d.links:
            ids = " ".join(ga_id[g].removeprefix(f"{pid}-0_") for g in link.gas if g in ga_id)
            SubElement(c, q("ComObjectInstanceRef"), {"RefId": link.ref, **link.extra, "Links": ids})
            stats["links"] += len(link.gas)

    def modules(parent):
        if not d.modules:
            return
        c = SubElement(parent, q("ModuleInstances"))
        for m in d.modules:
            mi = SubElement(c, q("ModuleInstance"), {"Id": m.id, "RefId": m.ref, **m.extra})
            args = SubElement(mi, q("Arguments"))
            for ref, value in m.args:
                SubElement(args, q("Argument"), RefId=ref, Value=value)

    builders = {"ParameterInstanceRefs": params, "ComObjectInstanceRefs": links, "ModuleInstances": modules}
    extras = iter(d.extra_xml)
    order = d.extra.get("__order__")
    if order is None:
        order = list(DEVICE_CHILDREN)  # neues Gerät: Schema-Reihenfolge
    done = set()
    for tag in order:
        if tag in builders:
            builders[tag](el)
            done.add(tag)
        else:
            raw = next(extras, None)
            if raw is not None:
                el.append(fromstring(raw))
    content = {"ParameterInstanceRefs": d.params, "ComObjectInstanceRefs": d.links, "ModuleInstances": d.modules}
    for tag in DEVICE_CHILDREN:  # nur ergänzen, was inzwischen Inhalt hat
        if tag not in done and content[tag]:
            builders[tag](el)
    for raw in extras:
        el.append(fromstring(raw))
