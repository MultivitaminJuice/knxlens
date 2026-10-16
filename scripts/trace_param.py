"""Bedingungskette eines sichtbaren Parameters: welche choose-Werte führen dorthin?"""
import re, sys
from knxlens import decode, param_compare, params
from knxlens.catalog import Catalog, _local
from knxlens.store import Store
project, pa, text, mask = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4], 0)
s = Store("data/knxlens.sqlite"); c = Catalog(); c.add(project)
pv = {re.sub(r"_R-\d+$", "", k): v for k, v in param_compare.project_values(project).get(pa, {}).items()}
d = decode.decode_device(s, pa, mask); ap = params.AppParams(c.lookup(d))
ev = params.evaluate_from_store(ap, decode.Raw(s, pa))
target = next(v for v in ev.values if v.text.startswith(text)).ref_id
def find(el, chain):
    for ch in el:
        tag = _local(ch.tag)
        if tag == "ParameterRefRef" and ch.get("RefId") == target:
            return chain
        if tag == "choose": r = find(ch, chain + [[ch.get("ParamRefId"), None]])
        elif tag == "when": r = find(ch, chain[:-1] + [[chain[-1][0], ch.get("test") or "default"]])
        else: r = find(ch, chain)
        if r is not None:
            return r
chain = find(ap.dynamic, [])
for ref_id, test in chain or []:
    v = ev.resolve(ref_id, params.Context())
    key = re.sub(r"_R-\d+$", "", ref_id)
    print(f"{ap.refs[ref_id].text!r:55} Zweig {test:8} gelesen {v.value!r:6} ({'Gerät' if v.from_device else 'ETS-only'}) Projekt {pv.get(key, '(Std ' + v.default + ')')}")
