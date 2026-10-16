"""Gelesene Parameter aller Geräte gegen ein ETS-Projekt prüfen (Testanlage)."""
import re
import sys
from collections import Counter

from knxrecon import decode, device, param_compare, params
from knxrecon.catalog import Catalog
from knxrecon.store import Store

project = sys.argv[1]
store = Store(sys.argv[2] if len(sys.argv) > 2 else "data/recon.sqlite")
cat = Catalog()
cat.add(project)
values = param_compare.project_values(project)
total = Counter()
cache = {}
for pa, mask in store.found_devices():
    if not device.supported(mask):
        continue
    d = decode.decode_device(store, pa, mask)
    app = cat.lookup(d)
    if app is None:
        print(f"{pa:9} keine Produktdaten")
        continue
    ap = cache.get(app.key) or cache.setdefault(app.key, params.AppParams(app))
    ev = params.evaluate_from_store(ap, decode.Raw(store, pa), {n for n, _ in d.links})
    r = param_compare.check(ev, values.get(pa, {}))
    pv = {re.sub(r"_R-\d+$", "", k): v for k, v in values.get(pa, {}).items()}
    inf_ok = sum(1 for key, test, default in ev.inferred_keys if params._test(test, pv.get(key, default)) or (not test))
    total.update(sichtbar=r.visible, aus_geraet=r.from_device, gleich=r.equal, nur_ets=r.only_ets,
                 abweichend=len(r.mismatches), unberuehrt_markiert=r.untouched_mismatch, intern=r.internal, erschlossen=len(ev.inferred_keys), erschlossen_richtig=inf_ok)
    flag = "" if not r.mismatches else f"  ← {len(r.mismatches)} Abweichungen"
    print(f"{pa:9} {app.key:18} sichtbar {r.visible:4}  aus Gerät {r.from_device:4}  gleich {r.equal:4}  "
          f"nur ETS {r.only_ets:3}  erschlossen {inf_ok}/{len(ev.inferred_keys)}{flag}"
          + (f"  nicht gelesen: {sorted(ev.unreadable)}" if ev.unreadable else ""))
    for m in r.mismatches[:4]:
        print("          ", m[:3])
print(dict(total))
