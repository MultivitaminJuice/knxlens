"""Oberflächentests: gebaute Editor-Oberfläche im echten (kopflosen) Browser.

Läuft nur, wenn Playwright und Chromium installiert sind, sonst übersprungen:
    pip install playwright && python -m playwright install chromium-headless-shell
Die Oberfläche kommt aus knxlens/web/static/editor (vorher `npm run build` im Ordner frontend/).
Kein Buszugriff: es wird nur das kleine Testprojekt bearbeitet.
"""

import socket
import threading
import time

import pytest
from test_project import make_knxproj

from knxlens.editor import create_editor_app
from knxlens.editor.state import STATIC
from knxlens.project import Project, products
from knxlens.project.knxproj_io import import_knxproj

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")
pytestmark = [pytest.mark.ui,
              pytest.mark.skipif(not (STATIC / "index.html").exists(), reason="Oberfläche nicht gebaut")]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001 - Browser fehlt
            pytest.skip(f"Chromium für Playwright fehlt: {e}")
        yield b
        b.close()


@pytest.fixture
def editor(tmp_path, monkeypatch):
    """Editor mit frischem Testprojekt auf freiem Port → (URL, Projektdatei)."""
    monkeypatch.setattr(products, "CACHE", tmp_path / "cache")
    from knxlens.editor import state as st
    monkeypatch.setattr(st, "RECENT", tmp_path / "recent.json")
    p = import_knxproj(make_knxproj(tmp_path / "in.knxproj"))
    p.save(tmp_path / "p.knxlens")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_editor_app(str(tmp_path / "p.knxlens")), host="127.0.0.1",
                                        port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/", tmp_path / "p.knxlens"
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def page(browser, editor):
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(editor[0])
    pg.get_by_role("region", name="Topologie").locator(".row").first.wait_for()
    yield pg
    pg.close()
    assert errors == [], f"Fehler in der Browser-Konsole: {errors}"


def row(pg, view: str, text: str):
    return pg.get_by_role("region", name=view).locator(".row", has_text=text).last


def expect():
    return sync_api.expect


def test_start_shows_project_and_views(page):
    expect()(page.get_by_role("banner")).to_contain_text("2 Geräte · 3 GAs · 3 Verknüpfungen")
    for view in ("Topologie", "Gruppenadressen", "Eigenschaften"):
        expect()(page.get_by_role("region", name=view)).to_be_visible()
    expect()(row(page, "Gruppenadressen", "Temperatur")).to_contain_text("1/2/0")
    expect()(page.get_by_role("button", name="Gespeichert")).to_be_disabled()


def test_select_device_and_open_parameters(page):
    row(page, "Topologie", "Taster Flur").click()
    props = page.get_by_role("region", name="Eigenschaften")
    expect()(props.get_by_role("heading", level=3)).to_have_text("1.1.5 Gerät")
    expect()(props.get_by_role("textbox", name="Physikalische Adresse")).to_have_value("1.1.5")
    row(page, "Topologie", "Taster Flur").dblclick()
    dialog = page.get_by_role("dialog")
    expect()(dialog).to_contain_text("1.1.5 Taster Flur")
    page.keyboard.press("Escape")
    expect()(dialog).to_be_hidden()


def test_select_group_address(page):
    row(page, "Gruppenadressen", "Temperatur").click()
    expect()(page.get_by_role("region", name="Eigenschaften")).to_contain_text("1/2/0")


def test_rename_undo_and_save(page, editor):
    row(page, "Topologie", "Taster Flur").click()
    name = page.get_by_role("region", name="Eigenschaften").get_by_role("textbox", name="Name")
    name.fill("Taster Diele")
    name.press("Enter")
    expect()(row(page, "Topologie", "Taster Diele")).to_be_visible()
    undo = page.get_by_role("button", name="↶")
    expect()(undo).to_be_enabled()
    undo.click()
    expect()(row(page, "Topologie", "Taster Flur")).to_be_visible()

    name.fill("Taster Diele")
    name.press("Enter")
    expect()(row(page, "Topologie", "Taster Diele")).to_be_visible()
    save = page.get_by_role("button", name="Speichern", exact=True)
    expect()(save).to_be_enabled()
    save.click()
    expect()(page.get_by_role("button", name="Gespeichert")).to_be_disabled()
    saved = Project.load(editor[1])
    assert any(d.name == "Taster Diele" for d in saved.devices)


def test_switch_view_in_tab(page):
    page.get_by_role("button", name="Topologie ▾").click()
    page.get_by_text("Gebäude", exact=True).last.click()
    expect()(page.get_by_role("region", name="Gebäude")).to_contain_text("Haus")
