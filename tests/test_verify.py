"""Regressionsprüfung: Vergleich je Gerät und Kennzahl."""

from knxlens.verify import compare, totals


def _m(params=(10, 0), sim=(5, 1), error=None):
    m = {"params": {"equal": params[0], "diff": params[1]}, "sim": {"equal": sim[0], "diff": sim[1]}}
    if error:
        m["sim"]["error"] = error
    return m


def test_compare_per_device():
    old = {"1.1.1": _m(), "1.1.2": _m()}
    assert compare(old, {"1.1.1": _m(), "1.1.2": _m()}) == ([], [])
    worse, better = compare(old, {"1.1.1": _m(params=(9, 1)), "1.1.2": _m(params=(11, 0))})
    assert worse == ["1.1.1 params: 10/0 → 9/1 (gleich/abweichend)"]  # Summe gleich, trotzdem erkannt
    assert better == ["1.1.2 params: 10/0 → 11/0"]
    worse, _ = compare(old, {"1.1.1": _m(error="Prüfung fehlgeschlagen"), "1.1.2": _m()})
    assert any("Abbruch" in w for w in worse)
    worse, _ = compare(old, {"1.1.1": _m()})
    assert worse == ["1.1.2: fehlt in der Messung"]


def test_totals_counts_errors():
    t = totals({"a": _m(), "b": _m(error="x")})
    assert t["params"] == {"equal": 20, "diff": 0, "errors": 0} and t["sim"]["errors"] == 1
