"""
Document Ingestion Pipeline
============================
Supports: PDF, DOCX, TXT, Markdown, Website URLs.

Processing flow:
  1. Load document (text extraction)
  2. Split into overlapping chunks
  3. Generate embeddings (via VectorStoreManager)
  4. Store in per-chatbot ChromaDB collection
  5. Update document status in SQLite
"""

import logging
import uuid
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ingestion")

# ── Chunk settings ────────────────────────────────────────────────────────────
CHUNK_SIZE    = 512   # characters
CHUNK_OVERLAP = 64    # characters


# ─────────────────────────────────────────────────────────────────────────────
# Text loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _load_pdf(path: str) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        # Fallback: pdfminer
        try:
            from pdfminer.high_level import extract_text
            return extract_text(path)
        except ImportError:
            raise RuntimeError("Install pypdf or pdfminer.six to handle PDF files")


def _load_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        raise RuntimeError("Install python-docx to handle DOCX files")


def _load_url(url: str) -> str:
    """Fetch a web page and extract clean text.

    Strategy (in order):
    1. Standard HTML text extraction (works for server-rendered sites)
    2. Meta tags + title (SEO fields always present, even on JS-rendered sites)
    3. JSON-LD structured data (schema.org blocks embedded in <script> tags)
    4. Next.js __NEXT_DATA__ / Nuxt __NUXT_DATA__ JSON blobs
    5. trafilatura if installed (best-effort smart extractor)
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import httpx
        import json
        from html.parser import HTMLParser

        class _FullExtractor(HTMLParser):
            """Extract visible text, meta descriptions, and JSON-LD blobs."""
            def __init__(self):
                super().__init__()
                self._skip_tags  = {"style", "noscript"}
                self._json_tags  = set()   # script tags we want to capture
                self._in_skip    = False
                self._in_json    = False
                self._in_title   = False
                self.text_parts:  list[str] = []
                self.meta_parts:  list[str] = []
                self.json_blobs:  list[str] = []
                self._buf = ""

            def handle_starttag(self, tag, attrs):
                t = tag.lower()
                adict = dict(attrs)
                if t in self._skip_tags:
                    self._in_skip = True
                elif t == "title":
                    self._in_title = True
                elif t == "meta":
                    # collect description / og:description / keywords
                    name = (adict.get("name") or adict.get("property") or "").lower()
                    content = adict.get("content", "").strip()
                    if content and any(k in name for k in ("description", "keyword", "title")):
                        self.meta_parts.append(content)
                elif t == "script":
                    stype = adict.get("type", "").lower()
                    sid   = adict.get("id", "").lower()
                    if stype == "application/ld+json" or sid in ("__next_data__", "__nuxt_data__"):
                        self._in_json = True
                        self._buf = ""
                    else:
                        self._in_skip = True

            def handle_endtag(self, tag):
                t = tag.lower()
                if t in self._skip_tags or t == "script":
                    if self._in_json and t == "script":
                        self.json_blobs.append(self._buf)
                        self._buf = ""
                    self._in_skip = False
                    self._in_json = False
                elif t == "title":
                    self._in_title = False

            def handle_data(self, data):
                if self._in_json:
                    self._buf += data
                elif self._in_title:
                    stripped = data.strip()
                    if stripped:
                        self.meta_parts.append("Title: " + stripped)
                elif not self._in_skip:
                    stripped = data.strip()
                    if stripped:
                        self.text_parts.append(stripped)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = httpx.get(url, timeout=30, follow_redirects=True, headers=headers)
        response.raise_for_status()

        extractor = _FullExtractor()
        extractor.feed(response.text)

        parts: list[str] = []

        # 1. Standard visible text
        parts.extend(extractor.text_parts)

        # 2. Meta tags (always harvest even if visible text exists)
        parts.extend(extractor.meta_parts)

        # 3. JSON-LD / __NEXT_DATA__ blobs — extract string values recursively
        def _strings_from_json(obj, depth=0):
            if depth > 6:
                return
            if isinstance(obj, str) and len(obj) > 20:
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from _strings_from_json(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    yield from _strings_from_json(item, depth + 1)

        for blob in extractor.json_blobs:
            try:
                data = json.loads(blob)
                parts.extend(_strings_from_json(data))
            except Exception:
                pass

        raw = "\n".join(parts)
        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()

        # 4. trafilatura fallback if still empty and library is available
        if not raw:
            try:
                import trafilatura
                raw = trafilatura.extract(response.text) or ""
            except ImportError:
                pass

        return raw
    except Exception as e:
        raise RuntimeError(f"Failed to load URL {url}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks, respecting sentence boundaries
    where possible.
    """
    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = start + chunk_size

        if end >= length:
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        # Try to break at a sentence boundary
        boundary = -1
        for sep in (". ", ".\n", "! ", "? ", "\n\n"):
            idx = text.rfind(sep, start, end)
            if idx != -1 and idx > start + chunk_size // 2:
                boundary = idx + len(sep)
                break

        if boundary == -1:
            # Fall back to word boundary
            space = text.rfind(" ", start, end)
            boundary = space + 1 if space != -1 else end

        chunk = text[start:boundary].strip()
        if chunk:
            chunks.append(chunk)

        # Move start back by overlap
        start = max(start + 1, boundary - overlap)

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Main ingestion class
# ─────────────────────────────────────────────────────────────────────────────

class DocumentIngestion:
    """Handles document loading, chunking, embedding, and storage."""

    def __init__(self, vector_store, tenant_manager):
        self.vs  = vector_store
        self.tm  = tenant_manager

    # ── Public entry points ───────────────────────────────────────────────────

    async def ingest_file(
        self,
        file_path: str,
        chatbot_id: str,
        doc_id: str,
        original_name: str,
    ) -> dict:
        """Load a file, chunk it, and store in the vector DB."""
        logger.info("Ingesting file: %s (doc_id=%s)", original_name, doc_id)
        try:
            text = self._load_file(file_path)
            return await self._process_text(text, chatbot_id, doc_id, original_name, source=file_path)
        except Exception as e:
            logger.error("Ingestion failed for %s: %s", original_name, e)
            self.tm.update_document_status(doc_id, "failed", error=str(e))
            raise

    async def ingest_url(
        self,
        url: str,
        chatbot_id: str,
        doc_id: str,
    ) -> dict:
        """Crawl a URL, chunk it, and store in the vector DB."""
        logger.info("Ingesting URL: %s (doc_id=%s)", url, doc_id)
        try:
            text = _load_url(url)
            return await self._process_text(text, chatbot_id, doc_id, original_name=url, source=url)
        except Exception as e:
            logger.error("URL ingestion failed for %s: %s", url, e)
            self.tm.update_document_status(doc_id, "failed", error=str(e))
            raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_file(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        loaders = {
            ".pdf":  _load_pdf,
            ".docx": _load_docx,
            ".doc":  _load_docx,
            ".txt":  _load_txt,
            ".md":   _load_txt,
        }
        loader = loaders.get(ext)
        if not loader:
            raise ValueError(f"Unsupported file extension: {ext}")
        return loader(file_path)

    async def _process_text(
        self,
        text: str,
        chatbot_id: str,
        doc_id: str,
        original_name: str,
        source: str,
    ) -> dict:
        if not text.strip():
            self.tm.update_document_status(doc_id, "failed", error="Document is empty")
            raise ValueError("Document produced no extractable text")

        chunks = _split_text(text)
        if not chunks:
            self.tm.update_document_status(doc_id, "failed", error="No chunks generated")
            raise ValueError("Document produced no chunks after splitting")

        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id":        doc_id,
                "chatbot_id":    chatbot_id,
                "source":        original_name,
                "chunk_index":   i,
                "total_chunks":  len(chunks),
            }
            for i in range(len(chunks))
        ]

        self.vs.upsert_chunks(chatbot_id, chunks, metadatas, ids)
        self.tm.update_document_status(doc_id, "ready", chunk_count=len(chunks))

        logger.info(
            "Ingested %d chunks from '%s' into chatbot %s",
            len(chunks), original_name, chatbot_id,
        )
        return {"doc_id": doc_id, "chunks": len(chunks), "status": "ready"}
