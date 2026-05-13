"""Token-aware chunker.

Strategy: paragraph-aware sliding window over the joined per-page text,
with a hard token budget per chunk and a small overlap so RAG retrieval
keeps cross-paragraph context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

# Default budget tuned for text-embedding-3-large (8191 max) but small enough
# that 6–8 chunks comfortably fit a Step-1-of-Equity-Model prompt.
DEFAULT_TARGET_TOKENS = 350
DEFAULT_OVERLAP_TOKENS = 50


@dataclass(slots=True)
class Chunk:
    chunk_index: int
    page_number: int | None
    text: str
    token_count: int


def _encoder():
    try:
        import tiktoken
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        try:
            import tiktoken
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def chunk_pages(
    pages: Sequence[tuple[int, str]],
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Produce a list of Chunks from (page_number, text) pairs."""
    chunks: list[Chunk] = []
    enc = _encoder()
    if enc is None:
        # No tokenizer available — fall back to char-based heuristics.
        char_target = target_tokens * 4
        for page_no, text in pages:
            chunks.extend(_char_chunks(page_no, text, target=char_target, overlap=overlap_tokens * 4, start_index=len(chunks)))
        return chunks

    for page_no, text in pages:
        if not text.strip():
            continue
        token_ids = enc.encode(text)
        if not token_ids:
            continue
        step = max(1, target_tokens - overlap_tokens)
        for start in range(0, len(token_ids), step):
            window = token_ids[start : start + target_tokens]
            if not window:
                break
            piece = enc.decode(window).strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    page_number=page_no,
                    text=piece,
                    token_count=len(window),
                )
            )
            if start + target_tokens >= len(token_ids):
                break
    return chunks


def _char_chunks(
    page_no: int, text: str, *, target: int, overlap: int, start_index: int
) -> Iterator[Chunk]:
    step = max(1, target - overlap)
    idx = start_index
    for start in range(0, len(text), step):
        piece = text[start : start + target].strip()
        if not piece:
            continue
        yield Chunk(
            chunk_index=idx,
            page_number=page_no,
            text=piece,
            token_count=max(1, len(piece) // 4),
        )
        idx += 1
        if start + target >= len(text):
            break
