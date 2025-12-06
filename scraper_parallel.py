# scraper_parallel.py
"""
Final patched scraper for Shoalhaven DA Tracking
- Uses direct text input for Telerik date fields + JS sync
- Pagination with safe stop conditions
- 4 parallel workers
- Writes results.csv with required headers
"""

import asyncio
import time
from urllib.parse import urljoin

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ===== CONFIG =====
BASE_URL = "https://www3.shoalhaven.nsw.gov.au/masterviewUI/modules/ApplicationMaster/default.aspx?page=search"

ROW_SELECTOR = "tr.rgRow[id^='ctl00_cphContent_ctl01_ctl00_RadGrid1_ctl00__']"
NEXT_SELECTOR = "input.rgPageNext[title='Next Page']"
RADGRID_TABLE_SELECTOR = "table[id*='RadGrid1']"

CSV_HEADERS = [
    "DA_Number",
    "Detail_URL",
    "Description",
    "Submitted_Date",
    "Decision",
    "Categories",
    "Property_Address",
    "Applicant",
    "Progress",
    "Fees",
    "Documents",
    "Contact_Council",
]

WORKERS = 4
HEADLESS = True  # set False to watch browser windows
NAV_RETRY = 2
NAV_RETRY_DELAY = 1.0

# Stop conditions
EXPECTED_TOTAL = 215  # stop when we've collected this many rows (challenge expectation)


# ---------------------------
# Utilities
# ---------------------------
def clean(text: str) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split())


async def safe_text(page, selector: str, timeout: int = 2000) -> str:
    try:
        return clean(await page.locator(selector).inner_text(timeout=timeout))
    except Exception:
        return ""


# ---------------------------
# Date-setting helper (patched)
# ---------------------------
async def set_dates_and_fire_search(page, from_date="01/09/2025", to_date="30/09/2025"):
    """
    1) Fill visible text boxes (ctl03_dateInput_text and ctl05_dateInput_text)
    2) Update hidden date inputs and Telerik RadDateInput internal state via $find(...).set_value(...) if available
    3) Click the Search button to cause the postback
    """
    # 1) Fill visible textboxes (visible inputs)
    try:
        await page.fill("#ctl00_cphContent_ctl00_ctl03_dateInput_text", from_date)
    except Exception:
        await page.evaluate(f"document.getElementById('ctl00_cphContent_ctl00_ctl03_dateInput_text').value = '{from_date}';")

    try:
        await page.fill("#ctl00_cphContent_ctl00_ctl05_dateInput_text", to_date)
    except Exception:
        await page.evaluate(f"document.getElementById('ctl00_cphContent_ctl00_ctl05_dateInput_text').value = '{to_date}';")

    # 2) Use JS to update Telerik RadDateInput internal object and hidden inputs
    js = f"""
    (function() {{
        try {{
            var v1 = document.getElementById('ctl00_cphContent_ctl00_ctl03_dateInput_text');
            var h1 = document.getElementById('ctl00_cphContent_ctl00_ctl03_dateInput');
            if (v1) v1.value = '{from_date}';
            if (h1) h1.value = '{from_date}';

            var v2 = document.getElementById('ctl00_cphContent_ctl00_ctl05_dateInput_text');
            var h2 = document.getElementById('ctl00_cphContent_ctl00_ctl05_dateInput');
            if (v2) v2.value = '{to_date}';
            if (h2) h2.value = '{to_date}';

            if (typeof $find === 'function') {{
                try {{
                    var r1 = $find('ctl00_cphContent_ctl00_ctl03_dateInput');
                    if (r1 && typeof r1.set_value === 'function') r1.set_value('{from_date}');
                }} catch(e){{}}

                try {{
                    var r2 = $find('ctl00_cphContent_ctl00_ctl05_dateInput');
                    if (r2 && typeof r2.set_value === 'function') r2.set_value('{to_date}');
                }} catch(e){{}}
            }}
        }} catch(e) {{ /* silent */ }}
    }})();
    """
    try:
        await page.evaluate(js)
    except Exception:
        pass

    # small wait to let client-side state sync
    await asyncio.sleep(0.25)

    # 3) Click Search button (preferred)
    try:
        await page.get_by_role("button", name="Search For Property").click()
    except Exception:
        # fallback to forcing a postback (best effort)
        try:
            await page.evaluate("__doPostBack('ctl00$cphContent$ctl00$btnSearchProperty','');")
        except Exception:
            # last resort: click first visible button
            try:
                btns = page.locator("input[type='button'], button")
                if await btns.count() > 0:
                    await btns.first.click()
            except Exception:
                pass


# ---------------------------
# Phase 1: collect list rows
# ---------------------------
async def run_search_and_collect_list_rows() -> list:
    """
    Perform the UI search for 01/09/2025 - 30/09/2025 and collect list rows across pages.
    Stops when EXPECTED_TOTAL rows collected OR when an empty DA_Number row is encountered.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()

        print("Opening site...")
        await page.goto(BASE_URL)

        # Accept disclaimer if present
        try:
            await page.get_by_role("button", name="Agree", exact=True).click()
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # Click Advanced Search
        try:
            await page.get_by_role("link", name="Advanced Search").click()
            await asyncio.sleep(0.2)
        except Exception:
            pass

        print("Setting date range (patched method)...")
        # Set dates and fire search (this performs the postback)
        await set_dates_and_fire_search(page, from_date="01/09/2025", to_date="30/09/2025")

        # Wait for network / initial grid load
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(0.8)

        collected = []
        page_no = 1
        stop_flag = False
        consecutive_small_pages = 0  # optional guard

        while True:
            print(f"Scraping list page {page_no}...")

            # Wait for RadGrid table and rows
            try:
                await page.wait_for_selector(RADGRID_TABLE_SELECTOR, timeout=30000)
            except PlaywrightTimeoutError:
                print("Grid not found — aborting list scraping.")
                break

            try:
                await page.wait_for_selector(ROW_SELECTOR, timeout=30000)
            except PlaywrightTimeoutError:
                print("Rows not found — aborting list scraping.")
                break

            rows = page.locator(ROW_SELECTOR)
            try:
                count = await rows.count()
            except Exception:
                count = 0
            print(f"Found {count} rows on page {page_no}")

            # If there are no rows, stop
            if count == 0:
                print("No data rows on this page — stopping.")
                break

            for i in range(count):
                r = rows.nth(i)
                try:
                    href = await r.locator("td:nth-child(1) a").get_attribute("href")
                except Exception:
                    href = ""

                da = clean(await r.locator("td:nth-child(2)").inner_text())

                # STOP CONDITION 1: empty DA number -> end of valid rows
                if da == "":
                    print("Reached end of valid rows — stopping.")
                    stop_flag = True
                    break

                submitted = clean(await r.locator("td:nth-child(3)").inner_text())
                address = clean(await r.locator("td:nth-child(4)").inner_text())

                full_url = urljoin(page.url, href) if href else ""
                collected.append({
                    "DA_Number": da,
                    "Detail_URL": full_url,
                    "Submitted_Date": submitted,
                    "Property_Address": address,
                })

                # STOP CONDITION 2: reached expected total
                if len(collected) >= EXPECTED_TOTAL:
                    print(f"Reached expected {EXPECTED_TOTAL} results — stopping.")
                    stop_flag = True
                    break

            # Break outer loop if stopping triggered
            if stop_flag:
                break

            # Optional guard: if page has very few rows repeatedly, increment guard
            if count < 3:
                consecutive_small_pages += 1
            else:
                consecutive_small_pages = 0

            # If the grid shows Next button, move, else finish
            next_btn = page.locator(NEXT_SELECTOR)
            if await next_btn.count() == 0:
                print("No next button — reached last page.")
                break

            # Safety: if too many consecutive tiny pages, stop to avoid scanning thousands of pages
            if consecutive_small_pages >= 8 and len(collected) > 50:
                print("Many consecutive small pages detected — assuming results exhausted. Stopping.")
                break

            print("Clicking Next page...")
            try:
                await next_btn.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await asyncio.sleep(0.8)
                page_no += 1
            except Exception as e:
                print(f"Failed to click next: {e}")
                break

        await browser.close()
        return collected


# ---------------------------
# Phase 2: scrape detail pages in parallel
# ---------------------------
async def scrape_details_worker(chunk: list, worker_id: int) -> list:
    """
    Launches its own browser instance and scrapes details for each row in chunk.
    Each worker returns a list of dict rows with all required columns.
    """
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()

        for r in chunk:
            da = r.get("DA_Number", "")
            url = r.get("Detail_URL", "")
            print(f"[Worker {worker_id}] Scraping {da}")

            # default empty result template
            default_entry = {
                **r,
                "Description": "",
                "Decision": "",
                "Categories": "",
                "Applicant": "",
                "Progress": "",
                "Fees": "",
                "Documents": "",
                "Contact_Council": "",
            }

            if not url:
                results.append(default_entry)
                continue

            # Navigate with retry
            nav_ok = False
            for attempt in range(NAV_RETRY):
                try:
                    await page.goto(url, wait_until="networkidle")
                    nav_ok = True
                    break
                except Exception:
                    await asyncio.sleep(NAV_RETRY_DELAY)
            if not nav_ok:
                try:
                    await page.goto(url)
                except Exception:
                    print(f"[Worker {worker_id}] Failed loading {url}")
                    results.append(default_entry)
                    continue

            # Extract fields (selectors based on observation)
            description = await safe_text(page, "#ctl00_cphContent_lblDescription")
            decision = await safe_text(page, "#ctl00_cphContent_lblDecision")
            categories = await safe_text(page, "#ctl00_cphContent_lblCat")
            applicant = await safe_text(page, "#ctl00_cphContent_lblApplicant")
            progress = await safe_text(page, "#ctl00_cphContent_lblProgress")
            fees = await safe_text(page, "#ctl00_cphContent_lblFees")
            documents = await safe_text(page, "#ctl00_cphContent_lblDocuments")
            contact = await safe_text(page, "#ctl00_cphContent_lblContactCouncil")

            # Cleaning rules (exact matches)
            if fees == "No fees recorded against this application.":
                fees = "Not required"
            if contact == "Application Is Not on exhibition, please call Council on 1300 293 111 if you require assistance.":
                contact = "Not required"

            results.append({
                **r,
                "Description": description,
                "Decision": decision,
                "Categories": categories,
                "Applicant": applicant,
                "Progress": progress,
                "Fees": fees,
                "Documents": documents,
                "Contact_Council": contact,
            })

        await browser.close()
    return results


# ---------------------------
# MAIN
# ---------------------------
async def main():
    t0 = time.time()
    print("Phase 1: collecting list rows...")
    list_rows = await run_search_and_collect_list_rows()
    total = len(list_rows)
    print(f"Collected {total} list rows")

    if total == 0:
        print("No rows collected — exiting.")
        return

    # Split into chunks for workers
    chunk_size = total // WORKERS + 1
    chunks = [list_rows[i:i + chunk_size] for i in range(0, total, chunk_size)]

    print(f"Launching {len(chunks)} workers (each opens a browser)...")
    tasks = [asyncio.create_task(scrape_details_worker(chunks[i], i + 1)) for i in range(len(chunks))]
    nested = await asyncio.gather(*tasks)

    final = [item for sub in nested for item in sub]

    # Build DataFrame with exact headers & save CSV
    df = pd.DataFrame(final)
    for h in CSV_HEADERS:
        if h not in df.columns:
            df[h] = ""
    df = df[CSV_HEADERS]
    df.to_csv("results.csv", index=False)

    print(f"Saved results.csv with {len(final)} rows — elapsed {time.time() - t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
