from datetime import date

from pipeline.quarters import calendar_quarter, merge_tags, parse_facts, to_quarters


def f(start, end, val, form="10-Q", filed="2025-01-01", accn="a"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed, "accn": accn}


def test_ytd_cash_flow_becomes_single_quarters():
    # Calendar-year company, cash flow only reported year-to-date.
    facts = parse_facts([
        f("2024-01-01", "2024-03-31", 20),
        f("2024-01-01", "2024-06-30", 45),
        f("2024-01-01", "2024-09-30", 75),
        f("2024-01-01", "2024-12-31", 110, form="10-K"),
    ])
    q = to_quarters(facts)
    assert [q[d].value for d in sorted(q)] == [20, 25, 30, 35]
    assert q[date(2024, 12, 31)].method == "derived"
    assert q[date(2024, 3, 31)].method == "direct"


def test_direct_three_month_number_wins_and_restatement_uses_latest_filing():
    facts = parse_facts([
        f("2024-01-01", "2024-06-30", 45),
        f("2024-01-01", "2024-03-31", 20),
        f("2024-04-01", "2024-06-30", 26, filed="2024-08-01"),
        f("2024-04-01", "2024-06-30", 27, filed="2025-08-01"),  # restated later
    ])
    q = to_quarters(facts)
    assert q[date(2024, 6, 30)].value == 27


def test_nvidia_style_52_53_week_quarters():
    # Fiscal year starting late January, 13- and 14-week quarters.
    facts = parse_facts([
        f("2024-01-29", "2024-04-28", 100),
        f("2024-01-29", "2024-07-28", 250),
        f("2024-01-29", "2024-10-27", 400),
        f("2024-01-29", "2025-01-26", 600, form="10-K"),
    ])
    q = to_quarters(facts)
    assert [q[d].value for d in sorted(q)] == [100, 150, 150, 200]
    labels = [calendar_quarter(q[d].start, q[d].end) for d in sorted(q)]
    assert labels == ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]


def test_fiscal_year_ends_line_up_by_calendar_quarter():
    # Microsoft Jul-Sep and Oracle Jun-Aug both count as calendar Q3.
    assert calendar_quarter(date(2025, 7, 1), date(2025, 9, 30)) == "2025Q3"
    assert calendar_quarter(date(2025, 6, 1), date(2025, 8, 31)) == "2025Q3"
    assert calendar_quarter(date(2025, 12, 1), date(2026, 2, 28)) == "2026Q1"


def test_merge_prefers_first_tag():
    a = to_quarters(parse_facts([f("2024-01-01", "2024-03-31", 1)]))
    b = to_quarters(parse_facts([f("2024-01-01", "2024-03-31", 2), f("2023-10-01", "2023-12-31", 9)]))
    m = merge_tags([a, b])
    assert m[date(2024, 3, 31)].value == 1
    assert m[date(2023, 12, 31)].value == 9
