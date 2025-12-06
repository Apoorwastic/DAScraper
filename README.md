# DAScraperFinal

This package contains the final 4-browser parallel Playwright scraper for the ReluConsultancy hiring challenge.

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
