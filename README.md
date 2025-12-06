# DAScraperFinal

Shoalhaven DA Scraper — 4-Browser Parallel Playwright Extractor

This package contains the final, production-ready automated scraper built for the ReluConsultancy hiring challenge, designed to extract Development Application (DA) records from the Shoalhaven Council MasterView system.

It fully implements the required workflow:

Navigate & accept disclaimer

Enter DA Tracking module

Use Advanced Search

Set date range correctly (Telerik RadDateInput)

Trigger server-side search

Paginate through all results

Scrape every record’s detail page

Apply strict data-cleaning rules

Store results in CSV using exact required headers

🚀 Key Features
🔹 4-Worker Parallel Scraping (4 Browsers at Once)

The scraper launches four separate Chromium browser instances, each acting as an independent worker.
Instead of scraping detail pages sequentially, the script divides the total list of applications into 4 chunks, and each browser processes one chunk.

Why this makes scraping ~4× faster:

Every detail page is loaded on its own browser → no waiting for previous pages

I/O latency is parallelized

CPU/JavaScript execution is distributed across multiple browser processes

Large datasets finish dramatically sooner (e.g., 215 rows drop from ~8–10 minutes to ~2–3 minutes)
## Files
- scraper_parallel.py : the main scraper (async, Playwright)
- requirements.txt : dependencies
- README.md : this file

## Run locally
1. Create venv and install dependencies:
   python -m venv .venv
   .venv\\Scripts\\activate  # windows
   pip install -r requirements.txt

2. Install playwright browsers (required):
   python -m playwright install

3. Run the scraper:
   python scraper_parallel.py

Output: results.csv will be created in the same folder.
