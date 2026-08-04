import json

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL = "https://enr.sos.mo.gov/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def scrape():
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
        page.goto(URL)

        page.wait_for_selector("#MainContent_btnElectionType", timeout=30000)

        page.click("#MainContent_btnElectionType")

        try:
            page.wait_for_selector(
                "#MainContent_UpdatePanel1 table", timeout=15000
            )
        except PlaywrightTimeoutError:
            print("No results table found yet -- results may not be posted.")
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

    return races


if __name__ == "__main__":
    races = scrape()

    with open("2026-08-04-missouri-election-results.json", "w") as outfile:
        json.dump(races, outfile, indent=4)
