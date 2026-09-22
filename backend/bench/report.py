"""
Summarise benchmark runs: p50 / p95 / p99 per stage, overall and by segment.

Joins run_bench results to the server's "request timings" log lines by
request_id, so each request has both what the client saw and where the server
spent the time.

    python -m bench.report bench/results/mock-*.jsonl \
        --server-log bench/results/server-mock.jsonl \
        --json bench/baseline.json --markdown bench/baseline.md

Segments: conversation (new / existing), retrieval (rag / no-rag), audience,
kind. There are no guest sessions yet, so a guest vs registered split is
reported as not measurable.
"""
import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CLIENT_METRICS = {
    "headers_ms": "response headers",
    "first_status_ms": "first status frame",
    "first_token_ms": "first answer token",
    "done_ms": "answer complete",
}

SERVER_STAGES = {
    "behavior_compute": "behaviour settings",
    "rag.total": "retrieval, total",
    "rag.rewrite": "query rewrite",
    "rag.search.gather": "search (both, wall)",
    "rag.crawl.total": "page fetch (wall)",
    "rag.extract": "HTML extract (summed)",
    "rag.assemble": "context assembly",
}

NOT_MEASURED = {
    "persistence": "Supabase saves happen in the browser; measured there by "
                   "UI/src/services/telemetryService.ts (marks user_saved, assistant_saved), "
                   "not by this harness.",
    "guest_vs_registered": "No guest sessions exist yet.",
    "deployment": "No deployment config yet (warm/cold instances, region, proxy buffering).",
}


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", nargs="+", type=Path, help="run_bench output files")
    p.add_argument("--server-log", nargs="*", type=Path, default=[], help="backend JSON log files")
    p.add_argument("--json", type=Path, help="write the summary here")
    p.add_argument("--markdown", type=Path, help="write the tables here too")
    return p.parse_args()


def _load_jsonl(path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _server_lines(paths):
    by_id = {}
    for path in paths:
        for row in _load_jsonl(path):
            if row.get("msg") == "request timings" and row.get("request_id"):
                by_id[row["request_id"]] = row
    return by_id


def _quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        p50 = p95 = p99 = values[0]
    else:
        q = statistics.quantiles(values, n=100, method="inclusive")
        p50, p95, p99 = q[49], q[94], q[98]
    return {"n": len(values), "p50": round(p50, 1), "p95": round(p95, 1), "p99": round(p99, 1)}


def _metrics(rows):
    out = {}
    for key, label in CLIENT_METRICS.items():
        q = _quantiles(r.get(key) for r in rows)
        if q:
            out[f"client.{key}"] = {"label": label, **q}

    served = [r["server"] for r in rows if r.get("server")]
    q = _quantiles(s.get("total_ms") for s in served)
    if q:
        out["server.total_ms"] = {"label": "server total", **q}
    for stage, label in SERVER_STAGES.items():
        q = _quantiles(s["stages"].get(stage) for s in served if stage in s.get("stages", {}))
        if q:
            out[f"server.{stage}"] = {"label": label, **q}

    def _span(s, a, b):
        marks = s.get("marks", {})
        return marks[b] - marks[a] if a in marks and b in marks else None

    for key, (a, b), label in (
        ("llm.ttft", ("llm.request_sent", "llm.first_token"), "provider time to first token"),
        ("llm.stream", ("llm.first_token", "llm.last_token"), "provider streaming"),
    ):
        q = _quantiles(_span(s, a, b) for s in served)
        if q:
            out[f"server.{key}"] = {"label": label, **q}
    return out


def _retrieval(row):
    server = row.get("server")
    if server:
        return "rag" if server.get("counters", {}).get("sources_used", 0) > 0 else "no-rag"
    return "rag" if row.get("sources", 0) > 0 else "no-rag"


def _group_summary(rows):
    errors = Counter(r["error"] for r in rows if r.get("error"))
    segments = {}
    for name, key in (
        ("conversation", lambda r: r["conversation"]),
        ("retrieval", _retrieval),
        ("audience", lambda r: r["audience"]),
        ("kind", lambda r: r["kind"]),
    ):
        buckets = defaultdict(list)
        for r in rows:
            buckets[key(r)].append(r)
        segments[name] = {
            value: {
                "n": len(bucket),
                "first_token_ms": _quantiles(r.get("first_token_ms") for r in bucket),
                "done_ms": _quantiles(r.get("done_ms") for r in bucket),
            }
            for value, bucket in sorted(buckets.items())
        }
    joined = sum(1 for r in rows if r.get("server"))
    # Of the answers built on sources, how many cite them in the [N] form.
    grounded = [r for r in rows if r.get("sources", 0) > 0 and not r.get("error") and "citations" in r]
    return {
        "n": len(rows),
        "joined_to_server_log": joined,
        "errors": dict(errors),
        "citation_rate": round(sum(1 for r in grounded if r["citations"] > 0) / len(grounded), 3) if grounded else None,
        "answers_with_raw_markers": sum(1 for r in rows if r.get("raw_citation_markers", 0) > 0),
        "metrics": _metrics(rows),
        "segments": segments,
    }


def _fmt(q, field):
    return "—" if not q else f"{q[field]:,.0f}"


def _markdown(summary):
    lines = [f"# Benchmark summary\n\nGenerated {summary['generated_at']}.\n"]
    for g in summary["groups"]:
        lines.append(
            f"## {g['mode']} · concurrency {g['concurrency']} · model {g['model']}\n\n"
            f"{g['n']} requests, {g['joined_to_server_log']} joined to server timings, "
            f"errors: {g['errors'] or 'none'}. "
            f"Grounded answers citing [N]: {g['citation_rate'] if g['citation_rate'] is not None else 'n/a'}; "
            f"answers with raw 【】 markers: {g['answers_with_raw_markers']}.\n"
        )
        lines.append("| Metric | n | p50 ms | p95 ms | p99 ms |\n|---|---:|---:|---:|---:|")
        for m in g["metrics"].values():
            lines.append(f"| {m['label']} | {m['n']} | {m['p50']:,.0f} | {m['p95']:,.0f} | {m['p99']:,.0f} |")
        lines.append("")
        for seg_name, seg in g["segments"].items():
            lines.append(f"**By {seg_name}** (first token / complete, p50 · p95 ms)\n")
            lines.append("| Segment | n | first token p50 | p95 | complete p50 | p95 |\n|---|---:|---:|---:|---:|---:|")
            for value, s in seg.items():
                ft, dn = s["first_token_ms"], s["done_ms"]
                lines.append(f"| {value} | {s['n']} | {_fmt(ft, 'p50')} | {_fmt(ft, 'p95')} | {_fmt(dn, 'p50')} | {_fmt(dn, 'p95')} |")
            lines.append("")
    lines.append("## Not measured here\n")
    for key, why in summary["not_measured"].items():
        lines.append(f"- **{key}**: {why}")
    return "\n".join(lines) + "\n"


def main():
    args = _parse_args()
    server = _server_lines(args.server_log)
    groups = defaultdict(list)
    inputs = []
    for path in args.results:
        inputs.append(str(path))
        for row in _load_jsonl(path):
            row["server"] = server.get(row["request_id"])
            groups[(row["mode"], row["concurrency"], row["model"])].append(row)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": inputs,
        "groups": [
            {"mode": mode, "concurrency": conc, "model": model, **_group_summary(rows)}
            for (mode, conc, model), rows in sorted(groups.items())
        ],
        "not_measured": NOT_MEASURED,
    }
    text = _markdown(summary)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
