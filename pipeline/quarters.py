"""Turn raw SEC facts into clean single-quarter numbers.

The tricky part: cash flow numbers in 10-Q reports are year-to-date (YTD).
A Q3 report says "capex for the first 9 months", not "capex for Q3".
So to get one quarter we subtract the previous YTD total:

    Q3 = (9-month YTD) - (6-month YTD)
    Q4 = (full-year from the 10-K) - (9-month YTD)

Income statement items like revenue usually DO have a direct 3-month number,
which we prefer when it exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

QUARTER_MIN_DAYS = 80  # 13-week quarter is 91 days; 14-week (53-week years) is 98
QUARTER_MAX_DAYS = 100


@dataclass
class Fact:
    start: date
    end: date
    value: float
    form: str
    accn: str
    filed: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass
class Quarter:
    start: date
    end: date
    value: float
    method: str  # "direct" (3-month number reported) or "derived" (YTD subtraction)
    accn: str  # filing the number (or the later YTD number) came from
    form: str
    filed: str


def _is_quarter(days: int) -> bool:
    return QUARTER_MIN_DAYS <= days <= QUARTER_MAX_DAYS


def parse_facts(units_usd: list[dict]) -> list[Fact]:
    """Keep duration facts only, and for each (start, end) keep the most recently
    filed value, so restated numbers replace old ones."""
    best: dict[tuple[str, str], dict] = {}
    for f in units_usd:
        if "start" not in f or "end" not in f:
            continue
        if not f.get("form", "").startswith(("10-Q", "10-K")):
            continue
        key = (f["start"], f["end"])
        if key not in best or f.get("filed", "") > best[key].get("filed", ""):
            best[key] = f
    return [
        Fact(
            start=date.fromisoformat(f["start"]),
            end=date.fromisoformat(f["end"]),
            value=float(f["val"]),
            form=f.get("form", ""),
            accn=f.get("accn", ""),
            filed=f.get("filed", ""),
        )
        for f in best.values()
    ]


def to_quarters(facts: list[Fact]) -> dict[date, Quarter]:
    """Return single quarters keyed by quarter end date."""
    direct: dict[date, Quarter] = {}
    derived: dict[date, Quarter] = {}

    # Direct 3-month numbers.
    for f in facts:
        if _is_quarter(f.days):
            direct[f.end] = Quarter(f.start, f.end, f.value, "direct", f.accn, f.form, f.filed)

    # YTD numbers share the same start date (the fiscal year start).
    # Within a group, subtract each total from the next one.
    by_start: dict[date, list[Fact]] = {}
    for f in facts:
        by_start.setdefault(f.start, []).append(f)
    for group in by_start.values():
        group.sort(key=lambda x: x.end)
        for prev, cur in zip(group, group[1:]):
            q_start = prev.end + timedelta(days=1)
            if _is_quarter((cur.end - q_start).days + 1):
                derived[cur.end] = Quarter(
                    q_start, cur.end, cur.value - prev.value, "derived", cur.accn, cur.form, cur.filed
                )

    out = dict(derived)
    out.update(direct)  # direct wins when both exist
    return out


def merge_tags(per_tag: list[dict[date, Quarter]]) -> dict[date, Quarter]:
    """per_tag is ordered by preference; earlier tags win for the same quarter."""
    out: dict[date, Quarter] = {}
    for quarters in reversed(per_tag):
        out.update(quarters)
    return out


def calendar_quarter(start: date, end: date) -> str:
    """Label a quarter by the calendar quarter its middle day falls in.

    Nvidia's quarter Aug-Oct has its middle in September, so it counts as Q3.
    Microsoft's Jul-Sep quarter is also Q3. That lines everyone up.
    """
    mid = start + (end - start) / 2
    return f"{mid.year}Q{(mid.month - 1) // 3 + 1}"


def annual_facts(facts: list[Fact]) -> list[Fact]:
    """Full-year numbers (from 10-Ks), used to double-check our quarters."""
    return [f for f in facts if 350 <= f.days <= 380 and f.form.startswith("10-K")]
