import os

import chromadb
from openai import OpenAI

from app.rag.config import CHROMA_DIR, COLLECTION_NAME, EMBEDDING_MODEL, TOP_K


def _get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        return None


def get_relevant_chunks(question: str, top_k: int = TOP_K) -> list[dict]:
    collection = _get_collection()
    if collection is None or collection.count() == 0:
        return []

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return []

    client = OpenAI(api_key=api_key)
    embedding = client.embeddings.create(model=EMBEDDING_MODEL, input=[question]).data[0].embedding

    results = collection.query(query_embeddings=[embedding], n_results=top_k)

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    return [
        {"text": doc, "source_url": meta.get("source_url", ""), "title": meta.get("title", "")}
        for doc, meta in zip(documents, metadatas)
    ]
