"""Rekonstruiertes Projekt als .knxproj schreiben (experimentell).

Produktdaten (M-xxxx/…, knx_master.xml, Herstellersignaturen) werden unverändert aus
der Quelle übernommen – so bleiben die Signaturen der Hersteller gültig. Der Projektteil
(P-xxxx/project.xml, P-xxxx/0.xml) wird erzeugt; eine Projektsignatur gibt es nicht.
Ob die ETS das ohne Beanstandung öffnet, ist ungeklärt.
"""

from __future__ import annotations

import base64
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from xknx.telegram import GroupAddress

from .catalog import Application, Catalog
from .model import Device
from .params import Evaluation

NS_DEFAULT = "http://knx.org/xml/project/23"


@dataclass
class ProjectInput:
    name: str
    devices: list[Device]
    catalog: Catalog
    evaluations: dict[str, Evaluation | None]
    ga_names: dict[str, str] = field(default_factory=dict)
    range_names: dict[str, str] = field(default_factory=dict)
    dpts: dict[str, dict] = field(default_factory=dict)
    device_notes: dict[str, dict[str, str]] = field(default_factory=dict)
    project_id: str = "P-0E7A"


class Puid:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return str(self.n)


def _dpst(dpt: dict | None) -> str | None:
    if not dpt or not dpt.get("main"):
        return None
    return f"DPST-{dpt['main']}-{dpt['sub']}" if dpt.get("sub") is not None else f"DPT-{dpt['main']}"


def _namespace(catalog: Catalog) -> str:
    """Schema-Version der Produktdaten übernehmen (z. B. project/20 oder /23)."""
    for app in catalog.apps.values():
        try:
            with zipfile.ZipFile(app.source_path) as z:
                head = z.read(app.member)[:600].decode("utf-8", "ignore")
            m = re.search(r'xmlns="(http://knx\.org/xml/project/\d+)"', head)
            if m:
                return m[1]
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
    return NS_DEFAULT


def build_installation(inp: ProjectInput) -> tuple[bytes, dict]:
    """0.xml erzeugen. Liefert XML und eine kleine Statistik."""
    ns = _namespace(inp.catalog)
    pid = inp.project_id
    puid = Puid()
    root = Element("KNX", xmlns=ns, CreatedBy="knxrecon", ToolVersion="0.1")
    project = SubElement(root, "Project", Id=pid)
    inst = SubElement(SubElement(project, "Installations"), "Installation", Name="", BCUKey="4294967295")
    topo = SubElement(inst, "Topology")
    stats = {"devices": 0, "without_product": [], "params": 0, "links": 0, "gas": 0}

    # Gruppenadressen-Ids zuerst vergeben (Links verweisen darauf)
    all_gas = sorted({ga for d in inp.devices for _, ga in d.links}, key=lambda g: GroupAddress(g).raw)
    ga_ids = {ga: f"GA-{i + 1}" for i, ga in enumerate(all_gas)}

    areas: dict[int, Element] = {}
    lines: dict[tuple[int, int], Element] = {}
    for di, d in enumerate(sorted(inp.devices, key=lambda d: [int(x) for x in d.pa.split(".")]), start=1):
        a, l, dev = (int(x) for x in d.pa.split("."))
        if a not in areas:
            areas[a] = SubElement(topo, "Area", Id=f"{pid}-0_A-{a + 1}", Address=str(a), Puid=puid())
        if (a, l) not in lines:
            line = SubElement(areas[a], "Line", Id=f"{pid}-0_L-{a}{l}", Address=str(l), Puid=puid())
            lines[(a, l)] = SubElement(line, "Segment", Id=f"{pid}-0_S-{a}{l}", Number="0", MediumTypeRefId="MT-0",
                                       Puid=puid())
        app: Application | None = inp.catalog.lookup(d)
        if app is None or not app.product_refs:
            stats["without_product"].append(d.pa)
            continue
        product_id, h2p_id = app.product_refs[0]
        note = inp.device_notes.get(d.pa, {})
        attrs = {
            "Id": f"{pid}-0_DI-{di}", "Address": str(dev), "Name": note.get("name", ""),
            "ProductRefId": product_id, "Hardware2ProgramRefId": h2p_id,
            "ApplicationProgramLoaded": "true", "CommunicationPartLoaded": "true",
            "IndividualAddressLoaded": "true", "MediumConfigLoaded": "true", "ParametersLoaded": "true",
            "Puid": puid(),
        }
        if d.serial:
            attrs["SerialNumber"] = base64.b64encode(bytes.fromhex(d.serial.replace(":", ""))).decode()
        el = SubElement(lines[(a, l)], "DeviceInstance", **attrs)
        stats["devices"] += 1

        ev = inp.evaluations.get(d.pa)
        if ev is not None:
            prefs = SubElement(el, "ParameterInstanceRefs")
            for v in ev.values:
                if v.from_device and not v.is_default and not v.untouched and not v.internal:
                    SubElement(prefs, "ParameterInstanceRef", RefId=v.instance_id, Value=_value(v.value))
                    stats["params"] += 1

        corefs = SubElement(el, "ComObjectInstanceRefs")
        used_instances: set[str] = set()
        by_obj: dict[int, list[str]] = {}
        for num, ga in d.links:
            by_obj.setdefault(num, []).append(ga)
        for num, gas in sorted(by_obj.items()):
            ref = _co_ref(app, ev, num)
            if ref is None:
                stats.setdefault("objects_unknown", []).append(f"{d.pa}/{num}")
                continue
            if ev is None or num not in ev.active_cos:
                stats["objects_guessed"] = stats.get("objects_guessed", 0) + 1
            links = " ".join(ga_ids[g] for g in sorted(gas, key=lambda g: GroupAddress(g).raw))
            SubElement(corefs, "ComObjectInstanceRef", RefId=ref, Links=links)
            stats["links"] += len(gas)
            if "_MI-1_" in ref:
                used_instances.add(app.full_id + "_" + ref.split("_MI-1_")[0])

        # Modulinstanzen: aktive aus der Parameterauswertung + von Objekten benötigte
        if ev is not None or used_instances:
            prefix = app.full_id + "_"
            instances = dict(ev.modules) if ev is not None else {}
            for inst_id in used_instances - instances.keys():
                args = ev.ap.module_args(inst_id) if ev is not None else None
                if args is not None:
                    instances[inst_id] = args
            if instances:
                mods = SubElement(el, "ModuleInstances")
                for inst_id, args in instances.items():
                    rel = inst_id.removeprefix(prefix)
                    mi = SubElement(mods, "ModuleInstance", Id=f"{rel}_MI-1", RefId=rel)
                    arg_el = SubElement(mi, "Arguments")
                    for ref_id, value in args:
                        SubElement(arg_el, "Argument", RefId=ref_id.removeprefix(prefix), Value=value)

    # Gruppenadressen
    granges = SubElement(SubElement(inst, "GroupAddresses"), "GroupRanges")
    mains: dict[int, Element] = {}
    middles: dict[tuple[int, int], Element] = {}
    for ga in all_gas:
        main, middle, _ = (int(x) for x in ga.split("/"))
        if main not in mains:
            mains[main] = SubElement(granges, "GroupRange", Id=f"{pid}-0_GR-{main + 1}",
                                     RangeStart=str(max(1, main << 11)), RangeEnd=str((main << 11) | 0x7FF),
                                     Name=inp.range_names.get(str(main)) or f"Hauptgruppe {main}", Puid=puid())
        if (main, middle) not in middles:
            start = (main << 11) | (middle << 8)
            middles[(main, middle)] = SubElement(
                mains[main], "GroupRange", Id=f"{pid}-0_GR-{main + 1}{middle:02d}",
                RangeStart=str(max(1, start)), RangeEnd=str(start | 0xFF),
                Name=inp.range_names.get(f"{main}/{middle}") or f"Mittelgruppe {main}/{middle}", Puid=puid())
        attrs = {"Id": f"{pid}-0_{ga_ids[ga]}", "Address": str(GroupAddress(ga).raw),
                 "Name": inp.ga_names.get(ga) or f"GA {ga}", "Puid": puid()}
        if dpt := _dpst(inp.dpts.get(ga)):
            attrs["DatapointType"] = dpt
        SubElement(middles[(main, middle)], "GroupAddress", **attrs)
        stats["gas"] += 1

    # Räume aus den Notizen
    rooms: dict[str, list[str]] = {}
    for di, d in enumerate(sorted(inp.devices, key=lambda d: [int(x) for x in d.pa.split(".")]), start=1):
        room = inp.device_notes.get(d.pa, {}).get("room")
        if room and d.pa not in stats["without_product"]:
            rooms.setdefault(room, []).append(f"{pid}-0_DI-{di}")
    if rooms:
        building = SubElement(SubElement(inst, "Locations"), "Space", Type="Building", Id=f"{pid}-0_BP-1",
                              Name=inp.name, Puid=puid())
        for i, (room, refs) in enumerate(sorted(rooms.items()), start=2):
            space = SubElement(building, "Space", Type="Room", Id=f"{pid}-0_BP-{i}", Name=room, Puid=puid())
            for ref in refs:
                SubElement(space, "DeviceInstanceRef", RefId=ref)

    indent(root)
    stats["last_puid"] = puid.n
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + tostring(root, encoding="utf-8"), stats


def _value(value: object) -> str:
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _co_ref(app: Application, ev: Evaluation | None, number: int) -> str | None:
    """Relative ComObjectRef-Id (ohne Applikations-Präfix), bevorzugt die aktive Variante."""
    key = ev.active_cos.get(number) if ev is not None else None
    if key is None:
        # Objekt ist am Bus verknüpft, aktive Variante aber nicht bestimmbar:
        # erste Variante dieser Nummer nehmen – die Verknüpfung ist wichtiger als die Variante.
        keys = sorted(k for k, n in app.ref_number.items() if n == number)
        if not keys:
            return None
        key = keys[0]
    instance, _, ref = key.rpartition("|")
    prefix = app.full_id + "_"
    rel = ref[len(prefix):] if ref.startswith(prefix) else ref
    if instance:
        # Modulobjekt: MD-2_O-2-0_R-1 in Instanz MD-2_M-1 → MD-2_M-1_MI-1_O-2-0_R-1
        inst_rel = instance[len(prefix):] if instance.startswith(prefix) else instance
        md = inst_rel.rsplit("_M-", 1)[0]
        rel = f"{inst_rel}_MI-1_{rel[len(md) + 1:]}" if rel.startswith(md + "_") else rel
    return rel


def project_xml(inp: ProjectInput, last_puid: int) -> bytes:
    ns = _namespace(inp.catalog)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    root = Element("KNX", xmlns=ns, CreatedBy="knxrecon", ToolVersion="0.1")
    project = SubElement(root, "Project", Id=inp.project_id)
    SubElement(project, "ProjectInformation", Name=inp.name, GroupAddressStyle="ThreeLevel", LastModified=now,
               ProjectStart=now, Comment="Rekonstruiert mit knxrecon (rein lesend vom Bus)",
               LastUsedPuid=str(last_puid), Guid=str(uuid.uuid4()))
    indent(root)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + tostring(root, encoding="utf-8")


def write(inp: ProjectInput, out_path: str) -> dict:
    installation, stats = build_installation(inp)
    used = {app.source_path: set() for d in inp.devices if (app := inp.catalog.lookup(d))}
    manufacturers = {}
    for d in inp.devices:
        app = inp.catalog.lookup(d)
        if app:
            manufacturers.setdefault(app.key[:6], app.source_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as out:
        master_written = False
        for mfr, source in manufacturers.items():
            with zipfile.ZipFile(source) as z:
                for name in z.namelist():
                    if name.startswith(mfr + "/") or name == mfr + ".signature":
                        out.writestr(name, z.read(name))
                    elif name == "knx_master.xml" and not master_written:
                        out.writestr(name, z.read(name))
                        master_written = True
        pid = inp.project_id
        out.writestr(f"{pid}/project.xml", project_xml(inp, stats["last_puid"]))
        out.writestr(f"{pid}/0.xml", installation)
    stats["manufacturers"] = sorted(manufacturers)
    stats["sources"] = sorted(used)
    return stats
