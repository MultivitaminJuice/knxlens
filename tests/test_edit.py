"""Parameter bearbeiten: Kodierung, Prüfung, Setzen/Zurücksetzen mit Sichtbarkeit."""

import pytest

from knxlens import params
from knxlens.project import Device
from knxlens.project.edit import EditError, editor_item, set_param
from test_params import APP, setup


def dev():
    return Device(uid="d1", area=1, line=1, address=5, product_ref="", h2p_ref="", app_id=APP)


def test_encode_is_inverse_of_decode():
    cases = [
        (params.PType("enum", 4), 1),
        (params.PType("number", 8, signed=True), -2),
        (params.PType("number", 16), 300),
        (params.PType("float", 16, encoding="DPT 9"), 21.0),
        (params.PType("float", 16, encoding="DPT 9"), -30.0),
        (params.PType("float", 32, encoding="IEEE-754 Single"), 0.5),
        (params.PType("time", 24, unit="PackedDaysHoursMinutesAndSeconds"), 3725),
        (params.PType("text", 64), "Gar"),
        (params.PType("text", 64), "Kino "),  # Leerzeichen am Ende bleibt
    ]
    for t, value in cases:
        raw = params.encode_value(t, value)
        assert params.decode_value(t, raw)[0] == value, (t, value, raw)


def test_set_and_reset_changes_visibility(tmp_path):
    ap, _ = setup(tmp_path, {})
    d = dev()
    ev = ap.evaluate_values(d.params)
    texts = {v.text for v in ev.values}
    assert "Treppenlichtzeit" not in texts  # Betriebsart Standard = Schalten
    mode = next(v for v in ev.values if v.text == "Betriebsart")
    assert set_param(ap, d, mode.instance_id, "1") is True
    assert d.params == {mode.instance_id: "1"}
    ev = ap.evaluate_values(d.params)
    stl = next(v for v in ev.values if v.text == "Treppenlichtzeit")
    item = editor_item(ap, stl)
    assert item["kind"] == "number" and item["editable"] and item["max"] == 65535
    set_param(ap, d, stl.instance_id, "120")
    assert d.params[stl.instance_id] == "120"
    # Standardwert wird nicht gespeichert (wie in der ETS)
    set_param(ap, d, stl.instance_id, "60")
    assert stl.instance_id not in d.params
    # Zurücksetzen der Betriebsart blendet die Treppenlichtzeit wieder aus
    set_param(ap, d, mode.instance_id, None)
    assert d.params == {}
    with pytest.raises(EditError, match="nicht sichtbar"):
        set_param(ap, d, stl.instance_id, "100")


def test_validation_errors(tmp_path):
    ap, _ = setup(tmp_path, {})
    d = dev()
    ev = ap.evaluate_values(d.params)
    by = {v.text: v for v in ev.values}
    with pytest.raises(EditError, match="Kein gültiger Wert"):
        set_param(ap, d, by["Betriebsart"].instance_id, "9")
    with pytest.raises(EditError, match="Erlaubt"):
        set_param(ap, d, by["Korrektur"].instance_id, "200")  # 8 Bit vorzeichenbehaftet
    with pytest.raises(EditError, match="Höchstens 8"):
        set_param(ap, d, by["Name"].instance_id, "x" * 9)
    internal = next(v for v in ev.values if v.internal)
    with pytest.raises(EditError, match="nicht änderbar"):
        set_param(ap, d, internal.instance_id, "3")
    assert d.params == {}  # fehlgeschlagene Änderungen hinterlassen nichts


def test_orphans_and_rebind():
    from types import SimpleNamespace
    from knxlens.project import ComLink, Device
    from knxlens.project.edit import orphans, rebind_variants, unlink_orphans
    full = "M-0008_A-1-1-AAAA"
    app = SimpleNamespace(full_id=full, ref_number={f"{full}_O-0_R-1": 0, f"{full}_O-0_R-2": 0, f"{full}_O-1_R-3": 1},
                          variant_by_ref={})
    ev = SimpleNamespace(active_cos={0: f"{full}_O-0_R-2"})  # Objekt 0 in anderer Variante aktiv, 1 inaktiv
    ap = SimpleNamespace(app=app, evaluate_values=lambda values: ev)
    d = Device("D", 1, 1, 1, "P", "H", links=[ComLink("O-0_R-1", ["1/1/1"], 0), ComLink("O-1_R-3", ["1/1/2"], 1)])
    assert [o["number"] for o in orphans(ap, d)] == [1]
    assert rebind_variants(ap, d) == 1 and d.links[0].ref == "O-0_R-2"  # GA wandert mit
    assert unlink_orphans(ap, d) == 1 and [l.ref for l in d.links] == ["O-0_R-2"]
