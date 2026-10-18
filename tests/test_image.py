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


def test_fixed_ref_value(tmp_path):
    ap, _ = setup(tmp_path, {})
    # P-7 (Access Read) im aktiven Zweig: Wert unter anderer Ref ändert nichts …
    img = param_image(ap, {f"{APP}_P-7_R-99": "500"})
    assert img.segments[SEG][16:18] == b"\x00\x01"
    # … ein an genau dieser Ref gespeicherter Wert schon (wie bei MDT 1.1.83)
    img = param_image(ap, {f"{APP}_P-7_R-7": "500"})
    assert img.segments[SEG][16:18] == (500).to_bytes(2, "big")


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


def _gated_app(tmp_path, with_assign: bool):
    """Schalter P-6 (ohne Speicher) ist nur bei Betriebsart 1 sichtbar; bei 0 setzt ihn ein Assign."""
    import test_params
    assign = f'<choose ParamRefId="{APP}_P-1_R-1"><when test="0"><Assign TargetParamRefRef="{APP}_P-6_R-6" Value="1"/></when></choose>' if with_assign else ""
    dyn = f"""<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Text="A">
      <ParameterRefRef RefId="{APP}_P-1_R-1"/>
      <choose ParamRefId="{APP}_P-1_R-1"><when test="1"><ParameterRefRef RefId="{APP}_P-6_R-6"/></when></choose>
      <choose ParamRefId="{APP}_P-6_R-6"><when test="0"><ParameterRefRef RefId="{APP}_P-2_R-2"/></when>
        <when test="1"><ParameterRefRef RefId="{APP}_P-3_R-3"/></when></choose>
      {assign}
    </ParameterBlock></ChannelIndependentBlock></Dynamic>"""
    start, end = test_params.XML.index("<Dynamic>"), test_params.XML.index("</Dynamic>") + len("</Dynamic>")
    old = test_params.XML
    test_params.XML = old[:start] + dyn + old[end:]
    try:
        return setup(tmp_path, {})[0]
    finally:
        test_params.XML = old


def test_choose_needs_visible_switch(tmp_path):
    ap = _gated_app(tmp_path / "a", with_assign=False)
    img = param_image(ap, {f"{APP}_P-1_R-1": "1"})  # Schalter sichtbar, Standard 0 → P-2
    assert img.segments[SEG][2:4] == (60).to_bytes(2, "big") and not img.defined(SEG, 4, ap)
    img = param_image(ap, {f"{APP}_P-1_R-1": "0"})  # Schalter unsichtbar → kein Zweig gilt
    assert img.segments[SEG][2:4] == b"\x01\x2c" and not img.defined(SEG, 4, ap)


def test_assign_sets_hidden_switch(tmp_path):
    ap = _gated_app(tmp_path / "b", with_assign=True)
    img = param_image(ap, {f"{APP}_P-1_R-1": "0"})  # Assign setzt den Schalter auf 1 → P-3
    assert img.segments[SEG][2:4] == b"\x01\x2c" and img.defined(SEG, 4, ap)


def test_tables_skip_inactive_objects():
    t = tables(_dev(), 0x0705, active={0, 3})  # Objekt 1 inaktiv
    assert t.addresses == ["1/1/1", "1/1/2"] and (1, 1) not in [(a, b) for a, b in t.associations]
    assert any("Objekt 1 ist inaktiv" in p for p in t.problems)


def test_values_belong_to_the_ref(tmp_path):
    ap, _ = setup(tmp_path, {})
    # Wert unter einer anderen Ref desselben Parameters gilt nicht für die aktive Ref
    img = param_image(ap, {f"{APP}_P-3_R-77": "-2"})
    assert img.segments[SEG][4] == 0
    img = param_image(ap, {f"{APP}_P-3_R-3": "-2"})
    assert img.segments[SEG][4] == 0xFE


def test_object_table_system7():
    from types import SimpleNamespace
    from knxlens.image import object_table
    from knxlens.params import Segment
    full = "M-0083_A-0001-01-AAAA"
    tpl = bytes([5, 0x07, 0x00]) + b"".join(bytes([0x07, i, 0x17, 0x07]) for i in range(5))
    v_on = SimpleNamespace(flags={"communication": True, "transmit": True, "read": True}, priority="High", size="1 Bit")
    app = SimpleNamespace(full_id=full, ref_number={f"{full}_O-0_R-1": 0, f"{full}_O-2_R-2": 2},
                          variant_by_ref={f"{full}_O-0_R-1": v_on, f"{full}_O-2_R-2": v_on})
    ap = SimpleNamespace(app=app, co_table=("SEG", 0), segments={"SEG": Segment("SEG", True, 0x4400, None, len(tpl), tpl)},
                         co_numbers={"x": (2, None)})
    ev = SimpleNamespace(active_cos={0: f"{full}_O-0_R-1", 2: f"{full}_O-2_R-2"})
    seg, off, data = object_table(ap, ev, linked={0})
    assert data[0] == 3  # höchste Objektnummer + 1, nicht die Vorlage (5)
    assert data[1:3] == b"\x07\x00"
    e0, e1, e2 = data[3:7], data[7:11], data[11:15]
    assert e0 == bytes([0x07, 0, 0x40 | 0x08 | 0x04 | 1, 0])  # aktiv, verknüpft, Priorität hoch, 1 Bit
    assert e1 == bytes([0x07, 1, 0x13, 0x07])  # inaktiv: Vorlage ohne Kommunikations-Bit
    assert e2[2] == 0x40 | 0x08 | 1  # aktiv, aber nicht verknüpft: ohne Kommunikations-Bit


def test_loadplan_merges_mask_procedure():
    from knxlens.loadplan import build, raw_steps
    app = b"""<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData><Manufacturer><ApplicationPrograms>
      <ApplicationProgram Id="A" MaskVersion="MV-07B0"><Static><LoadProcedures>
        <LoadProcedure MergeId="2"><LdCtrlRelSegment AppliesTo="full" LsmIdx="4" Size="10" Mode="1" Fill="0"/>
          <LdCtrlRelSegment AppliesTo="par" LsmIdx="4" Size="10" Mode="0" Fill="0"/></LoadProcedure>
        <LoadProcedure MergeId="4"><LdCtrlWriteRelMem AppliesTo="full,par" ObjIdx="4" Offset="0" Size="10" Verify="true"/></LoadProcedure>
      </LoadProcedures></Static></ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""
    master = b"""<KNX><MaskVersions><MaskVersion Id="MV-07B0"><Procedures>
      <Procedure ProcedureType="Load" ProcedureSubType="all"><LdCtrlConnect/><LdCtrlLoad LsmIdx="4"/>
        <LdCtrlMerge MergeId="2"/><LdCtrlMerge MergeId="4"/><LdCtrlLoadCompleted LsmIdx="4"/><LdCtrlRestart/></Procedure>
    </Procedures></MaskVersion></MaskVersions></KNX>"""
    steps = raw_steps(app, master, "MV-07B0", "all")
    assert [s[0] for s in steps] == ["Connect", "Load", "RelSegment", "WriteRelMem", "LoadCompleted", "Restart"]
    plan = build(steps, {}, {}, {4: b"\\x01" * 10})
    write = next(s for s in plan if s.op == "WriteRelMem")
    assert write.writes and write.data == b"\\x01" * 10 and "Parameter" in write.text
