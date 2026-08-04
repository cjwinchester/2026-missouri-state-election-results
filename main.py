import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

STATE_URL = "https://enr.sos.mo.gov/"
BOONE_URL = "https://www.showmeboone.com/clerk/elections/results/unofficial.asp"
TIMEZONE = ZoneInfo("America/Chicago")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

BOONE_PARTY_ABBREVIATIONS = {
    "REP": "Republican",
    "DEM": "Democratic",
    "LIB": "Libertarian",
    "GRN": "Green",
    "CON": "Constitution",
    "BTR": "Better",
    "IND": "Independent",
    "NPA": "Nonpartisan",
}


def scrape_state_results():

    races_to_watch = [
        "STATE AUDITOR",
        "U.S. REPRESENTATIVE - DISTRICT 1",
        "U.S. REPRESENTATIVE - DISTRICT 3",
        "U.S. REPRESENTATIVE - DISTRICT 4"
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined})"
        )
        page = context.new_page()
        page.goto(STATE_URL)

        page.wait_for_selector("#MainContent_btnElectionType", timeout=30000)

        page.click("#MainContent_btnElectionType")

        try:
            page.wait_for_selector(
                "#MainContent_UpdatePanel1 table", timeout=15000
            )
        except PlaywrightTimeoutError:
            print("State: no results table found yet -- results may not be posted.")
            browser.close()
            return []

        panel = page.query_selector("#MainContent_UpdatePanel1")
        html = panel.inner_html() if panel else ""
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "MainContent_dgrdResults"})

    # start a tracking list to hold race-level data
    races = []

    # a reusable dict to keep track of data for each race
    current_office = {}

    for row in table.find_all("tr")[1:]:

        cells = row.find_all("td")

        # skip "totals" lines and blank rows
        row_text = row.text.lower().strip()
        if not row_text or "party total" in row_text or "total votes" in row_text:
            continue

        # the "precincts reported" text is the flag that it's a new record
        if "precincts reported" in row_text:

            # add the most recently scraped race dict to the tracking list
            if current_office:
                races.append(current_office)

            office = cells[0].text.strip()
            precincts = cells[-1].text.split()
            precincts_reported = int(precincts[0])
            precincts_total = int(precincts[2])
            print(f"{office}: {precincts_reported} / {precincts_total}")

            current_office = {
                "office": office,
                "precincts": {
                    "reported": precincts_reported,
                    "total": precincts_total
                },
                "candidates": {}
            }

            if "amendment" in office.lower() or office in races_to_watch:
                current_office["race_to_watch"] = True

            continue

        # the only type of row left has candidate info
        name, party, votes, vote_pct = [x.text.strip() for x in cells]

        # amendments swap the candidate ("NO" or "YES") with party -- need to swip swap
        if "amendment" in current_office["office"].lower():
            name, party = party, name

        if not current_office["candidates"].get(party):
            current_office["candidates"][party] = []

        current_office["candidates"][party].append({
            "name": name,
            "votes": int(votes),
            "vote_pct": vote_pct
        })

    races.append(current_office)

    return races


def parse_boone_votes(text):
    digits = re.sub(r"[^\d-]", "", text)
    return int(digits) if digits else 0


def scrape_boone_county_results():
    response = requests.get(
        BOONE_URL, headers={"User-Agent": USER_AGENT}, timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    container = soup.find("div", id="unoff-elect-results")

    if container is None:
        print("Boone County: no active election results posted yet.")
        return []

    results_div = container.find("div", id="elect-results")

    if results_div is None or not results_div.find("div", class_="elect-topic"):
        print("Boone County: no active election results posted yet.")
        return []

    # county-wide precinct count applies to every race on the page
    precincts_reported, precincts_total = 0, 0
    stats_div = container.find("div", id="elect-stats")
    if stats_div is not None:
        for row in stats_div.find_all("p"):
            label = row.find("span", class_="rpt-stats-col-0")
            value = row.find("span", class_="rpt-stats-col-1")
            if label and value and label.text.strip() == "Precincts Complete":
                match = re.match(r"(\d+)\s+of\s+(\d+)", value.text.strip())
                if match:
                    precincts_reported, precincts_total = (
                        int(match.group(1)),
                        int(match.group(2)),
                    )

    races = []

    for topic in results_div.find_all("div", class_="elect-topic"):
        heading = topic.find("h3")
        if heading is None:
            continue
        office = heading.text.strip()

        rows = []
        total_votes = 0

        for row in topic.find_all("p"):
            if "elect-topic-headings" in (row.get("class") or []):
                continue

            name_span = row.find("span", class_="rpt-col-1")
            votes_span = row.find("span", class_="rpt-col-2")
            if name_span is None or votes_span is None:
                continue

            name = name_span.text.strip()
            votes = parse_boone_votes(votes_span.text)

            if name.lower() in ("total votes cast", "total votes"):
                total_votes = votes
                continue

            # candidate names are prefixed with a party abbreviation, e.g.
            # "REP Donald J. Trump, JD Vance" -- ballot measures (YES/NO)
            # and write-ins have no such prefix
            party = ""
            tokens = name.split(" ", 1)
            if len(tokens) == 2 and tokens[0].isalpha() and tokens[0].isupper():
                abbr, name = tokens
                party = BOONE_PARTY_ABBREVIATIONS.get(abbr, abbr)

            rows.append((party, name, votes))

        if not total_votes:
            total_votes = sum(votes for _, _, votes in rows)

        candidates = {}
        for party, name, votes in rows:
            vote_pct = (
                f"{(votes / total_votes * 100):.3f}%" if total_votes else "0.000%"
            )
            candidates.setdefault(party, []).append({
                "name": name,
                "votes": votes,
                "vote_pct": vote_pct,
            })

        races.append({
            "office": office,
            "precincts": {
                "reported": precincts_reported,
                "total": precincts_total,
            },
            "candidates": candidates,
        })

    return races


if __name__ == "__main__":
    output = {
        "updated": datetime.now(TIMEZONE).isoformat(),
        "sources": {
            "state": {
                "label": "Statewide",
                "races": scrape_state_results(),
            },
            "boone": {
                "label": "Boone County",
                "races": scrape_boone_county_results(),
            },
        },
    }

    with open("2026-08-04-missouri-election-results.json", "w") as outfile:
        json.dump(output, outfile, indent=2)
