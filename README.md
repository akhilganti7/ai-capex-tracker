# AI Capex Payback Tracker

Is Big Tech's AI spending paying off? This project compares what Microsoft, Alphabet,
Amazon, Meta and Oracle spend on capex (data centers and chips) with the cash they bring
in, next to Nvidia's revenue from selling them the chips. Every number comes from SEC filings.

## How the data works

1. A GitHub Actions workflow (`.github/workflows/update-data.yml`) runs every weekday.
2. `pipeline/build.py` downloads each company's numbers from the SEC's XBRL "company facts" API.
3. `pipeline/quarters.py` turns year-to-date cash flow numbers into single quarters
   (Q3 = 9-month total minus 6-month total) and lines up different fiscal years by calendar quarter.
4. Output:
   - `data/quarterly.json` — revenue, operating cash flow, capex, free cash flow,
     reinvestment rate and capex/revenue per quarter, each with a link to the source filing.
   - `data/checks.json` — automatic checks (four quarters add up to the 10-K full year,
     no missing quarters).
5. `data/manual/segments.csv` — numbers the SEC API doesn't have (cloud revenue, Nvidia data
   center revenue), entered by hand from earnings releases with a source link for each.

## Formulas

- Free cash flow = operating cash flow − capex
- Reinvestment rate = capex ÷ operating cash flow (over 100% means free cash flow is negative)
- Capex ÷ revenue = share of sales going into building

Caveat: companies don't report "AI capex" on its own, so total capex stands in for it.

## Running locally

```
SEC_USER_AGENT="Your Name you@example.com" python -m pipeline.build
python -m pytest
```
