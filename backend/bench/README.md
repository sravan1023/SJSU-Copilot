# Chat latency benchmark

Measures `/api/chat` end to end: what the client sees (headers, first status,
first answer token, answer complete) joined by `request_id` to where the server
spent the time (the `request timings` log line from `observability.py`).

All commands run from `backend/`, with the virtualenv's Python
(`.venv\Scripts\python` on Windows).

## The rule

**Concurrency sweeps are mock-only. Real-provider runs stay at concurrency 1–3.**

Groq's free tier allows 30 requests and 8,000 tokens per minute for the whole
organisation. One answer with retrieval uses roughly 4,000–5,500 tokens, so a
real run at concurrency 25 measures 429 handling and nothing else.
`run_bench --mode real` refuses anything above 3 unless you pass
`--allow-high-concurrency`.

## Mock mode

Runs the real app with the outside world swapped out inside that one process:
a fake Groq (`mock_upstream.py`), a blocking fake search on the same bounded
thread pool DDGS uses, and a fake page fetch that returns a ~45 KB page, so
HTML extraction still does real work. Delays default to what the real
provider showed on 2026-09-21 and are all flags (`--search-ms`, `--ttft-ms`, …).

```
python -m bench.serve_mock --log bench/results/server-mock.jsonl
python -m bench.run_bench --mode mock --concurrency 10 --repeat 3
curl http://127.0.0.1:8101/stats      # most streams the app held open at once
```

Mock mode measures the app's own overhead and how it behaves under load. It
says nothing about the provider's speed.

## Real mode

```
python -m uvicorn main:app --port 8000 > bench/results/server-real.jsonl
python -m bench.run_bench --mode real --limit 8 --pace 20
```

`--pace` spaces each worker's requests out, to stay inside the per-minute token
limit. A full 40-question real run is roughly 200k tokens. Check your daily
limits before running one.

## Report

```
python -m bench.report bench/results/mock-c*.jsonl --server-log bench/results/server-mock.jsonl \
    --json bench/baseline.json --markdown bench/baseline.md
```

This gives p50/p95/p99 for each client mark and server stage, overall and split
by conversation (new/existing), retrieval (rag/no-rag), audience and question
kind. Raw results in `bench/results/` are not committed. The summaries
(`baseline-mock.*`, `baseline-real.*`, `before-after.md`) are.

## Comparing two versions

`serve_mock --backend-dir PATH` serves another checkout's `backend/` behind the
same mocks, for example an older commit extracted with
`git archive <commit> backend | tar -x -C <dir>`. Run the same `run_bench`
sweep against each and compare. `before-after.md` was produced this way.

## Questions

`questions.jsonl` has 40 conversations: 4 audiences × 5 kinds × 2 (FAQ,
time-sensitive, ambiguous follow-up, long conversation, restricted request). The
same set can serve a model comparison. Edit `make_questions.py` and re-run it,
rather than editing the JSONL by hand.

## Turn signatures (refactor safety net)

`run_bench` measures *how long* a turn took. `turn_signature.py` answers a
different question — *which stages it reached* — and exists because the UI has
no test runner, so a refactor of the three handlers in `UI/src/App.jsx` has no
other automated check.

The signature is the set of `(kind, outcome, sorted mark names)` across a run.
Mark values move every run and are ignored; names and outcomes do not move
unless behaviour did.

**Capture without touching git state.** The backend writes JSON lines to
stdout, so redirect it:

```powershell
# --- after (the working tree) ---
.venv\Scripts\python -m uvicorn main:app --port 8000 *> bench\results\after.jsonl
#   in another shell: cd UI; npm run dev   -- then drive the matrix below
.venv\Scripts\python -m bench.turn_signature --capture bench\results\after.jsonl --out bench\results\after.sig

# --- before (the committed version) ---
Copy-Item ..\UI\src\App.jsx $env:TEMP\App.jsx.work
git -C .. checkout -- UI/src/App.jsx
#   restart both servers, drive the same matrix
.venv\Scripts\python -m bench.turn_signature --capture bench\results\before.jsonl --out bench\results\before.sig
Copy-Item $env:TEMP\App.jsx.work ..\UI\src\App.jsx      # restore

.venv\Scripts\python -m bench.turn_signature --diff bench\results\before.sig bench\results\after.sig
```

Exit 0 and "identical" means every turn shape survived. Exit 1 prints which
shapes appeared or vanished and which stage each missing mark belongs to.

**The matrix** — 9 cases, {send, regenerate, edit} × {happy, stop mid-stream,
forced network error}. For the error column, run the dev server with
`VITE_API_BASE=http://127.0.0.1:9` so the fetch fails fast.

**Close the tab before reading the log.** `telemetryService` flushes every 10s,
at 20 queued events, and on `pagehide`; a tab left open can be holding the last
batch.

## Not measured here

- **Persistence**: Supabase saves happen in the browser.
  `UI/src/services/telemetryService.ts` records `user_saved` and
  `assistant_saved` and posts them to `/api/telemetry`, which logs them as
  `client timings` lines. `turn_signature.py` above reads exactly those lines.
- **Guest vs registered**: no guest sessions exist yet.
- **Deployment effects** (cold starts, region, proxy buffering): no deployment
  config exists yet.
