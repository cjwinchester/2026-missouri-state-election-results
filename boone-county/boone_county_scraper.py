#!/usr/bin/env python3
"""Scrape Boone County, MO unofficial election results.

Fetches https://www.showmeboone.com/clerk/elections/results/unofficial.asp,
parses every race, candidate/choice, and current vote count on the page, and
logs a timestamped snapshot. The page only has content in the results section
while an election is being tallied; at other times the script exits with a
clear message instead of a stack trace.

Run it once for a single snapshot, or re-run it repeatedly on election night
(e.g. from cron or a loop) to build a time series of how the count changed:

    python3 boone_county_scraper.py

Each run:
  - appends every (race, candidate, votes) row to boone_county_results_log.csv
  - appends every (stat, value) row (turnout, precincts, etc.) to
    boone_county_stats_log.csv
  - overwrites boone_county_results_latest.csv with just the latest snapshot
  - prints the latest snapshot to stdout

All outputs are written next to this script.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://www.showmeboone.com/clerk/elections/results/unofficial.asp"
TIMEZONE = ZoneInfo("America/Chicago")  # Boone County, MO
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; journalism scraper/1.0)"}

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_LOG = SCRIPT_DIR / "boone_county_results_log.csv"
STATS_LOG = SCRIPT_DIR / "boone_county_stats_log.csv"
LATEST_RESULTS = SCRIPT_DIR / "boone_county_results_latest.csv"


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def parse_votes(text: str) -> int:
    digits = re.sub(r"[^\d-]", "", text)
    return int(digits) if digits else 0


def parse_results(html: str, scraped_at: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", id="unoff-elect-results")
    results_div = container.find("div", id="elect-results") if container else None

    if results_div is None or not results_div.find("div", class_="elect-topic"):
        raise RuntimeError(
            "No active election results found on the page right now "
            "(the county only populates this section during an election)."
        )

    heading = container.find("h2")
    election_name = (
        heading.find("span", class_="elect-results-heading").get_text(strip=True)
        if heading and heading.find("span", class_="elect-results-heading")
        else None
    )
    election_date = (
        heading.find("span", class_="elect-date").get_text(strip=True)
        if heading and heading.find("span", class_="elect-date")
        else None
    )

    # --- Overall stats: precincts complete, registered voters, turnout, etc. ---
    stats_rows = []
    stats_div = container.find("div", id="elect-stats")
    if stats_div is not None:
        for row in stats_div.find_all(["h3", "p"]):
            labels = row.find_all("span", class_="rpt-stats-col-0")
            values = row.find_all("span", class_="rpt-stats-col-1")
            for label, value in zip(labels, values):
                label_text = label.get_text(strip=True)
                if label_text == "Statistics":
                    continue  # column header row, not a stat
                stats_rows.append(
                    {
                        "scraped_at": scraped_at.isoformat(),
                        "election_name": election_name,
                        "election_date": election_date,
                        "stat": label_text,
                        "value": value.get_text(strip=True),
                    }
                )

    # --- Every race and every candidate/choice with its current vote count ---
    result_rows = []
    for topic in results_div.find_all("div", class_="elect-topic"):
        race_name = topic.find("h3").get_text(strip=True)
        for row in topic.find_all("p"):
            if "elect-topic-headings" in (row.get("class") or []):
                continue  # header row ("Vote For 1" / "TOTAL")
            name_span = row.find("span", class_="rpt-col-1")
            votes_span = row.find("span", class_="rpt-col-2")
            if name_span is None or votes_span is None:
                continue
            result_rows.append(
                {
                    "scraped_at": scraped_at.isoformat(),
                    "election_name": election_name,
                    "election_date": election_date,
                    "race": race_name,
                    "candidate": name_span.get_text(strip=True),
                    "votes": parse_votes(votes_span.get_text(strip=True)),
                }
            )

    return pd.DataFrame(result_rows), pd.DataFrame(stats_rows)


def append_csv(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        return
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=URL, help="Results page URL (default: live Boone County page)")
    parser.add_argument(
        "--html-file",
        type=Path,
        help="Parse a saved HTML file instead of fetching the live page (useful for testing)",
    )
    args = parser.parse_args()

    scraped_at = datetime.now(TIMEZONE)

    html = args.html_file.read_text() if args.html_file else fetch_html(args.url)

    try:
        results_df, stats_df = parse_results(html, scraped_at)
    except RuntimeError as exc:
        print(f"[{scraped_at.isoformat()}] {exc}", file=sys.stderr)
        sys.exit(1)

    append_csv(results_df, RESULTS_LOG)
    append_csv(stats_df, STATS_LOG)
    results_df.to_csv(LATEST_RESULTS, index=False)

    print(f"Scraped at {scraped_at.isoformat()} — {len(results_df)} candidate/choice rows across "
          f"{results_df['race'].nunique() if not results_df.empty else 0} races.")
    print(results_df.to_string(index=False))


if __name__ == "__main__":
    main()
