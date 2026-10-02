"""
Offline precision report for the registration intent router (R7).

Runs services.reg_intent.classify over bench/router_questions.jsonl and scores
it against the labels. No database, no network, no tokens.

What gets classified: by default the text the chat hook will actually see,
i.e. services.web_search.prepare_rag_query(messages). For single-turn rows
that is the question itself, unless the conversational/meta gate drops it (then
the hook never runs and the route is "none"). For multi-turn rows a short
follow-up gets the previous user turn prepended. `--input raw` classifies the
last user turn alone instead.

The bar (SERVICES_BUILD_PLAN decision 2): card precision >= 0.98 on this set
AND no policy (expected abstain) question carded.

Card correctness: the route is card, the label is card, and the card names the
labelled target: event_keys == (key,) for calendar cards, or intent final_exam
with the labelled course code for exam cards. "Strict" additionally requires
the term hint to match where the question names a term, because a card for the
right event in the wrong term is still a wrong date.

    python -m bench.make_router_questions        # regenerate the labels first
    python -m bench.eval_router                   # the report
    python -m bench.eval_router --json bench/results/router-eval.json --check
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

from services.reg_intent import classify
from services.web_search import prepare_rag_query

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "router_questions.jsonl"
CLASSIFIER_FILES = ("services/reg_intent.py", "campus/registration/event_keys.json")
ROUTES = ("card", "context", "abstain", "none")
FALLTHROUGH = ("abstain", "none")  # both reach KB / live search unchanged
BAR_PRECISION = 0.98

# Miss classes, most harmful first.
SEVERITY = [
    ("wrong_card_policy", "WRONG CARD on a policy question (fails the bar outright)"),
    ("wrong_card_none", "WRONG CARD on a non-registration question"),
    ("wrong_card_context", "WRONG CARD where context was right (one row shown for a multi-row answer)"),
    ("wrong_card_target", "WRONG CARD: right route, wrong event key or course code"),
    ("context_on_fallthrough", "context where it should fall through (live retrieval skipped, model "
                               "answers from calendar rows only)"),
    ("card_as_context", "card expected, got context (safe: rows still ground the model)"),
    ("lookup_fell_through", "lookup expected, fell through to KB/live search (safe: today's behaviour)"),
    ("abstain_none_swap", "abstain vs none swapped (no behavioural difference)"),
]


def load(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def wilson_lower(k, n, z=1.96):
    """95% Wilson lower bound: how low the true rate could plausibly be given k/n."""
    if n == 0:
        return float("nan")
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def hook_text(row, mode):
    if mode == "raw":
        return row["text"]
    messages = row.get("messages") or [{"role": "user", "content": row["text"]}]
    return prepare_rag_query(messages)


def classify_row(row, mode):
    text = hook_text(row, mode)
    # The hook prepends earlier turns for a follow-up; the chat router (R8)
    # passes that fact as `followup`, which caps the route at context.
    followup = mode == "hook" and text is not None and text != row["text"]
    result = classify(text, followup=followup) if text else None
    return text, result


def card_target_ok(row, r):
    if row["expected_route"] != "card" or r is None or r.route != "card":
        return False
    if row.get("expected_course_code"):
        return r.intent == "final_exam" and r.course_code == row["expected_course_code"]
    return tuple(r.event_keys) == tuple(row["expected_event_keys"])


def term_ok(row, r):
    if "expected_term_hint" not in row:
        return True
    return (r.term_hint if r else None) == row["expected_term_hint"]


def miss_class(row, r):
    exp = row["expected_route"]
    act = r.route if r else "none"
    if act == "card" and not card_target_ok(row, r):
        return {"abstain": "wrong_card_policy", "none": "wrong_card_none",
                "context": "wrong_card_context", "card": "wrong_card_target"}[exp]
    if exp == act:
        return None
    if exp in FALLTHROUGH and act == "context":
        return "context_on_fallthrough"
    if exp == "card" and act == "context":
        return "card_as_context"
    if exp in ("card", "context") and act in FALLTHROUGH:
        return "lookup_fell_through"
    if exp in FALLTHROUGH and act in FALLTHROUGH:
        return "abstain_none_swap"
    raise AssertionError((row["id"], exp, act))


def describe(r):
    if r is None:
        return "none"
    bits = [r.route, r.intent]
    if r.event_keys:
        bits.append(",".join(r.event_keys))
    if r.course_code:
        bits.append(r.course_code)
    if r.term_hint:
        bits.append(f"term={r.term_hint}")
    return " ".join(bits)


def describe_expected(row):
    bits = [row["expected_route"]]
    if row["expected_event_keys"]:
        bits.append(",".join(row["expected_event_keys"]))
    if row.get("expected_course_code"):
        bits.append(row["expected_course_code"])
    if row.get("expected_term_hint"):
        bits.append(f"term={row['expected_term_hint']}")
    return " ".join(bits)


def score(rows, results):
    """The headline numbers for one subset of rows."""
    carded = [(row, r) for row, r in zip(rows, results) if r is not None and r.route == "card"]
    correct = [(row, r) for row, r in carded if card_target_ok(row, r)]
    strict = [(row, r) for row, r in correct if term_ok(row, r)]
    expected_cards = [row for row in rows if row["expected_route"] == "card"]
    policy_carded = [row for row, r in carded if row["expected_route"] == "abstain"]
    n_carded = len(carded)
    precision = len(correct) / n_carded if n_carded else float("nan")
    fall = [r for row, r in zip(rows, results) if row["expected_route"] in FALLTHROUGH]
    return {
        "n": len(rows),
        "carded": n_carded,
        "cards_correct": len(correct),
        "cards_correct_strict": len(strict),
        "expected_cards": len(expected_cards),
        "card_precision": precision,
        "card_precision_wilson_lower": wilson_lower(len(correct), n_carded),
        "card_precision_strict": len(strict) / n_carded if n_carded else float("nan"),
        "card_recall": len(correct) / len(expected_cards) if expected_cards else float("nan"),
        "policy_carded": len(policy_carded),
        "fallthrough_expected": len(fall),
        "fallthrough_skipping_retrieval": sum(1 for r in fall if r is not None and r.route == "context"),
        "fallthrough_skipping_retrieval_if_context_only": sum(
            1 for r in fall if r is not None and r.route in ("card", "context")),
        "passes_bar": bool(n_carded) and precision >= BAR_PRECISION and not policy_carded,
    }


def confusion(rows, results):
    m = Counter()
    for row, r in zip(rows, results):
        m[(row["expected_route"], r.route if r else "none")] += 1
    return m


def git_provenance():
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=BACKEND, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "?"
    head = run("rev-parse", "--short", "HEAD")
    status = run("status", "--porcelain", "--", *CLASSIFIER_FILES)
    hashes = {
        f: hashlib.sha256((BACKEND / f).read_bytes()).hexdigest()[:12] for f in CLASSIFIER_FILES
    }
    return {"head": head, "classifier_files_dirty": bool(status), "sha256_12": hashes}


def fmt(x):
    return "n/a" if isinstance(x, float) and math.isnan(x) else f"{x:.3f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--input", choices=("hook", "raw"), default="hook",
                    help="hook: prepare_rag_query(messages), what chat will classify (default); "
                         "raw: the last user turn alone")
    ap.add_argument("--json", help="write per-row results and the summary here")
    ap.add_argument("--check", action="store_true", help="exit 1 if the bar is missed")
    args = ap.parse_args(argv)

    rows = load(args.questions)
    texts, results = zip(*(classify_row(row, args.input) for row in rows))
    other_mode = "raw" if args.input == "hook" else "hook"
    other = [classify_row(row, other_mode)[1] for row in rows]

    prov = git_provenance()
    qhash = hashlib.sha256(Path(args.questions).read_bytes()).hexdigest()[:12]
    print(f"router eval: {len(rows)} questions ({qhash}), input={args.input}")
    print(f"commit {prov['head']}; classifier files dirty/untracked: {prov['classifier_files_dirty']}; "
          + ", ".join(f"{k} {v}" for k, v in prov["sha256_12"].items()))

    # Confusion matrix.
    m = confusion(rows, results)
    print("\n## Confusion matrix (rows: expected, columns: actual)\n")
    print("| expected \\ actual | " + " | ".join(ROUTES) + " | total |")
    print("|---" * (len(ROUTES) + 2) + "|")
    for e in ROUTES:
        total = sum(m[(e, a)] for a in ROUTES)
        print(f"| **{e}** | " + " | ".join(str(m[(e, a)]) for a in ROUTES) + f" | {total} |")
    print("| total | " + " | ".join(str(sum(m[(e, a)] for e in ROUTES)) for a in ROUTES)
          + f" | {len(rows)} |")

    # Headline numbers, overall and without the contested labels.
    full = score(rows, results)
    keep = [i for i, row in enumerate(rows) if not row.get("contested")]
    uncontested = score([rows[i] for i in keep], [results[i] for i in keep])
    print("\n## Headline\n")
    print("| | all labels | contested excluded |")
    print("|---|---|---|")
    for label, key in [
        ("questions", "n"),
        ("carded", "carded"),
        ("cards correct (right key / course)", "cards_correct"),
        ("card precision", "card_precision"),
        ("card precision, 95% Wilson lower bound", "card_precision_wilson_lower"),
        ("cards correct incl. term hint", "cards_correct_strict"),
        ("card precision, strict (term hint too)", "card_precision_strict"),
        ("expected cards", "expected_cards"),
        ("card recall", "card_recall"),
        ("policy questions carded", "policy_carded"),
    ]:
        a, b = full[key], uncontested[key]
        print(f"| {label} | {fmt(a) if isinstance(a, float) else a} | {fmt(b) if isinstance(b, float) else b} |")
    verdict = "PASS" if full["passes_bar"] else "FAIL"
    print(f"\nBar (precision >= {BAR_PRECISION} and 0 policy carded): {verdict} "
          f"(contested excluded: {'PASS' if uncontested['passes_bar'] else 'FAIL'})")

    # Term hints.
    with_term = [(row, r) for row, r in zip(rows, results) if "expected_term_hint" in row]
    term_right = sum(term_ok(row, r) for row, r in with_term)
    routed = [(row, r) for row, r in with_term if r is not None]
    print(f"Term hint matches on questions that name a term: {term_right}/{len(with_term)} "
          f"({sum(term_ok(row, r) for row, r in routed)}/{len(routed)} of those the router routed; "
          f"the rest got no result at all)")

    # Decision 2's fallback is "ship context-only": every card becomes context.
    # Context skips live retrieval, so what matters then is how many questions
    # that should fall through would lose their KB / live-search grounding.
    fall = [(row, r) for row, r in zip(rows, results) if row["expected_route"] in FALLTHROUGH]
    lose_now = sum(1 for _row, r in fall if r is not None and r.route == "context")
    lose_ctx_only = sum(1 for _row, r in fall if r is not None and r.route in ("card", "context"))
    print(f"Fall-through questions (expected abstain/none) that skip retrieval: {lose_now}/{len(fall)} "
          f"as routed; {lose_ctx_only}/{len(fall)} if cards ship as context-only")

    # Per category.
    print("\n## By category\n")
    print("| category | n | exact | wrong cards | context on fall-through | safe misses |")
    print("|---|---|---|---|---|---|")
    cats = list(dict.fromkeys(row["category"] for row in rows))
    for cat in cats:
        idx = [i for i, row in enumerate(rows) if row["category"] == cat]
        classes = [miss_class(rows[i], results[i]) for i in idx]
        wrong = sum(1 for c in classes if c and c.startswith("wrong_card"))
        ctxfall = sum(1 for c in classes if c == "context_on_fallthrough")
        exact = sum(1 for c in classes if c is None)
        print(f"| {cat} | {len(idx)} | {exact} | {wrong} | {ctxfall} | {len(idx) - exact - wrong - ctxfall} |")

    # Every miss, grouped by severity.
    print("\n## Misses by severity\n")
    misses = {}
    for row, text, r in zip(rows, texts, results):
        c = miss_class(row, r)
        if c:
            misses.setdefault(c, []).append((row, text, r))
    for key, title in SEVERITY:
        items = misses.get(key, [])
        print(f"### {title}: {len(items)}\n")
        for row, text, r in items:
            seen = "" if text == row["text"] else f"  [hook saw: {text!r}]"
            flag = "  (contested)" if row.get("contested") else ""
            print(f"- `{row['id']}` {row['text']!r}{seen}\n  expected {describe_expected(row)}; "
                  f"got {describe(r)}{flag}")
        if items:
            print()

    # Term-hint misses on otherwise-correct cards.
    term_misses = [(row, r) for row, r in zip(rows, results) if card_target_ok(row, r) and not term_ok(row, r)]
    print(f"### correct card, wrong term hint: {len(term_misses)}\n")
    for row, r in term_misses:
        print(f"- `{row['id']}` {row['text']!r}: expected term={row['expected_term_hint']}, "
              f"got term={r.term_hint}")

    # Where the hook's view changes the outcome.
    diffs = [(row, r, o) for row, r, o in zip(rows, results, other)
             if (r.route if r else "none") != (o.route if o else "none")
             or (r and o and (r.event_keys, r.course_code) != (o.event_keys, o.course_code))]
    print(f"\n### {args.input} vs {other_mode} input differ: {len(diffs)}\n")
    for row, r, o in diffs:
        print(f"- `{row['id']}` {row['text']!r}: {args.input} {describe(r)}; {other_mode} {describe(o)}")

    if args.json:
        out = {
            "questions": args.questions,
            "questions_sha256_12": qhash,
            "input": args.input,
            "provenance": prov,
            "summary": full,
            "summary_contested_excluded": uncontested,
            "confusion": {f"{e}->{a}": m[(e, a)] for e in ROUTES for a in ROUTES},
            "rows": [
                {
                    "id": row["id"], "category": row["category"], "text": row["text"], "hook_text": text,
                    "expected": describe_expected(row), "actual": describe(r),
                    "miss": miss_class(row, r), "contested": bool(row.get("contested")),
                }
                for row, text, r in zip(rows, texts, results)
            ],
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.json}")

    if args.check and not full["passes_bar"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
