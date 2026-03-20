"""
Vector Store Manager — Multi-Tenant ChromaDB
=============================================
Each chatbot gets its own ChromaDB collection, ensuring total
isolation between tenants.  Embeddings are generated locally with
sentence-transformers (no API cost).

Collection naming:  chatbot_<chatbot_id>
"""

import logging
import os
from pathlib import Path
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("vector_store")

# ── Paths ─────────────────────────────────────────────────────────────────────
_DATA_DIR = Path(os.getenv("DATA_DIR", ".."))
CHROMA_DIR = _DATA_DIR / "chroma_db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# ── Embedding model (cached after first load) ─────────────────────────────────
_EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_embed_model: Optional[SentenceTransformer] = None


def get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        logger.info("Loading embedding model: %s", _EMBED_MODEL_NAME)
        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
        logger.info("Embedding model loaded")
    return _embed_model


# ─────────────────────────────────────────────────────────────────────────────

class VectorStoreManager:
    """
    Manages a persistent ChromaDB instance and exposes per-chatbot
    collection operations.
    """

    def __init__(self):
        self._client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        logger.info("ChromaDB initialised at %s", CHROMA_DIR)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _collection_name(chatbot_id: str) -> str:
        # ChromaDB collection names: 3-63 chars, alphanumeric + dashes/underscores
        return f"chatbot_{chatbot_id}"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = get_embed_model()
        # BGE models benefit from a query/passage prefix
        if "bge" in _EMBED_MODEL_NAME.lower():
            texts = [f"passage: {t}" for t in texts]
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def _embed_query(self, text: str) -> list[float]:
        model = get_embed_model()
        if "bge" in _EMBED_MODEL_NAME.lower():
            text = f"query: {text}"
        embedding = model.encode([text], normalize_embeddings=True)
        return embedding[0].tolist()

    # ── Collection management ─────────────────────────────────────────────────

    def get_or_create_collection(self, chatbot_id: str):
        name = self._collection_name(chatbot_id)
        collection = self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        return collection

    def delete_collection(self, chatbot_id: str) -> None:
        name = self._collection_name(chatbot_id)
        try:
            self._client.delete_collection(name)
            logger.info("Deleted collection: %s", name)
        except Exception as e:
            logger.warning("Could not delete collection %s: %s", name, e)

    def collection_exists(self, chatbot_id: str) -> bool:
        try:
            self._client.get_collection(self._collection_name(chatbot_id))
            return True
        except Exception:
            return False

    # ── Write operations ──────────────────────────────────────────────────────

    def add_chunks(
        self,
        chatbot_id: str,
        chunks: list[str],
        metadatas: list[dict],
        ids: list[str],
    ) -> None:
        """
        Add text chunks + pre-computed metadata to the chatbot's collection.
        Each chunk is embedded locally.
        """
        if not chunks:
            return
        collection = self.get_or_create_collection(chatbot_id)
        embeddings = self._embed(chunks)
        collection.add(
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
            ids=ids,
        )
        logger.info("Added %d chunks to chatbot %s", len(chunks), chatbot_id)

    def upsert_chunks(
        self,
        chatbot_id: str,
        chunks: list[str],
        metadatas: list[dict],
        ids: list[str],
    ) -> None:
        """Upsert — safe to call again for idempotent re-ingestion."""
        if not chunks:
            return
        collection = self.get_or_create_collection(chatbot_id)
        embeddings = self._embed(chunks)
        collection.upsert(
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
            ids=ids,
        )
        logger.info("Upserted %d chunks to chatbot %s", len(chunks), chatbot_id)

    def delete_by_doc_id(self, chatbot_id: str, doc_id: str) -> None:
        """Remove all vectors belonging to a specific document."""
        try:
            collection = self.get_or_create_collection(chatbot_id)
            collection.delete(where={"doc_id": doc_id})
            logger.info("Deleted vectors for doc %s from chatbot %s", doc_id, chatbot_id)
        except Exception as e:
            logger.warning("Could not delete vectors: %s", e)

    # ── Query operations ──────────────────────────────────────────────────────

    def query(
        self,
        chatbot_id: str,
        question: str,
        n_results: int = 5,
    ) -> list[dict]:
        """
        Retrieve the top-n most relevant chunks for a question.
        Returns list of {text, score, metadata} dicts.
        """
        collection = self.get_or_create_collection(chatbot_id)
        count = collection.count()
        if count == 0:
            logger.info("Collection for chatbot %s is empty", chatbot_id)
            return []

        n_results = min(n_results, count)
        query_embedding = self._embed_query(question)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        docs      = results.get("documents", [[]])[0]
        metas     = results.get("metadatas", [[]])[0]
        distances = results.get("distances",  [[]])[0]

        for doc, meta, dist in zip(docs, metas, distances):
            # Convert cosine distance → similarity score
            score = 1 - dist
            hits.append({"text": doc, "score": score, "metadata": meta})

        # Filter low-relevance results
        hits = [h for h in hits if h["score"] > 0.25]
        return hits

    def get_collection_stats(self, chatbot_id: str) -> dict:
        """Return basic stats about a chatbot's knowledge base."""
        try:
            collection = self.get_or_create_collection(chatbot_id)
            return {"chunk_count": collection.count(), "chatbot_id": chatbot_id}
        except Exception as e:
            return {"chunk_count": 0, "chatbot_id": chatbot_id, "error": str(e)}
