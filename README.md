# Python Practice Scripts

A collection of random Python scripts I’m writing while practicing and improving my Python skills.

The scripts are small, experimental, and may cover different concepts, exercises, and ideas as I learn.

## Crossword Puzzle Scraper

`crossword-puzzles-scrape-file.py` uses Playwright to crawl [Crossword Labs](https://crosswordlabs.com), collect puzzle links, and save puzzle pages as PDFs. It stores crawl data in `data/crossword/pages.jsonl` and `data/crossword/links.jsonl`, with PDFs written to `data/crossword/pdfs/`. The current script processes the first two pages and first two puzzle links as a small test run.

Run it with:

```bash
uv run playwright install chromium
uv run python crossword-puzzles-scrape-file.py
```

The script recreates `data/crossword/` on each run.
