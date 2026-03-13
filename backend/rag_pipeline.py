"""
RAG Pipeline
============
Retrieval-Augmented Generation pipeline:

  1. Retrieve top-k relevant chunks from the chatbot's vector store
  2. Build a concise context string
  3. Route to the best available LLM via LLMRouter
  4. Return the grounded answer

The pipeline maintains per-session short-term memory so follow-up
questions are answered correctly.
"""

import logging
from collections import defaultdict, deque
from typing import Optional

from llm_router import LLMRouter

logger = logging.getLogger("rag_pipeline")

# ── Settings ──────────────────────────────────────────────────────────────────
TOP_K             = 5     # number of context chunks to retrieve
MAX_CONTEXT_CHARS = 3500  # hard cap to avoid token limit issues
HISTORY_TURNS     = 3     # how many past Q/A pairs to include


class RAGPipeline:
    """
    Orchestrates retrieval + generation for a given chatbot.
    """

    def __init__(self, vector_store):
        self.vs     = vector_store
        self.router = LLMRouter()
        # Per-session conversation history  {session_id: deque[(q,a)]}
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS))

    # ── Public API ────────────────────────────────────────────────────────────

    async def query(
        self,
        chatbot_id: str,
        question: str,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        Run the full RAG pipeline for a user question.

        Returns:
            {
                "answer":   str,
                "provider": str,
                "sources":  list[str],
                "context_found": bool,
            }
        """
        # 1. Retrieve relevant chunks
        hits = self.vs.query(chatbot_id, question, n_results=TOP_K)

        if not hits:
            logger.info("No relevant context found for chatbot %s", chatbot_id)
            return {
                "answer":        "I don't have that information. Please contact support.",
                "provider":      "none",
                "sources":       [],
                "context_found": False,
            }

        # 2. Build context string (ranked by relevance score)
        hits.sort(key=lambda h: h["score"], reverse=True)
        context_parts: list[str] = []
        total_chars = 0
        sources: list[str] = []

        for hit in hits:
            text = hit["text"].strip()
            src  = hit["metadata"].get("source", "")
            if total_chars + len(text) > MAX_CONTEXT_CHARS:
                break
            context_parts.append(text)
            total_chars += len(text)
            if src and src not in sources:
                sources.append(src)

        context = "\n\n---\n\n".join(context_parts)

        # 3. Prepend conversation history for context-aware follow-ups
        if session_id and self._history[session_id]:
            history_text = self._build_history(session_id)
            context = f"Previous conversation:\n{history_text}\n\n---\n\nKnowledge base:\n{context}"

        # 4. Call LLM
        try:
            result = await self.router.generate(question, context)
        except RuntimeError as e:
            logger.error("LLM router exhausted all providers: %s", e)
            return {
                "answer":        "I'm temporarily unavailable. Please try again later.",
                "provider":      "error",
                "sources":       sources,
                "context_found": True,
            }

        answer   = result["answer"]
        provider = result["provider"]

        # 5. Store in session history
        if session_id:
            self._history[session_id].append((question, answer))

        logger.info(
            "chatbot=%s  provider=%s  score_top=%.2f  chunks=%d",
            chatbot_id, provider, hits[0]["score"], len(context_parts),
        )

        return {
            "answer":        answer,
            "provider":      provider,
            "sources":       sources,
            "context_found": True,
        }

    def clear_session(self, session_id: str) -> None:
        """Clear conversation history for a session."""
        self._history.pop(session_id, None)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_history(self, session_id: str) -> str:
        lines = []
        for q, a in self._history[session_id]:
            lines.append(f"User: {q}")
            lines.append(f"Assistant: {a}")
        return "\n".join(lines)
