"""Download-Abbild: Parameterspeicher und Tabellen (Regeln an 40 Geräten der Testanlage geprüft)."""

from knxlens.image import param_image, put, tables
from knxlens.project import ComLink, Device
from test_params import APP, SEG, setup


def test_param_image_rules(tmp_path):
    ap, _ = setup(tmp_path, {})
    img = param_image(ap, {f"{APP}_P-1_R-1": "1", f"{APP}_P-3_R-3": "-2", f"{APP}_P-5_R-5": "Flur"})
    seg = img.segments[SEG]
    assert seg[0] & 0x0F == 1  # Betriebsart im unteren Nibble
    assert seg[2:4] == (60).to_bytes(2, "big")  # Treppenlichtzeit sichtbar → Standardwert geschrieben
    assert seg[4] == 0xFE  # vorzeichenbehaftet
    assert bytes(seg[8:16]) == b"Flur\x00\x00\x00\x00"
    assert seg[16:18] == b"\x00\x01"  # versteckte Ref im aktiven Zweig: fester Wert
    assert img.defined(SEG, 18, ap)  # Union-Mitglied aktiv
    assert not img.defined(SEG, 20, ap)  # P-9 nur im inaktiven Zweig: wird nicht geschrieben
    # Standardbild bleibt, wo nichts geschrieben wird
    img2 = param_image(ap, {})
    assert img2.segments[SEG][2:4] == b"\x01\x2c" and not img2.defined(SEG, 16 + 4, ap)


def test_fixed_ref_wins_over_other_refs_value(tmp_path):
    ap, _ = setup(tmp_path, {})
    # P-7 (Access Read) im aktiven Zweig: ein Projektwert unter anderer Ref ändert nichts
    img = param_image(ap, {f"{APP}_P-7_R-99": "500"})
    assert img.segments[SEG][16:18] == b"\x00\x01"


def test_put_bits():
    buf = bytearray(b"\xff\xff")
    put(buf, 4, 6, 0)
    assert buf == bytearray(b"\xf0\x3f")


def _dev(**kw):
    return Device("D", 1, 1, 5, "P", "H", links=[
        ComLink("O-0_R-1", ["1/1/2", "1/1/1"], 0),  # sendend 1/1/2
        ComLink("O-3_R-2", ["1/1/1"], 3),
        ComLink("O-1_R-3", ["2/0/1", "1/1/2"], 1),
    ], **kw)


def test_tables_system7_order():
    t = tables(_dev(), 0x0705)
    assert t.addresses == ["1/1/1", "1/1/2", "2/0/1"]  # aufsteigend
    # erst je Objekt die sendende GA nach Objektnummer, dann die übrigen
    assert t.associations == [(2, 0), (3, 1), (1, 3), (1, 0), (2, 1)]
    assert t.address_bytes == bytes([4, 0x11, 0x05, 0x09, 0x01, 0x09, 0x02, 0x10, 0x01])  # eigene PA zuerst
    assert t.association_bytes == bytes([5, 2, 0, 3, 1, 1, 3, 1, 0, 2, 1])


def test_tables_system_b_and_missing_numbers():
    d = _dev()
    d.links[1].number = None
    t = tables(d, 0x07B0, number_of=lambda link: 3)
    assert t.address_bytes[:2] == b"\x00\x03" and len(t.address_bytes) == 2 + 3 * 2  # ohne PA
    assert t.association_bytes[:6] == b"\x00\x05\x00\x02\x00\x00"
    assert tables(d, 0x07B0).problems == ["Objekt O-3_R-2: Nummer unbekannt"]
