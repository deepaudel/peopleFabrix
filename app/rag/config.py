import os

SEED_URLS = [
    "https://handbook.gitlab.com/handbook/people-group/",
    "https://handbook.gitlab.com/handbook/people-policies/",
]

CHROMA_DIR = os.environ.get("CHROMA_DIR", "data/chroma")
COLLECTION_NAME = "gitlab_handbook"

EMBEDDING_MODEL = "text-embedding-3-small"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

TOP_K = 4
MAX_PAGES = 400
REQUEST_DELAY_SECONDS = 0.2
USER_AGENT = "peopleFabrix-ingest/1.0 (+internal HR assistant)"
