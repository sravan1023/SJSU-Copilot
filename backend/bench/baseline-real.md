# Benchmark summary

Generated 2026-09-22T02:57:21+00:00.

## real · concurrency 1 · model quality

8 requests, 8 joined to server timings, errors: none.

| Metric | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|
| response headers | 8 | 7 | 52 | 70 |
| first status frame | 8 | 7 | 52 | 71 |
| first answer token | 8 | 5,476 | 7,212 | 7,786 |
| answer complete | 8 | 6,609 | 8,435 | 9,103 |
| server total | 8 | 6,602 | 8,408 | 9,067 |
| behaviour settings | 8 | 0 | 0 | 0 |
| retrieval, total | 8 | 4,993 | 5,740 | 5,960 |
| query rewrite | 1 | 422 | 422 | 422 |
| search (both, wall) | 7 | 3,977 | 4,270 | 4,288 |
| page fetch (wall) | 7 | 1,008 | 1,953 | 2,174 |
| HTML extract (summed) | 7 | 330 | 1,454 | 1,781 |
| context assembly | 7 | 0 | 0 | 0 |
| provider time to first token | 8 | 563 | 1,511 | 1,815 |
| provider streaming | 8 | 914 | 1,826 | 1,981 |

**By conversation** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| existing | 2 | 3,180 | 5,609 | 3,555 | 6,238 |
| new | 6 | 5,476 | 7,417 | 6,783 | 8,674 |

**By retrieval** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| no-rag | 1 | 482 | 482 | 574 | 574 |
| rag | 7 | 5,512 | 7,314 | 6,682 | 8,554 |

**By audience** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| alumni | 2 | 5,695 | 5,860 | 6,609 | 6,675 |
| faculty | 2 | 4,206 | 7,557 | 4,922 | 8,835 |
| guest | 2 | 5,430 | 5,440 | 6,175 | 6,814 |
| student | 2 | 5,371 | 5,830 | 6,530 | 6,848 |

**By kind** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| faq | 3 | 5,419 | 5,502 | 6,883 | 6,885 |
| followup | 1 | 5,879 | 5,879 | 6,536 | 6,536 |
| fresh | 2 | 6,905 | 7,826 | 7,723 | 9,115 |
| long | 1 | 482 | 482 | 574 | 574 |
| restricted | 1 | 5,441 | 5,441 | 5,465 | 5,465 |

## Not measured here

- **persistence**: Supabase saves happen in the browser; measured there by UI/src/services/telemetryService.ts (marks user_saved, assistant_saved), not by this harness.
- **guest_vs_registered**: No guest sessions exist yet.
- **deployment**: No deployment config yet (warm/cold instances, region, proxy buffering).
