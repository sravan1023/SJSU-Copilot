# Benchmark summary

Generated 2026-09-22T05:42:06+00:00.

## mock · concurrency 1 · model quality

40 requests, 40 joined to server timings, errors: none. Grounded answers citing [N]: 1.0; answers with raw 【】 markers: 0.

| Metric | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|
| response headers | 40 | 3 | 3 | 3 |
| first status frame | 40 | 3 | 4 | 4 |
| first answer token | 40 | 3,909 | 4,712 | 4,775 |
| answer complete | 40 | 4,519 | 5,349 | 5,460 |
| server total | 40 | 4,515 | 5,346 | 5,456 |
| behaviour settings | 40 | 0 | 0 | 0 |
| retrieval, total | 40 | 3,476 | 4,188 | 4,310 |
| query rewrite | 9 | 428 | 576 | 584 |
| search (both, wall) | 36 | 1,966 | 2,251 | 2,338 |
| page fetch (wall) | 36 | 1,476 | 1,752 | 1,821 |
| HTML extract (summed) | 36 | 86 | 114 | 123 |
| context assembly | 36 | 0 | 0 | 0 |
| provider time to first token | 40 | 427 | 584 | 590 |
| provider streaming | 40 | 595 | 766 | 770 |

**By conversation** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| existing | 16 | 3,832 | 4,424 | 4,506 | 5,102 |
| new | 24 | 3,991 | 4,757 | 4,552 | 5,331 |

**By retrieval** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| no-rag | 4 | 558 | 586 | 1,140 | 1,202 |
| rag | 36 | 3,991 | 4,724 | 4,556 | 5,358 |

**By audience** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| alumni | 10 | 3,586 | 4,248 | 4,319 | 4,787 |
| faculty | 10 | 3,955 | 4,573 | 4,499 | 5,084 |
| guest | 10 | 3,862 | 4,357 | 4,480 | 5,021 |
| student | 10 | 4,126 | 4,774 | 4,627 | 5,453 |

**By kind** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| faq | 8 | 3,916 | 4,192 | 4,382 | 4,682 |
| followup | 8 | 3,964 | 4,307 | 4,612 | 4,950 |
| fresh | 8 | 3,950 | 4,305 | 4,552 | 4,873 |
| long | 8 | 2,059 | 4,517 | 2,742 | 5,204 |
| restricted | 8 | 4,447 | 4,776 | 4,999 | 5,449 |

## mock · concurrency 10 · model quality

120 requests, 120 joined to server timings, errors: none. Grounded answers citing [N]: 1.0; answers with raw 【】 markers: 0.

| Metric | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|
| response headers | 120 | 3 | 33 | 39 |
| first status frame | 120 | 3 | 36 | 41 |
| first answer token | 120 | 4,007 | 4,528 | 4,837 |
| answer complete | 120 | 4,593 | 5,232 | 5,455 |
| server total | 120 | 4,579 | 5,211 | 5,451 |
| behaviour settings | 120 | 0 | 0 | 0 |
| retrieval, total | 120 | 3,529 | 4,018 | 4,382 |
| query rewrite | 27 | 449 | 594 | 604 |
| search (both, wall) | 108 | 1,993 | 2,268 | 2,354 |
| page fetch (wall) | 108 | 1,482 | 1,689 | 1,794 |
| HTML extract (summed) | 108 | 110 | 164 | 220 |
| context assembly | 108 | 0 | 0 | 0 |
| provider time to first token | 120 | 468 | 586 | 604 |
| provider streaming | 120 | 611 | 762 | 800 |

**By conversation** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| existing | 48 | 3,908 | 4,660 | 4,560 | 5,259 |
| new | 72 | 4,027 | 4,471 | 4,616 | 5,194 |

**By retrieval** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| no-rag | 12 | 436 | 569 | 1,026 | 1,251 |
| rag | 108 | 4,020 | 4,593 | 4,626 | 5,239 |

**By audience** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| alumni | 30 | 4,030 | 4,463 | 4,669 | 5,182 |
| faculty | 30 | 3,914 | 4,473 | 4,481 | 5,212 |
| guest | 30 | 3,927 | 4,381 | 4,562 | 5,021 |
| student | 30 | 4,105 | 4,737 | 4,747 | 5,292 |

**By kind** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| faq | 24 | 3,968 | 4,377 | 4,545 | 5,006 |
| followup | 24 | 4,043 | 4,771 | 4,658 | 5,415 |
| fresh | 24 | 4,013 | 4,318 | 4,605 | 4,787 |
| long | 24 | 2,186 | 4,172 | 2,794 | 5,020 |
| restricted | 24 | 4,205 | 4,779 | 4,837 | 5,318 |

## mock · concurrency 25 · model quality

120 requests, 120 joined to server timings, errors: none. Grounded answers citing [N]: 1.0; answers with raw 【】 markers: 0.

| Metric | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|
| response headers | 120 | 15 | 102 | 179 |
| first status frame | 120 | 19 | 116 | 212 |
| first answer token | 120 | 4,186 | 5,493 | 5,991 |
| answer complete | 120 | 4,902 | 5,999 | 6,583 |
| server total | 120 | 4,845 | 5,948 | 6,530 |
| behaviour settings | 120 | 0 | 0 | 0 |
| retrieval, total | 120 | 3,676 | 4,948 | 5,470 |
| query rewrite | 27 | 518 | 716 | 784 |
| search (both, wall) | 108 | 2,098 | 2,917 | 3,193 |
| page fetch (wall) | 108 | 1,580 | 1,868 | 1,966 |
| HTML extract (summed) | 108 | 204 | 369 | 448 |
| context assembly | 108 | 0 | 0 | 0 |
| provider time to first token | 120 | 488 | 636 | 728 |
| provider streaming | 120 | 634 | 805 | 942 |

**By conversation** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| existing | 48 | 4,162 | 5,755 | 4,862 | 6,314 |
| new | 72 | 4,196 | 5,172 | 4,923 | 5,743 |

**By retrieval** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| no-rag | 12 | 506 | 625 | 1,071 | 1,325 |
| rag | 108 | 4,273 | 5,500 | 4,949 | 6,016 |

**By audience** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| alumni | 30 | 4,103 | 4,927 | 4,756 | 5,544 |
| faculty | 30 | 4,200 | 5,025 | 4,831 | 5,694 |
| guest | 30 | 4,166 | 4,780 | 4,910 | 5,439 |
| student | 30 | 4,686 | 5,747 | 5,226 | 6,409 |

**By kind** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| faq | 24 | 4,151 | 5,153 | 4,866 | 5,649 |
| followup | 24 | 4,322 | 5,395 | 4,940 | 5,928 |
| fresh | 24 | 4,091 | 4,654 | 4,844 | 5,244 |
| long | 24 | 2,299 | 5,858 | 2,975 | 6,457 |
| restricted | 24 | 4,602 | 5,453 | 5,153 | 5,986 |

## mock · concurrency 50 · model quality

120 requests, 120 joined to server timings, errors: none. Grounded answers citing [N]: 1.0; answers with raw 【】 markers: 0.

| Metric | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|
| response headers | 120 | 118 | 216 | 357 |
| first status frame | 120 | 127 | 265 | 480 |
| first answer token | 120 | 5,440 | 6,868 | 7,012 |
| answer complete | 120 | 6,126 | 7,563 | 7,730 |
| server total | 120 | 5,904 | 7,485 | 7,711 |
| behaviour settings | 120 | 0 | 0 | 0 |
| retrieval, total | 120 | 4,584 | 6,054 | 6,301 |
| query rewrite | 27 | 573 | 787 | 828 |
| search (both, wall) | 108 | 2,787 | 5,205 | 5,867 |
| page fetch (wall) | 106 | 1,786 | 2,163 | 2,217 |
| HTML extract (summed) | 93 | 422 | 726 | 755 |
| context assembly | 108 | 0 | 0 | 0 |
| provider time to first token | 120 | 581 | 876 | 954 |
| provider streaming | 120 | 730 | 1,041 | 1,168 |

**By conversation** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| existing | 48 | 5,223 | 6,841 | 5,935 | 7,556 |
| new | 72 | 5,577 | 6,855 | 6,292 | 7,547 |

**By retrieval** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| no-rag | 12 | 597 | 910 | 1,323 | 1,946 |
| rag | 108 | 5,886 | 6,871 | 6,646 | 7,563 |

**By audience** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| alumni | 30 | 5,232 | 6,899 | 5,871 | 7,720 |
| faculty | 30 | 5,115 | 6,757 | 5,811 | 7,510 |
| guest | 30 | 5,711 | 6,490 | 6,305 | 7,386 |
| student | 30 | 6,479 | 6,928 | 7,324 | 7,563 |

**By kind** (first token / complete, p50 · p95 ms)

| Segment | n | first token p50 | p95 | complete p50 | p95 |
|---|---:|---:|---:|---:|---:|
| faq | 24 | 5,352 | 6,803 | 5,916 | 7,526 |
| followup | 24 | 6,007 | 6,858 | 6,722 | 7,610 |
| fresh | 24 | 5,132 | 6,775 | 5,817 | 7,673 |
| long | 24 | 2,858 | 6,782 | 3,594 | 7,536 |
| restricted | 24 | 6,389 | 6,862 | 7,118 | 7,520 |

## Not measured here

- **persistence**: Supabase saves happen in the browser; measured there by UI/src/services/telemetryService.ts (marks user_saved, assistant_saved), not by this harness.
- **guest_vs_registered**: No guest sessions exist yet.
- **deployment**: No deployment config yet (warm/cold instances, region, proxy buffering).
