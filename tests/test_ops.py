"""Projektbefehle: Gruppenadressen, Bereiche, Verknüpfungen, Geräte, Rückgängig."""

import pytest

from knxlens.project import ComLink, Device, GroupAddress, GroupRange, Line, Project
from knxlens.project.ops import Editor, OpError


@pytest.fixture
def ed():
    p = Project(name="T", project_id="P-0001")
    p.lines = [Line(1, 1), Line(1, 2)]
    p.ranges = [GroupRange("R1", "Licht", 2048, 4095), GroupRange("R11", "Schalten", 2304, 2559, "R1"),
                GroupRange("R12", "Status", 2560, 2815, "R1")]
    for a in ("1/1/1", "1/1/2"):
        p.group_addresses[a] = GroupAddress(a, f"GA {a}", "DPST-1-1", range_uid="R11", extra={"Id": f"P-0001-0_GA-{a[-1]}"})
    p.devices = [Device("P-0001-0_DI-1", 1, 1, 5, "P", "H", "M-0008_A-2032-12-ABCD", name="Taster",
                        params={"x_P-1_R-1": "3"}, links=[ComLink("O-0_R-1", ["1/1/1", "1/1/2"], 0)])]
    return Editor(p)


def test_ga_add_next_free_and_rules(ed):
    assert ed.ga_add("R11", "Neu") == "1/1/3"
    assert ed.ga_add("R11", address="1/1/9") == "1/1/9"
    with pytest.raises(OpError, match="schon vergeben"):
        ed.ga_add("R11", address="1/1/1")
    with pytest.raises(OpError, match="Mittelgruppe"):
        ed.ga_add("R1")
    with pytest.raises(OpError, match="liegt nicht"):
        ed.ga_add("R11", address="1/2/1")


def test_move_carries_links_and_undo(ed):
    mapping = ed.ga_move(["1/1/2"], "R12")
    assert mapping == {"1/1/2": "1/2/1"}
    assert ed.project.devices[0].links[0].gas == ["1/1/1", "1/2/1"]
    assert ed.project.group_addresses["1/2/1"].range_uid == "R12"
    assert ed.undo() == "Gruppenadressen verschieben"
    assert ed.project.devices[0].links[0].gas == ["1/1/1", "1/1/2"]
    assert "1/2/1" not in ed.project.group_addresses
    ed.redo()
    assert "1/2/1" in ed.project.group_addresses


def test_readdress_and_delete(ed):
    ed.ga_readdress("1/1/1", "1/2/7")
    assert ed.project.devices[0].links[0].gas == ["1/2/7", "1/1/2"]
    with pytest.raises(OpError, match="keine Mittelgruppe"):
        ed.ga_readdress("1/2/7", "5/0/1")
    assert ed.ga_delete(["1/2/7", "1/1/2"]) == 2
    assert ed.project.devices[0].links == []  # leere Verknüpfung entfernt


def test_link_unlink_sending(ed):
    ed.ga_add("R11", "C")
    ed.link("P-0001-0_DI-1", "O-0_R-1", "1/1/3", sending=True)
    assert ed.project.devices[0].links[0].gas == ["1/1/3", "1/1/1", "1/1/2"]
    ed.set_sending("P-0001-0_DI-1", "O-0_R-1", "1/1/2")
    assert ed.project.devices[0].links[0].gas[0] == "1/1/2"
    ed.link("P-0001-0_DI-1", "O-2_R-4", "1/1/1", number=2)
    assert [l.ref for l in ed.project.devices[0].links] == ["O-0_R-1", "O-2_R-4"]
    ed.unlink("P-0001-0_DI-1", "O-2_R-4", "1/1/1")
    assert [l.ref for l in ed.project.devices[0].links] == ["O-0_R-1"]
    with pytest.raises(OpError):
        ed.link("P-0001-0_DI-1", "O-0_R-1", "9/9/9")


def test_ranges(ed):
    main = ed.range_add(None, "Jalousie")
    assert next(r for r in ed.project.ranges if r.uid == main).start == 0 + 1  # Hauptgruppe 0 beginnt bei 1
    mid = ed.range_add("R1")
    assert next(r for r in ed.project.ranges if r.uid == mid).start == (1 << 11)  # 1/1 und 1/2 belegt → 1/0
    with pytest.raises(OpError, match="enthält 2"):
        ed.range_delete("R1")
    assert ed.range_delete("R11", force=True) == 2


def test_devices(ed):
    ed.device_set_pa("P-0001-0_DI-1", "1.1.7")
    assert ed.project.devices[0].pa == "1.1.7"
    with pytest.raises(OpError, match="existiert nicht"):
        ed.device_set_pa("P-0001-0_DI-1", "3.3.3")
    new = ed.device_copy(["P-0001-0_DI-1"], 1, 1)
    copy = next(d for d in ed.project.devices if d.uid == new[0])
    assert copy.pa == "1.1.1" and copy.params == {"x_P-1_R-1": "3"} and copy.links == [] and copy.origin == "neu"
    with pytest.raises(OpError, match="schon vergeben"):
        ed.device_set_pa(new[0], "1.1.7")
    ed.device_move([new[0]], 1, 2)
    assert copy.uid in [d.uid for d in ed.project.devices if (d.area, d.line) == (1, 2)]
    uid = ed.device_insert("P2", "H2", "M-0008_A-1111-11-0000", 1, 2, "Neu")
    assert next(d for d in ed.project.devices if d.uid == uid).pa == "1.2.2"
    ed.device_delete([uid])
    assert all(d.uid != uid for d in ed.project.devices)


def test_failed_op_changes_nothing(ed):
    before = len(ed.undo_stack)
    with pytest.raises(OpError):
        ed.ga_move(["1/1/1"], "R1")  # Hauptgruppe ist kein gültiges Ziel
    assert len(ed.undo_stack) == before
    assert ed.project.devices[0].links[0].gas == ["1/1/1", "1/1/2"]


def test_areas_and_lines(ed):
    assert ed.line_add(1) == 3 and ed.line_add(1, 7, "Garage", "MT-5") == 7
    with pytest.raises(OpError, match="existiert bereits"):
        ed.line_add(1, 7)
    a = ed.area_add(name="Nebengebäude")
    assert a == 2 and [(l.area, l.line) for l in ed.project.lines if l.area == 2] == [(2, 0)]
    ed.line_add(2)
    lines = {(l.area, l.line): l for l in ed.project.lines}
    assert lines[2, 1].extra["area"] == lines[2, 0].extra["area"]  # gleiche Bereichs-Attribute
    ids = [l.extra["line"]["Id"] for l in ed.project.lines if l.extra]
    assert len(ids) == len(set(ids))
    ed.area_rename(2, "Garage")
    assert {l.extra["area"]["Name"] for l in ed.project.lines if l.area == 2} == {"Garage"}
    ed.line_update(1, 7, name="Carport", medium="MT-0")
    assert lines[1, 7].name == "Carport" and lines[1, 7].medium == "MT-0"
    with pytest.raises(OpError, match="Bereichslinie"):
        ed.line_delete(2, 0)
    with pytest.raises(OpError, match="1 Gerät"):
        ed.line_delete(1, 1)
    assert ed.line_delete(1, 1, force=True) == 1 and not ed.project.devices
    assert ed.area_delete(2) == 0 and all(l.area != 2 for l in ed.project.lines)
    ed.undo(); ed.undo()
    assert ed.project.devices and any(l.area == 2 for l in ed.project.lines)


def test_buildings(ed):
    b = ed.space_add(None, "Building", "Haus")
    with pytest.raises(OpError, match="nicht erlaubt"):
        ed.space_add(None, "Room")
    f = ed.space_add(b, "Floor", "EG")
    r = ed.space_add(f, "Room", "Küche")
    v = ed.space_add(b, "DistributionBoard")
    assert ed._space(v).name == "Verteiler"
    with pytest.raises(OpError, match="in sich selbst"):
        ed.space_move([f], r)
    with pytest.raises(OpError, match="nicht erlaubt"):
        ed.space_move([f], v)
    ed.space_move([v], f)
    assert [s.uid for s in ed.project.spaces] == [b, f, r, v]  # Eltern vor Kindern
    with pytest.raises(OpError, match="nicht in „Haus“"):
        ed.space_assign(["P-0001-0_DI-1"], b)
    ed.space_assign(["P-0001-0_DI-1"], r)
    ed.space_assign(["P-0001-0_DI-1"], v)  # ein Gerät hat genau einen Ort
    assert ed._space(r).devices == [] and ed._space(v).devices == ["P-0001-0_DI-1"]
    ed.space_update(r, name="Wohnküche")
    with pytest.raises(OpError, match="passt nicht"):
        ed.space_update(f, type="Room")  # der Raum darunter passt nicht unter einen Raum
    assert ed.space_delete([f]) == 3 and [s.uid for s in ed.project.spaces] == [b]
    assert ed.project.devices  # Geräte bleiben
    ed.undo()
    ed.space_unassign(["P-0001-0_DI-1"])
    assert not any(s.devices for s in ed.project.spaces)


def test_download_flags_follow_changes(ed):
    from knxlens.project.ops import LOAD_FLAGS, load_state, needs_download
    d = ed.project.devices[0]
    d.extra.update({attr: "true" for attr in LOAD_FLAGS.values()})
    assert not needs_download(d)
    ed.ga_readdress("1/1/2", "1/1/9")  # Verknüpfung ändert sich → Grp
    assert load_state(d) == {"adr": True, "prg": True, "par": True, "grp": False, "cfg": True}
    ed.device_set_pa(d.uid, "1.1.7")
    assert not load_state(d)["adr"] and load_state(d)["par"]
    ed.undo(); ed.undo()
    assert not needs_download(ed.project.devices[0])  # Rückgängig stellt die Kennzeichen wieder her
    new = ed.device_copy([d.uid], 1, 2)[0]
    assert not any(load_state(ed._device(new)).values())
