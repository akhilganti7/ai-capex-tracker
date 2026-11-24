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


def _tag_units(facts_json: dict, tag: str):
    """Tags are "Name" (standard us-gaap) or "prefix:Name" (company-specific)."""
    ns, name = tag.split(":", 1) if ":" in tag else ("us-gaap", tag)
    return facts_json.get("facts", {}).get(ns, {}).get(name, {}).get("units", {}).get("USD")


def metric_quarters(facts_json: dict, ticker: str, metric: str):
    per_tag, used = [], []
    for tag in config.tags_for(ticker, metric):
        units = _tag_units(facts_json, tag)
        if not units:
            continue
        per_tag.append(parse_facts(units, tag))
        used.append(tag)
    # 1) Each tag on its own, earlier tags win.
    quarters = {}
    for facts in reversed(per_tag):
        quarters.update(to_quarters(facts))
    # 2) Only to fill quarters still missing: mix tags inside one year's chain
    #    (needed when, say, the 9-month total and the full year use different tags).
    merged = merge_tag_facts(per_tag)
    for end, q in to_quarters(merged).items():
        quarters.setdefault(end, q)
    return quarters, used, (per_tag[0] if per_tag else [])


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
    """List capex-like tags (standard and company-specific) so gaps are easy to track down."""
    out = {}
    for ns, tags in facts_json.get("facts", {}).items():
        for tag, body in tags.items():
            if not (("Property" in tag or "ProductiveAssets" in tag or "CapitalExpend" in tag)
                    and ("Payment" in tag or "Purchase" in tag)):
                continue
            facts = [f for f in body.get("units", {}).get("USD", []) if "start" in f]
            if facts:
                name = tag if ns == "us-gaap" else f"{ns}:{tag}"
                out[name] = {"facts": len(facts), "first_end": min(f["end"] for f in facts),
                             "last_end": max(f["end"] for f in facts),
                             "periods": sorted({f'{f["start"]}..{f["end"]}' for f in facts
                                                if f["end"] >= "2019-01-01"})}
    return out


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
