"""Zwei .knxproj auf XML-Ebene vergleichen (0.xml und project.xml), ohne Puid/LastUsedPuid/Attributreihenfolge."""
import sys, zipfile, re
from defusedxml import ElementTree as ET

IGNORE = {"Puid", "LastUsedPuid", "CreatedBy", "ToolVersion"}

def canon(el, path="", out=None):
    out = [] if out is None else out
    tag = el.tag.rsplit("}", 1)[-1]
    key = el.get("Id") or el.get("RefId") or el.get("Address") or ""
    here = f"{path}/{tag}[{key}]"
    attrs = tuple(sorted((k, v) for k, v in el.attrib.items() if k not in IGNORE))
    out.append((here, attrs, (el.text or "").strip()))
    for c in el:
        canon(c, here, out)
    return out

def load(path, member):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if re.match(rf"P-[0-9A-F]+/{member}$", n))
        return canon(ET.fromstring(z.read(name)))

for member in ("0.xml", "project.xml"):
    a, b = load(sys.argv[1], member), load(sys.argv[2], member)
    sa, sb = set(a), set(b)
    print(f"{member}: {len(a)} Elemente im Original, {len(b)} im Export, nur Original {len(sa - sb)}, nur Export {len(sb - sa)}")
    for x in sorted(sa - sb)[:5]:
        print("   - ", x[0][-110:], dict(x[1]))
    for x in sorted(sb - sa)[:5]:
        print("   + ", x[0][-110:], dict(x[1]))
    if [x[0] for x in a] != [x[0] for x in b] and sa == sb:
        print("   (gleiche Elemente, andere Reihenfolge)")
