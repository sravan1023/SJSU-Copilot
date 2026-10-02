# Chat latency: before and after the faster-chat work

**Before** is commit `e3fe709`, with its retired Llama model ids switched to
`openai/gpt-oss-120b` / `openai/gpt-oss-20b` and nothing else changed. **After**
is the current code. Both ran the same questions from `questions.jsonl` on the
same machine on 2026-09-21.

## Mock provider (`bench/serve_mock.py`)

Same mocks for both: search 1.8 s per call, page fetch 0.7 s, provider time to
first token 450 ms, all ±30%. 40 requests at concurrency 1, and 120 at each of
10, 25 and 50. Mock mode measures the app's own overhead and its behaviour under
load, not the provider's speed.

| p50 / p95, ms | c=1 | c=10 | c=25 | c=50 |
|---|---|---|---|---|
| response headers, before | 4,086 / 4,351 | 4,053 / 4,524 | 4,361 / 6,382 | 7,532 / 11,353 |
| response headers, after | **3 / 3** | **3 / 33** | **15 / 102** | **118 / 216** |
| first answer token, before | 4,582 / 4,835 | 4,566 / 5,047 | 4,944 / 6,940 | 8,295 / 12,106 |
| first answer token, after | **3,909 / 4,712** | **4,007 / 4,528** | **4,186 / 5,493** | **5,440 / 6,868** |
| answer complete, before | 5,158 / 5,512 | 5,129 / 5,673 | 5,572 / 7,778 | 9,007 / 12,565 |
| answer complete, after | **4,519 / 5,349** | **4,593 / 5,232** | **4,902 / 5,999** | **6,126 / 7,563** |
| errors, before / after | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

- **Response headers** measure how long the browser waits with nothing to show.
  Before, nothing was sent until retrieval finished; now status frames go out
  immediately.
- **Sources per grounded answer:** 5.00 before and after at every level. After
  answers 4 of every 40 questions without retrieval: the meta turns ("make that
  shorter", "summarize as bullets") that before paid for a full search.
- **The provider saw at most 10 concurrent streams before, 30 after, at c=50.**
  Before ran searches on Python's default thread pool (10 threads here) with no
  deadline, so requests queued behind one another.
- **At c=50, after trades some page content for speed:** 92 of about 540 page
  fetches fell back to their search snippet at the 6 s retrieval deadline. Before
  never fell back, but first token was 2.9 s slower at p50 and 5.2 s at p95. Up to
  c=25 after had no fallbacks and no abandoned searches.
- **Pool split, measured separately:** before searches got their own thread pool,
  the same code reached first token at 6,559 ms p50 at c=25 (vs 4,186 after) and
  abandoned a search on every one of 120 requests (vs 0 after).

## Real provider (Groq free tier)

8 questions, 2 per audience, concurrency 1, 35 s apart. After ran at 19:35 and
before at 22:35 local time; web search latency varies with the time of day, so
treat small differences as noise.

| | before | after |
|---|---|---|
| response headers p50 / p95 | 5,334 / 9,874 ms | **7 / 52 ms** |
| first answer token p50 / p95 | 6,144 / 10,938 ms | **5,476 / 7,212 ms** |
| answer complete p50 / p95 | 7,451 / 11,722 ms | **6,609 / 8,435 ms** |
| failed requests | **1 of 8**: Groq 413, "Request too large … TPM: Limit 8000, Requested 8308" | 0 of 8 |
| grounded answers citing sources as `[N]` | 2 of 7 | **6 of 7** (the seventh correctly refused) |
| answers showing raw `【2†L31-L33】` markers | 3 | **0** |
| "say that again but shorter" (meta turn), first token | 5,947 ms (ran a full search) | **482 ms** (no search) |

- Before's 413: up to 24,000 characters of retrieved context plus a
  2,048-token answer budget made a single request larger than the free tier's
  8,000 tokens per minute. After fits every request inside one token budget.
- Before's browser code also rendered that error as a blank answer and saved it.

## Persistence (browser)

Measured by `UI/src/services/telemetryService.ts`, 2 sends in the user's own
session: the message appears at once (0.0–0.4 ms), is saved at 193–594 ms, and
the answer is saved 212–256 ms after the stream ends. Before made 4 serial
Supabase round trips per exchange and refetched the whole sidebar after every
message, and the user's own message waited behind 2–3 of those round trips.

That session also showed the chat request waiting 357–903 ms for the
user-message save and the behaviour/memory lookups. The save now runs in
parallel with the chat request; the next browser session's `client timings`
will show the effect.

## What this does not measure

Answer quality beyond sources and citations: there is no judged comparison.
Real-provider runs are single-user; concurrency behaviour comes from the mock
runs only.
