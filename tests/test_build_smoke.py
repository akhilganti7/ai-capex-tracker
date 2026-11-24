"""Run build_company end to end on a tiny fake SEC response."""
from pipeline.build import build_company, tag_inventory


def fact(start, end, val, form="10-Q"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": "2025-02-01", "accn": "0000-25-1"}


def series(*vals):
    q = [("2024-01-01", "2024-03-31"), ("2024-01-01", "2024-06-30"),
         ("2024-01-01", "2024-09-30"), ("2024-01-01", "2024-12-31")]
    return {"units": {"USD": [fact(s, e, v, "10-K" if e.endswith("12-31") else "10-Q")
                              for (s, e), v in zip(q, vals)]}}


def test_build_company_runs_and_checks_pass():
    facts_json = {"facts": {"us-gaap": {
        "Revenues": series(100, 210, 330, 460),
        "NetCashProvidedByUsedInOperatingActivities": series(40, 85, 125, 170),
        "PaymentsToAcquirePropertyPlantAndEquipment": series(20, 45, 75, 110),
    }}}
    checks = []
    out = build_company("TEST", {"name": "Test", "cik": "0000000001"}, facts_json, checks)
    assert [r["capex"] for r in out["quarters"]] == [20, 25, 30, 35]
    assert out["quarters"][1]["free_cash_flow"] == 45 - 25
    assert all(c["ok"] for c in checks), checks
    assert "PaymentsToAcquirePropertyPlantAndEquipment" in tag_inventory(facts_json)
