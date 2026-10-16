"""Referenz-.knxproj parsen und als JSON ablegen."""
import json, sys
from xknxproject import XKNXProj

proj = XKNXProj(sys.argv[1], password=(sys.argv[3] if len(sys.argv) > 3 else None), language="de-DE").parse()
json.dump(proj, open(sys.argv[2], "w"), indent=1, ensure_ascii=False, default=str)
info = proj["info"]
print(info.get("name"), "| ETS", info.get("tool_version"), "| letzte Änderung", info.get("last_modified"))
print("Geräte:", len(proj["devices"]), "| GAs:", len(proj["group_addresses"]), "| Topologie:", {a: list(v["lines"]) for a, v in proj["topology"].items()})
for pa, d in sorted(proj["devices"].items(), key=lambda x: [int(p) for p in x[0].split(".")]):
    n_links = sum(len(c["group_address_links"]) for c in d["communication_object_ids"] and [proj["communication_objects"][i] for i in d["communication_object_ids"]] or [])
    print(f"{pa:9} {d['manufacturer_name'][:14]:14} {d['name'][:40]:40} {d.get('order_number','')[:18]:18} COs:{len(d['communication_object_ids']):3} Links:{n_links}")
