"""Embeddings, from Google's Gemini API.

Three facts here were measured on 2026-09-30 rather than read in a doc, and all
three are load-bearing:

* **`text-embedding-004` does not exist any more.** The key sees
  `gemini-embedding-001`, `gemini-embedding-2-preview` and `gemini-embedding-2`.
* **Both stable models are natively 3072 dimensions**, and pgvector refuses an
  HNSW index above 2000 -- `create index using hnsw` on `vector(3072)` fails with
  *"column cannot have more than 2000 dimensions for hnsw index"*. So truncating
  is required to be indexable at all, not a tidiness choice.
* **Truncation does not normalise on every model.** `gemini-embedding-001` at
  768 dimensions comes back with an L2 norm of ~0.588; `gemini-embedding-2` comes
  back at ~1.000. Cosine distance over an unnormalised vector is wrong in a way
  that raises no error, fails no test and writes no log line -- it just ranks
  badly. That is why the model below is `-2`, and why `_normalise` runs anyway as
  a belt-and-braces guard rather than trusting the provider forever.

`batchEmbedContents` is used even though it is absent from the model's
`supportedGenerationMethods`; a two-item batch was confirmed to return two
embeddings. The listing is not a reliable guide to what is callable.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-embedding-2"

# Must equal the `vector(N)` on document_chunks.embedding. Changing one without
# the other produces a PostgREST error on every write, which is at least loud.
DIMENSIONS = 768

# The provider accepts 100 per call. Kept lower so one failure costs less work
# and a retry is cheap.
BATCH_SIZE = 50

# Why two task types and not one: they are different vector spaces. Embedding a
# document as a query, or the reverse, degrades recall silently -- no error, no
# test failure, just worse answers. This is the single easiest thing in the
# pipeline to get wrong and never notice.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

TIMEOUT = 30.0
# Four attempts with a 2s -> 8s -> 32s backoff, so the retry window spans ~42s.
# Three attempts at 1s -> 4s covered 5s, which is useless against a *per-minute*
# quota: the first real ingestion lost 73 of 175 vectors to 429s that would have
# cleared on their own given another half minute.
MAX_ATTEMPTS = 4
INITIAL_RETRY_DELAY = 2.0


class EmbeddingUnavailable(RuntimeError):
    """No API key, so embeddings cannot be produced at all."""


def api_key() -> str:
    return os.getenv("GOOGLE_API_KEY", "").strip()


def available() -> bool:
    return bool(api_key())


def _normalise(vector: list[float]) -> list[float]:
    """Scale to unit length.

    `gemini-embedding-2` already returns a normalised vector at 768 dimensions,
    so this is normally a no-op. It runs anyway: the cost is one pass over 768
    floats, and the failure it prevents -- a silently mis-ranked corpus after a
    model change -- is invisible until someone notices answers got worse.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0 or abs(norm - 1.0) < 1e-6:
        return vector
    return [x / norm for x in vector]


async def _post(client: httpx.AsyncClient, path: str, body: dict) -> dict:
    """POST with retry on the failures that are worth retrying."""
    key = api_key()
    if not key:
        raise EmbeddingUnavailable("GOOGLE_API_KEY is not set")

    delay = INITIAL_RETRY_DELAY
    last: str = "unknown"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            res = await client.post(
                f"{BASE_URL}/{path}", params={"key": key}, json=body, timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        else:
            if res.status_code == 200:
                return res.json()
            # 429 and 5xx are transient; a 400 means the request is wrong and
            # will be wrong again.
            if res.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"embedding failed: {res.status_code} {res.text[:200]}")
            last = f"http_{res.status_code}"
            retry_after = res.headers.get("retry-after")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass

        if attempt < MAX_ATTEMPTS:
            logger.warning(
                "embedding request failed; retrying",
                extra={"attempt": attempt, "reason": last, "sleep_s": delay},
            )
            await asyncio.sleep(delay)
            delay *= 4
    raise RuntimeError(f"embedding failed after {MAX_ATTEMPTS} attempts: {last}")


async def embed_documents(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float] | None]:
    """Embed chunk texts for storage. Returns one vector per input, or None.

    **None is a supported outcome, not a failure to propagate.** A chunk with no
    embedding still has its generated `tsv`, so it stays findable by keyword and
    simply drops out of the vector arm of the fusion. Losing the row entirely
    because an embedding call failed would be worse than losing the vector.
    """
    out: list[list[float] | None] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        body = {
            "requests": [
                {
                    "model": f"models/{MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": TASK_DOCUMENT,
                    "outputDimensionality": DIMENSIONS,
                }
                for text in batch
            ]
        }
        try:
            data = await _post(client, f"models/{MODEL}:batchEmbedContents", body)
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            logger.warning(
                # str(exc), not just the type: the reason lives in the message
                # ("embedding failed after 4 attempts: http_429"), and logging
                # only "RuntimeError" cost a diagnostic round trip the first time
                # this fired in anger.
                "embedding batch failed; chunks will be stored without vectors: %s",
                exc,
                extra={"size": len(batch), "error": type(exc).__name__},
            )
            out.extend([None] * len(batch))
            continue

        vectors = [e.get("values") for e in data.get("embeddings", [])]
        if len(vectors) != len(batch):
            # A short response would silently misalign vectors with chunks, which
            # is worse than no vectors: every chunk would carry someone else's.
            logger.error(
                "embedding count does not match the batch; discarding the batch",
                extra={"sent": len(batch), "received": len(vectors)},
            )
            out.extend([None] * len(batch))
            continue
        out.extend(_normalise(v) if v else None for v in vectors)
    return out


async def embed_query(client: httpx.AsyncClient, text: str) -> list[float] | None:
    """Embed a question for retrieval.

    `TASK_QUERY`, not `TASK_DOCUMENT` -- see the note at the top of this module.
    Returns None rather than raising so the caller can fall through to the
    keyword arm: a KB lookup that cannot embed is still a usable KB lookup.
    """
    body = {
        "content": {"parts": [{"text": text}]},
        "taskType": TASK_QUERY,
        "outputDimensionality": DIMENSIONS,
    }
    try:
        data = await _post(client, f"models/{MODEL}:embedContent", body)
    except EmbeddingUnavailable:
        return None
    except Exception as exc:
        logger.warning("query embedding failed", extra={"error": type(exc).__name__})
        return None
    values = (data.get("embedding") or {}).get("values")
    return _normalise(values) if values else None


def to_pgvector(vector: list[float] | None) -> str | None:
    """Render a vector in the literal form pgvector parses: `[1,2,3]`."""
    if not vector:
        return None
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
