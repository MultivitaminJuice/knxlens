"""Rekonstruiertes Projekt gegen ein aus der ETS importiertes Projekt vergleichen."""
import re, sys
from collections import Counter
from knxlens import catalog, decode, device, params
from knxlens.project.knxproj_io import import_knxproj
from knxlens.project.reconstruct import from_reconstruction
from knxlens.store import Store

ets = import_knxproj(sys.argv[1])
store = Store(sys.argv[2] if len(sys.argv) > 2 else "data/knxlens.sqlite")
cat = catalog.Catalog(); cat.add(sys.argv[1])
devs = [d for pa, m in store.found_devices() if device.supported(m) for d in [decode.decode_device(store, pa, m)] if d.app_id or d.links]
evs, cache = {}, {}
for d in devs:
    app = cat.lookup(d)
    if app:
        ap = cache.get(app.key) or cache.setdefault(app.key, params.AppParams(app))
        ev = params.evaluate_from_store(ap, decode.Raw(store, d.pa), {n for n, _ in d.links})
        evs[d.pa] = ev if any(v.from_device for v in ev.values) else None
rec, stats = from_reconstruction("Rekonstruktion", devs, cat, evs, dpts=catalog.ga_dpts(devs, cat))
print("Rekonstruktion:", {k: (v if not isinstance(v, list) else len(v)) for k, v in stats.items()})
key = lambda r: re.sub(r"_R-\d+$", "", r)
c = Counter()
for d in rec.devices:
    e = ets.device(d.pa)
    if e is None:
        c["Gerät fehlt im ETS-Projekt"] += 1; continue
    c["Geräte"] += 1
    c["Produkt gleich"] += d.product_ref == e.product_ref
    c["Applikation gleich"] += d.app_id == e.app_id
    rl = {(l.ref, g) for l in d.links for g in l.gas}; el = {(l.ref, g) for l in e.links for g in l.gas}
    c["Links (Ref+GA) gleich"] += len(rl & el); c["Links gesamt ETS"] += len(el)
    c["GAs je Gerät gleich"] += {g for _, g in rl} == {g for _, g in el}
    c["Module gleich"] += {(m.id, tuple(m.args)) for m in d.modules} >= {(m.id, tuple(m.args)) for m in e.modules if any(l.ref.startswith(m.ref + "_MI") for l in e.links)}
    ep = {key(k): v for k, v in e.params.items()}
    for k, v in d.params.items():
        c["Parameter rekonstruiert"] += 1
        ev_ = ep.get(key(k))
        if ev_ is None: c["  …im ETS-Projekt Standard"] += 1
        elif str(ev_).lower() == str(v).lower() or (re.match(r"^-?[\d.eE+]+$", v) and re.match(r"^-?[\d.eE+]+$", ev_) and abs(float(v) - float(ev_)) < 1e-3): c["  …gleich"] += 1
        else: c["  …anders"] += 1
    c["Parameter im ETS-Projekt"] += len(e.params)
for k, v in c.items(): print(f"{k:32} {v}")
rec.save("data/scratch/rekonstruiert.knxlens"); print("gespeichert: data/scratch/rekonstruiert.knxlens")
