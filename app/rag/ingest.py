"""Crawl the GitLab handbook seed sections, chunk + embed the content, and
persist it to a local Chroma collection.

Run on demand whenever the handbook content should be refreshed:

    uv run python -m app.rag.ingest
"""

import hashlib
import os
import time
from collections import deque
from urllib.parse import urldefrag, urljoin

import chromadb
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from app.rag.config import (
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    MAX_PAGES,
    REQUEST_DELAY_SECONDS,
    SEED_URLS,
    USER_AGENT,
)

CONTENT_SELECTORS = ["main[role=main]", "main", "article"]


def normalize_url(url: str) -> str:
    url, _fragment = urldefrag(url)
    return url.rstrip("/")


def extract_content(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    content = None
    for selector in CONTENT_SELECTORS:
        content = soup.select_one(selector)
        if content:
            break
    if content is None:
        content = soup.body or soup

    for tag in content.select("nav, header, footer, script, style"):
        tag.decompose()

    text = content.get_text(separator="\n", strip=True)
    return title, text


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"])
        links.append(normalize_url(absolute))
    return links


def crawl() -> list[dict]:
    prefixes = [normalize_url(u) for u in SEED_URLS]
    visited: set[str] = set()
    queue: deque[str] = deque(prefixes)
    pages: list[dict] = []

    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30) as client:
        while queue and len(visited) < MAX_PAGES:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as e:
                print(f"  skip {url}: {e}")
                continue

            title, text = extract_content(response.text)
            if text:
                pages.append({"url": url, "title": title, "text": text})

            for link in extract_links(response.text, url):
                if link not in visited and any(link.startswith(p) for p in prefixes):
                    queue.append(link)

            time.sleep(REQUEST_DELAY_SECONDS)

    return pages


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        start = end - overlap
    return chunks


def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def main():
    load_dotenv()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set - required to embed handbook content.")

    print(f"Crawling seeds: {SEED_URLS}")
    pages = crawl()
    print(f"Crawled {len(pages)} pages.")

    records = []
    for page in pages:
        for chunk in chunk_text(page["text"]):
            records.append({"url": page["url"], "title": page["title"], "text": chunk})
    print(f"Split into {len(records)} chunks.")

    if not records:
        raise SystemExit("No content found to ingest - aborting without touching the existing index.")

    openai_client = OpenAI(api_key=api_key)
    embeddings: list[list[float]] = []
    batch_size = 100
    for i in range(0, len(records), batch_size):
        batch = [r["text"] for r in records[i : i + batch_size]]
        embeddings.extend(embed_batch(openai_client, batch))
        print(f"  embedded {min(i + batch_size, len(records))}/{len(records)} chunks")

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = chroma_client.create_collection(COLLECTION_NAME)

    ids = [hashlib.sha256(f"{r['url']}|{i}".encode()).hexdigest() for i, r in enumerate(records)]
    documents = [r["text"] for r in records]
    metadatas = [{"source_url": r["url"], "title": r["title"]} for r in records]

    add_batch = 100
    for i in range(0, len(records), add_batch):
        collection.add(
            ids=ids[i : i + add_batch],
            embeddings=embeddings[i : i + add_batch],
            documents=documents[i : i + add_batch],
            metadatas=metadatas[i : i + add_batch],
        )

    print(f"Ingested {len(records)} chunks from {len(pages)} pages into '{COLLECTION_NAME}' at {CHROMA_DIR}.")


if __name__ == "__main__":
    main()
