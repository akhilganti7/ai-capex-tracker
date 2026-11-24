"""Download SEC company facts and build data/quarterly.json + data/checks.json.

Run:  python -m pipeline.build
Needs the SEC_USER_AGENT environment variable, e.g. "Your Name your@email.com".
The SEC asks every automated request to say who is calling.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline import config
from pipeline.quarters import annual_facts, calendar_quarter, merge_tag_facts, parse_facts, to_quarters

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = ROOT / ".cache" / "sec"

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        sys.exit("Set SEC_USER_AGENT (your name and email). The SEC blocks requests without it.")
    return ua


def fetch_company_facts(cik: str) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    cache = RAW / f"CIK{cik}.json"
    if cache.exists() and os.environ.get("USE_CACHE") == "1":
        return json.loads(cache.read_text())
    req = urllib.request.Request(
        FACTS_URL.format(cik=cik),
        headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    cache.write_bytes(body)
    time.sleep(0.2)  # SEC limit is 10 requests/second; stay well under
    return json.loads(body)


def metric_quarters(facts_json: dict, ticker: str, metric: str):
    gaap = facts_json.get("facts", {}).get("us-gaap", {})
    per_tag, used = [], []
    for tag in config.tags_for(ticker, metric):
        units = gaap.get(tag, {}).get("units", {}).get("USD")
        if not units:
            continue
        per_tag.append(parse_facts(units, tag))
        used.append(tag)
    facts = merge_tag_facts(per_tag)
    return to_quarters(facts), used, facts


def filing_link(cik: str, accn: str) -> str:
    return FILING_URL.format(cik=int(cik), accn=accn.replace("-", ""))


def build_company(ticker: str, info: dict, facts_json: dict, checks: list) -> dict:
    cutoff = date.fromisoformat(config.START_DATE)
    metrics, tags_used = {}, {}
    for metric in config.DEFAULT_TAGS:
        q, used, all_facts = metric_quarters(facts_json, ticker, metric)
        metrics[metric] = {d: v for d, v in q.items() if d >= cutoff}
        tags_used[metric] = used
        if metric in ("revenue", "operating_cash_flow", "capex"):
            check_annual_tieout(ticker, metric, q, all_facts, checks)

    # One row per quarter where we have capex (the core number).
    rows = []
    for end in sorted(metrics["capex"]):
        cq = metrics["capex"][end]
        row = {
            "period": calendar_quarter(cq.start, cq.end),
            "start": cq.start.isoformat(),
            "end": end.isoformat(),
            "sources": {},
        }
        for metric, series in metrics.items():
            q = series.get(end)
            row[metric] = q.value if q else None
            if q:
                row["sources"][metric] = {
                    "method": q.method,
                    "form": q.form,
                    "filed": q.filed,
                    "tag": q.tag,
                    "url": filing_link(info["cik"], q.accn),
                }
        ocf, capex, rev = row["operating_cash_flow"], row["capex"], row["revenue"]
        row["free_cash_flow"] = ocf - capex if ocf is not None else None
        row["reinvestment_rate"] = capex / ocf if ocf else None
        row["capex_to_revenue"] = capex / rev if rev else None
        rows.append(row)

    check_completeness(ticker, rows, checks)
    return {**info, "tags_used": tags_used, "quarters": rows}


def tag_inventory(facts_json: dict) -> dict:
    """List capex-like tags a company has, so missing quarters are easy to track down."""
    out = {}
    for tag, body in facts_json.get("facts", {}).get("us-gaap", {}).items():
        if not any(k in tag for k in ("PropertyPlant", "ProductiveAssets", "CapitalExpend")):
            continue
        if not tag.startswith(("Payments", "Purchase")):
            continue
        ends = [f["end"] for f in body.get("units", {}).get("USD", []) if "start" in f]
        if ends:
            out[tag] = {"facts": len(ends), "first_end": min(ends), "last_end": max(ends)}
    return out


def check_annual_tieout(ticker, metric, quarters, facts, checks):
    """The 4 quarters of a fiscal year should add up to the 10-K full-year number."""
    for fy in annual_facts(facts):
        if fy.end < date.fromisoformat(config.START_DATE):
            continue
        qs = [q for d, q in quarters.items() if fy.start <= q.start and q.end <= fy.end]
        if len(qs) != 4:
            checks.append({"ticker": ticker, "check": "annual_tieout", "metric": metric,
                           "year_end": fy.end.isoformat(), "ok": False,
                           "detail": f"found {len(qs)} quarters, expected 4"})
            continue
        total = sum(q.value for q in qs)
        diff = abs(total - fy.value) / abs(fy.value) if fy.value else 0
        checks.append({"ticker": ticker, "check": "annual_tieout", "metric": metric,
                       "year_end": fy.end.isoformat(), "ok": diff < 0.005,
                       "quarters_sum": total, "annual": fy.value})


def check_completeness(ticker, rows, checks):
    """No missing quarters, and no doubled-up calendar quarters."""
    periods = [r["period"] for r in rows]
    dupes = sorted({p for p in periods if periods.count(p) > 1})
    gaps = []
    for a, b in zip(rows, rows[1:]):
        if (date.fromisoformat(b["start"]) - date.fromisoformat(a["end"])).days > 1:
            gaps.append(f'{a["end"]} -> {b["start"]}')
    negatives = [r["period"] for r in rows if r["capex"] is not None and r["capex"] <= 0]
    checks.append({"ticker": ticker, "check": "completeness",
                   "ok": not dupes and not gaps and not negatives,
                   "quarters": len(rows), "first": periods[0] if periods else None,
                   "last": periods[-1] if periods else None,
                   "duplicate_periods": dupes, "gaps": gaps, "non_positive_capex": negatives})


def main() -> None:
    DATA.mkdir(exist_ok=True)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "units": "USD", "companies": {}}
    checks: list = []
    inventory: dict = {}
    for ticker, info in config.COMPANIES.items():
        print(f"Fetching {ticker} ...", flush=True)
        facts_json = fetch_company_facts(info["cik"])
        out["companies"][ticker] = build_company(ticker, info, facts_json, checks)
        inventory[ticker] = tag_inventory(facts_json)

    (DATA / "quarterly.json").write_text(json.dumps(out, indent=1))
    (DATA / "tag_inventory.json").write_text(json.dumps(inventory, indent=1))
    failed = [c for c in checks if not c["ok"]]
    (DATA / "checks.json").write_text(json.dumps(
        {"generated_at": out["generated_at"], "passed": len(checks) - len(failed),
         "failed": len(failed), "checks": checks}, indent=1))

    for t, c in out["companies"].items():
        last = c["quarters"][-1] if c["quarters"] else {}
        print(f'{t}: {len(c["quarters"])} quarters, latest {last.get("period")} '
              f'capex={last.get("capex")} tags={c["tags_used"]["capex"]}')
    print(f"Checks: {len(checks) - len(failed)} passed, {len(failed)} failed")
    for c in failed:
        print("  FAILED:", json.dumps(c))


if __name__ == "__main__":
    main()
