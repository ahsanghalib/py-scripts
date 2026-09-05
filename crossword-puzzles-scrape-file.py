import asyncio
import json
import shutil
from collections.abc import Awaitable, Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import aiofiles
from aiofiles.threadpool.text import AsyncTextIOWrapper
from playwright.async_api import Browser, async_playwright
from tqdm import tqdm

BASE_URL = "https://crosswordlabs.com"
SCRAPE_FILE_PAGES = "./data/crossword/pages.jsonl"
SCRAPE_FILE_LINKS = "./data/crossword/links.jsonl"

FileMode = Literal["r", "w", "a"]


async def track_progress[T, U](awaitable: Awaitable[T], progress: tqdm[U]) -> T:
    try:
        return await awaitable
    finally:
        _ = progress.update(1)


def progress_bar[T](
    iterable: Iterable[T],
    desc: str,
    total: int,
    unit: str,
) -> tqdm[T]:
    return tqdm(
        iterable=iterable,
        total=total,
        desc=desc,
        unit=unit,
        # leave=True,
        # ncols=100,
        bar_format="{desc} {percentage:3.0f}% |{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
    )


class CrawlStatus(StrEnum):
    PENDING = "pending"
    FAILED = "failed"
    COMPLETED = "completed"
    PROCESSING = "processing"


@dataclass
class CrawlRecord:
    url: str
    status: CrawlStatus
    last_checked_at: str | None


class AsyncCrawlerRepo:
    def __init__(self, file_path: str | Path, mode: FileMode):
        self.file_path: Path = Path(file_path)
        self.file: AsyncTextIOWrapper | None = None
        self.records: list[CrawlRecord] = []
        self.file_mode: FileMode = mode

    # without context manager
    @classmethod
    def create(cls, file_path: str | Path, mode: FileMode) -> AsyncCrawlerRepo:
        instance: AsyncCrawlerRepo = cls(file_path, mode)
        _ = instance.open(mode)
        return instance

    # with context manager
    async def __aenter__(self):
        await self.open(self.file_mode)
        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb):  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        await self.close()

    # shared methods
    async def open(self, mode: FileMode):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.touch(exist_ok=True)

        if self.file is None:
            self.file = await aiofiles.open(self.file_path, mode=mode, encoding="utf-8")

    async def close(self):
        if self.file is not None:
            await self.file.close()
            self.file = None

    # utility methods
    async def save(self, record: CrawlRecord) -> None:
        if self.file is not None:
            _ = await self.file.write(json.dumps(asdict(record)) + "\n")

    async def load(self) -> list[CrawlRecord]:
        if not self.file_path.exists():
            self.records.clear()

        async with aiofiles.open(self.file_path, "r", encoding="utf-8") as file:
            lines = await file.readlines()
            self.records.clear()

            for line in lines:
                l = json.loads(line)  # pyright: ignore[reportAny]
                record = CrawlRecord(
                    url=l.get("url"),  # pyright: ignore[reportAny]
                    status=l.get("status"),  # pyright: ignore[reportAny]
                    last_checked_at=l.get("last_checked_at"),  # pyright: ignore[reportAny]
                )
                self.records.append(record)

        return self.records


def extract_total_pages(text: str) -> tuple[int, int]:
    numbers = [int(num) for num in text.strip().split() if num.isdigit()]

    if len(numbers) >= 2:
        return (numbers[0], numbers[1])
    elif len(numbers) == 1:
        return 0, numbers[0]

    return 0, 0


async def save_all_pages_link(browser: Browser):
    page = await browser.new_page()
    _ = await page.goto(f"{BASE_URL}/all")

    await page.locator("div.pagination > span > p > span").first.wait_for()
    total_pages = await page.locator("div.pagination > span > p > span").inner_text()
    _, total = extract_total_pages(total_pages)

    async with AsyncCrawlerRepo(SCRAPE_FILE_PAGES, "w") as file:
        # first page
        record = CrawlRecord(
            url=f"{BASE_URL}/all",
            status=CrawlStatus.PENDING,
            last_checked_at=None,
        )
        await file.save(record)

        # rest of pages
        for page_number in progress_bar(
            range(2, total + 1),
            total=total - 1,
            desc=f"{'Generating Pages':<20}",
            unit="page",
        ):
            record = CrawlRecord(
                url=f"{BASE_URL}/all?page={page_number}",
                status=CrawlStatus.PENDING,
                last_checked_at=None,
            )
            await file.save(record)

    await page.close()


async def save_links(
    browser: Browser, record: CrawlRecord, semaphore: asyncio.Semaphore
):
    async with semaphore:
        if (
            record.status != CrawlStatus.PENDING
            or record.last_checked_at is not None
            or record.url == ""
        ):
            return

        url = record.url
        page = await browser.new_page()
        _ = await page.goto(url)

        dom = "div.crossword-item-pair > div > h3 > a"
        await page.locator(dom).first.wait_for()
        links = await page.locator(dom).all()

        async with AsyncCrawlerRepo(SCRAPE_FILE_LINKS, "a") as file:
            for link in links:
                href = await link.get_attribute("href")

                if href is None:
                    continue

                plink = CrawlRecord(
                    url=f"{BASE_URL}{href}",
                    status=CrawlStatus.PENDING,
                    last_checked_at=None,
                )

                await file.save(plink)

        await page.close()


async def save_puzzles_pdf(
    browser: Browser, record: CrawlRecord, semaphore: asyncio.Semaphore
):
    async with semaphore:
        if (
            record.status != CrawlStatus.PENDING
            or record.last_checked_at is not None
            or record.url == ""
        ):
            return

        url = record.url
        file_name = urlparse(url).path.split("/")[-1]
        page = await browser.new_page()
        _ = await page.goto(url)
        _ = await page.pdf(
            path=f"./data/crossword/pdfs/{file_name}.pdf",
            format="A4",
            margin={"top": "1cm", "right": "1cm", "bottom": "1cm", "left": "1cm"},
        )

        await page.close()


async def main():
    data_folder = Path("./data/crossword/")
    if data_folder.exists() and data_folder.is_dir():
        shutil.rmtree(data_folder)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # crawl all the page links
        await save_all_pages_link(browser)

        # scrape puzzle links
        async with AsyncCrawlerRepo(SCRAPE_FILE_PAGES, "r") as repo:
            pages = await repo.load()
            semaphore = asyncio.Semaphore(5)

            with progress_bar(
                pages,
                total=len(pages),
                desc=f"{'Scrapping pages':<20}",
                unit="page",
            ) as progress:
                _ = await asyncio.gather(
                    *(
                        track_progress(save_links(browser, page, semaphore), progress)
                        for page in pages[:2]
                    )
                )

        # save puzzles as pdf
        async with AsyncCrawlerRepo(SCRAPE_FILE_LINKS, "r") as repo:
            pages = await repo.load()
            semaphore = asyncio.Semaphore(5)

            with progress_bar(
                pages,
                total=len(pages),
                desc=f"{'Saving PDFs':<20}",
                unit="pdf",
            ) as progress:
                _ = await asyncio.gather(
                    *(
                        track_progress(
                            save_puzzles_pdf(browser, page, semaphore), progress
                        )
                        for page in pages[:2]
                    )
                )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
