"""Kodierung prüfen: für jeden aus dem Gerät gelesenen Parameter muss encode(decode(raw)) == raw sein."""
import sys
from collections import Counter
from knxlens import catalog, decode, device, params
from knxlens.store import Store

store = Store(sys.argv[2] if len(sys.argv) > 2 else "data/knxlens.sqlite")
cat = catalog.Catalog(); cat.add(sys.argv[1])
c = Counter(); bad = []
cache = {}
for pa, m in store.found_devices():
    if not device.supported(m):
        continue
    d = decode.decode_device(store, pa, m)
    app = cat.lookup(d)
    if app is None:
        continue
    ap = cache.get(app.key) or cache.setdefault(app.key, params.AppParams(app))
    ev = params.evaluate_from_store(ap, decode.Raw(store, pa), {n for n, _ in d.links})
    for v in ev.values:
        if not v.from_device:
            continue
        t = ap.refs[v.ref_id].param.ptype
        c[t.kind] += 1
        try:
            ok = params.encode_value(t, v.value) == v.raw
        except Exception as e:  # noqa: BLE001
            ok, v.display = False, f"{v.display} ({e})"
        if not ok:
            bad.append((pa, t.kind, t.encoding or t.unit, v.text, v.raw, v.value))
print("geprüft je Typ:", dict(c), "gesamt", sum(c.values()))
print("Abweichungen:", len(bad))
for b in bad[:10]:
    print("  ", b)
