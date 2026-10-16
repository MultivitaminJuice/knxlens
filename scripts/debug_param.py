"""Einzelne Parameter eines Geräts im Detail: Typ, Ort, Rohbytes, Wert, Projektwert."""
import re, sys
from knxrecon import decode, param_compare, params
from knxrecon.catalog import Catalog
from knxrecon.store import Store
project, pa, pattern = sys.argv[1], sys.argv[2], sys.argv[3]
mask = int(sys.argv[4], 0)
s = Store("data/recon.sqlite"); c = Catalog(); c.add(project)
pv = {re.sub(r"_R-\d+$", "", k): v for k, v in param_compare.project_values(project).get(pa, {}).items()}
d = decode.decode_device(s, pa, mask); ap = params.AppParams(c.lookup(d)); raw = decode.Raw(s, pa)
ev = params.evaluate_from_store(ap, raw)
for v in ev.values:
    if re.search(pattern, v.text):
        ref = ap.refs[v.ref_id]; p = ref.param; t = p.ptype; loc = p.loc
        print(f"{v.text!r}: kind={t.kind} bits={t.bits} enc={t.encoding!r} unit={t.unit!r} signed={t.signed}")
        if loc and loc.segment:
            a = ev.address(loc, params.Context(v.instance_id.split('_MI-1')[0] if '_MI-1' in v.instance_id else None))
            print(f"   seg={loc.segment[-8:]} bit={loc.bit} base_arg={loc.base_arg} addr={a}")
        print(f"   raw={v.raw!r} -> {v.display!r} | default={ref.default!r} | Projekt={pv.get(v.param_key)!r} | enums={dict(list(t.enums.items())[:8])}")
