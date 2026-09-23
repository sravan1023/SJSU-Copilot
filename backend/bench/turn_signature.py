"""Behavioural signature of the client's chat turns, for before/after refactors.

The UI has no test framework, so a refactor of the three turn handlers in
UI/src/App.jsx has no automated safety net. This is the cheapest one available:
`telemetryService.startTurn` already posts {kind, outcome, marks} for every
send / regenerate / edit, routers/telemetry.py already logs one JSON line per
event on logger "client_timings", and those lines say which stages a turn
actually reached.

Mark *values* vary run to run and are useless for comparison. Mark *names* and
the outcome do not: a lost `user_saved`, a missing `assistant_saved`, or an
`error` that silently became `ok` all change the signature. So the signature is
the set of (kind, outcome, sorted mark names), deduplicated and sorted -- drive
the same matrix twice and the two files are byte-identical unless behaviour
moved.

    # 1. capture the current tree
    python -m bench.turn_signature --capture bench/results/after.jsonl \
                                   --out bench/results/after.sig

    # 2. put the old file back, capture again, restore (no git state touched)
    #    see bench/README.md for the exact copy/restore dance

    # 3. compare
    python -m bench.turn_signature --diff bench/results/before.sig \
                                          bench/results/after.sig

This is a Phase 2 tool. Delete it when the UI grows a real test runner.
"""
import argparse
import json
import sys
from pathlib import Path

# Marks that only appear when the turn got far enough to have them. Listed here
# so --explain can say which stage a missing name corresponds to, rather than
# leaving the reader to diff two opaque strings.
STAGE_NOTES = {
    "ack": "the user's own message was echoed",
    "user_saved": "the user row came back from Supabase",
    "context_ready": "behaviour + memory resolved",
    "request_sent": "the fetch left the browser",
    "auth_token": "the Authorization header was produced",
    "headers": "response headers arrived",
    "first_status": "the first SSE status frame arrived",
    "first_token": "the first answer token arrived",
    "stream_done": "the stream closed",
    "assistant_saved": "the assistant row came back from Supabase",
    "total": "always present; finish() adds it",
}


def signatures(log_path: Path) -> list[str]:
    """Every distinct (kind, outcome, marks) shape in one captured log."""
    seen = set()
    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw or not raw.startswith("{"):
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if line.get("logger") != "client_timings":
            continue
        marks = line.get("marks") or {}
        if not isinstance(marks, dict):
            continue
        seen.add(
            "{kind:<10} outcome={outcome:<8} marks={names}".format(
                kind=line.get("kind", "?"),
                outcome=line.get("outcome", "?"),
                names=",".join(sorted(marks)),
            )
        )
    return sorted(seen)


def _explain(names: set[str]) -> str:
    known = [f"      {n} -- {STAGE_NOTES[n]}" for n in sorted(names) if n in STAGE_NOTES]
    unknown = sorted(n for n in names if n not in STAGE_NOTES)
    if unknown:
        known.append(f"      (unrecognised: {', '.join(unknown)})")
    return "\n".join(known)


def diff(before: Path, after: Path) -> int:
    old = set(before.read_text(encoding="utf-8").splitlines())
    new = set(after.read_text(encoding="utf-8").splitlines())

    gone = sorted(old - new)
    added = sorted(new - old)

    if not gone and not added:
        shared = len(old)
        print(f"identical: {shared} turn signature(s) matched, none gained or lost")
        return 0

    print("SIGNATURE CHANGED\n")
    for line in gone:
        print(f"  only before: {line}")
    for line in added:
        print(f"  only after : {line}")

    # Name-level detail, which is what actually tells you what broke.
    def marks_of(lines):
        out = set()
        for line in lines:
            _, _, tail = line.partition("marks=")
            out |= {n for n in tail.split(",") if n}
        return out

    lost = marks_of(gone) - marks_of(added)
    won = marks_of(added) - marks_of(gone)
    if lost:
        print("\n  marks present before and absent after:")
        print(_explain(lost))
    if won:
        print("\n  marks absent before and present after:")
        print(_explain(won))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--capture",
        type=Path,
        help="a backend log (JSON lines on stdout, redirected to a file)",
    )
    parser.add_argument("--out", type=Path, help="where to write the signature")
    parser.add_argument(
        "--diff",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="compare two signature files; exit 1 if they differ",
    )
    args = parser.parse_args()

    if args.diff:
        return diff(*args.diff)

    if not args.capture:
        parser.error("pass --capture LOG [--out SIG] or --diff BEFORE AFTER")

    if not args.capture.exists():
        print(f"no such log: {args.capture}", file=sys.stderr)
        return 2

    lines = signatures(args.capture)
    if not lines:
        print(
            f"no client_timings lines in {args.capture}.\n"
            "The browser flushes telemetry every 10s, at 20 events, and on "
            "pagehide -- close the tab before reading the log.",
            file=sys.stderr,
        )
        return 2

    body = "\n".join(lines) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"{len(lines)} signature(s) -> {args.out}")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
