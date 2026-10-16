"""Parameterdecoder gegen eine selbst gebaute Applikation (absolutes Segment wie bei BCU1)."""

from pathlib import Path

from knxlens import params
from knxlens.catalog import Catalog

APP = "M-0008_A-2032-12-ABCD"
NS = 'xmlns="http://knx.org/xml/project/20"'
SEG = f"{APP}_AS-0100"


def p(pid, ptype, text, value, offset=None, bit=0, access=None):
    mem = f'<Memory CodeSegment="{SEG}" Offset="{offset}" BitOffset="{bit}"/>' if offset is not None else ""
    acc = f' Access="{access}"' if access else ""
    return f'<Parameter Id="{APP}_P-{pid}" Name="p{pid}" ParameterType="{APP}_PT-{ptype}" Text="{text}" Value="{value}"{acc}>{mem}</Parameter>'


XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX {NS}><ManufacturerData><Manufacturer RefId="M-0008"><ApplicationPrograms>
<ApplicationProgram Id="{APP}" Name="Test" MaskVersion="MV-0012">
 <Static>
  <Code><AbsoluteSegment Id="{SEG}" Address="256" Size="32"><Data>AAABLA==</Data></AbsoluteSegment></Code>
  <ParameterTypes>
   <ParameterType Id="{APP}_PT-mode"><TypeRestriction Base="Value" SizeInBit="4">
     <Enumeration Id="{APP}_PT-mode_EN-0" Text="Schalten" Value="0"/>
     <Enumeration Id="{APP}_PT-mode_EN-1" Text="Treppenlicht" Value="1"/></TypeRestriction></ParameterType>
   <ParameterType Id="{APP}_PT-s8"><TypeNumber SizeInBit="8" Type="signedInt"/></ParameterType>
   <ParameterType Id="{APP}_PT-u16"><TypeNumber SizeInBit="16" Type="unsignedInt"/></ParameterType>
   <ParameterType Id="{APP}_PT-f9"><TypeFloat Encoding="DPT 9"/></ParameterType>
   <ParameterType Id="{APP}_PT-txt"><TypeText SizeInBit="64"/></ParameterType>
   <ParameterType Id="{APP}_PT-bool"><TypeRestriction Base="Value" SizeInBit="1">
     <Enumeration Id="{APP}_PT-bool_EN-0" Text="nein" Value="0"/>
     <Enumeration Id="{APP}_PT-bool_EN-1" Text="ja" Value="1"/></TypeRestriction></ParameterType>
  </ParameterTypes>
  <Parameters>
   {p(1, "mode", "Betriebsart", 0, offset=0, bit=4)}
   {p(2, "u16", "Treppenlichtzeit", 60, offset=2)}
   {p(3, "s8", "Korrektur", 0, offset=4)}
   {p(4, "f9", "Schwelle", 0, offset=6)}
   {p(5, "txt", "Name", "", offset=8)}
   {p(6, "bool", "Erweitert anzeigen", 0)}
   {p(7, "u16", "", 1, offset=16, access="Read")}
   {p(9, "u16", "", 7, offset=20, access="Read")}
   <Union SizeInBit="8"><Memory CodeSegment="{SEG}" Offset="18" BitOffset="0"/>
     <Parameter Id="{APP}_UP-8" Name="u8" ParameterType="{APP}_PT-s8" Text="Union A" Value="0" Offset="0" BitOffset="0"/>
   </Union>
  </Parameters>
  <ParameterRefs>
   {''.join(f'<ParameterRef Id="{APP}_P-{i}_R-{i}" RefId="{APP}_P-{i}"/>' for i in (1, 2, 3, 4, 5, 6, 7, 9))}
   <ParameterRef Id="{APP}_UP-8_R-8" RefId="{APP}_UP-8"/>
  </ParameterRefs>
 </Static>
 <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Text="Kanal A">
   <ParameterRefRef RefId="{APP}_P-1_R-1"/>
   <choose ParamRefId="{APP}_P-1_R-1"><when test="1"><ParameterRefRef RefId="{APP}_P-2_R-2"/></when></choose>
   <ParameterRefRef RefId="{APP}_P-3_R-3"/>
   <ParameterRefRef RefId="{APP}_P-4_R-4"/>
   <ParameterRefRef RefId="{APP}_P-5_R-5"/>
   <ParameterRefRef RefId="{APP}_UP-8_R-8"/>
   <choose ParamRefId="{APP}_P-6_R-6">
     <when test="0"><ParameterRefRef RefId="{APP}_P-7_R-7"/></when>
     <when test="1"><ParameterRefRef RefId="{APP}_P-3_R-3"/><ParameterRefRef RefId="{APP}_P-9_R-9"/></when>
   </choose>
 </ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def setup(tmp_path: Path, memory: dict[int, int]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    d = tmp_path / "prod" / "M-0008"
    d.mkdir(parents=True)
    (d / f"{APP}.xml").write_text(XML)
    cat = Catalog()
    cat.add(tmp_path / "prod")
    ap = params.AppParams(cat.apps["M-0008_A-2032-12"])

    def mem(addr, n):
        return bytes(memory.get(addr + i, 0) for i in range(n))

    return ap, ap.evaluate(mem, lambda o, i: None, lambda lsm: None)


def by_text(ev):
    return {v.text: v for v in ev.values}


def test_decode_types_and_visibility(tmp_path):
    memory = {
        0x100: 0x01,  # Betriebsart im unteren Nibble (BitOffset 4) = Treppenlicht
        0x102: 0x01, 0x103: 0x2C,  # 300
        0x104: 0xFE,  # -2
        0x106: 0x0C, 0x107: 0x1A,  # DPT 9: 21.0
        0x108: ord("G"), 0x109: ord("a"), 0x10A: ord("r"), 0x10B: 0,
        0x110: 0x00, 0x111: 0x01,  # versteckter Parameter = Standard 1 → Zweig "0" passt
        0x112: 0x05,
    }
    ap, ev = setup(tmp_path, memory)
    v = by_text(ev)
    assert v["Betriebsart"].display == "Treppenlicht"
    assert v["Treppenlichtzeit"].value == 300  # sichtbar, weil Betriebsart = 1
    assert v["Korrektur"].value == -2
    assert v["Schwelle"].value == 21.0
    assert v["Name"].value == "Gar"
    assert v["Union A"].value == 5
    assert all(x.from_device for k, x in v.items() if k)
    assert ap.needed_ranges() == [(SEG, 0, 22)]


def test_hidden_branch_and_ets_only_inference(tmp_path):
    # Betriebsart = Schalten → Treppenlichtzeit unsichtbar
    _, ev = setup(tmp_path, {0x110: 0x00, 0x111: 0x01})
    assert "Treppenlichtzeit" not in by_text(ev)
    # "Erweitert anzeigen" hat keinen Speicher; versteckter P-7 = 1 passt zu Zweig 0
    assert ev.inferred == ["Erweitert anzeigen → Zweig 0"]
    internal = [x for x in ev.values if x.internal]
    assert len(internal) == 1 and internal[0].value == 1  # P-7 aus Zweig 0


def test_inference_needs_positive_evidence(tmp_path):
    # Zweig 0 widerlegt (P-7 ≠ 1), Zweig 1 ohne eigenen Beleg (P-9 ≠ 7) → keine Ableitung
    _, ev = setup(tmp_path, {0x110: 0x00, 0x111: 0x07})
    assert ev.inferred == []
    # Zweig 1 belegt (P-9 = 7) → Zweig 1
    _, ev = setup(tmp_path / "b", {0x110: 0x00, 0x111: 0x07, 0x114: 0x00, 0x115: 0x07})
    assert ev.inferred == ["Erweitert anzeigen → Zweig 1"]


def test_when_conditions():
    assert params._test("1 2 3", 2) and not params._test("1 2 3", 4)
    assert params._test("!=0", 5) and not params._test("!=0", 0)
    assert params._test("<5", 4) and params._test(">=5", 5) and params._test(">2", 3)
    assert params._test("=7", 7)


def test_untouched_default_image_is_flagged(tmp_path):
    # Standardbild (Data) hat an Offset 2/3 den Wert 300; Parameter-Standard ist 60.
    # Steht im Gerät noch 300 aus dem Bild, ist der Wert verdächtig ("nie geschrieben").
    memory = {0x100: 0x01, 0x102: 0x01, 0x103: 0x2C, 0x110: 0x00, 0x111: 0x01}
    _, ev = setup(tmp_path, memory)
    v = by_text(ev)["Treppenlichtzeit"]
    assert v.value == 300 and v.untouched and v.uncertain
    memory[0x103] = 0x2D  # 301: vom Bild verschieden → normal
    _, ev = setup(tmp_path / "b", memory)
    assert not by_text(ev)["Treppenlichtzeit"].untouched


def test_packed_time():
    t = params.PType("time", 24, unit="PackedDaysHoursMinutesAndSeconds")
    assert params.decode_value(t, 0x0A0000) == (10, "00:00:10")
    assert params.decode_value(t, 0x000500) == (300, "00:05:00")
    assert params.decode_value(t, 0x000001) == (3600, "01:00:00")
