"""
Compare two `supabase db dump --schema public` outputs object by object.

The live database was built by hand and had never
been tracked by the CLI, so before any migration is pushed we need to know
exactly where it differs from what the repo's migrations produce.

    python supabase/tests/schema_drift.py LIVE.sql REPLAY.sql [--markdown OUT.md]

Both files must come from the same tool (`supabase db dump`), so formatting
matches and only real differences show. Statements are keyed by the object
they define (table, column, constraint, index, policy, function, trigger, grant,
RLS flag, ...) and compared after whitespace normalisation, so ordering and
layout don't matter. Ownership statements are ignored.
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


def split_statements(sql: str) -> list[str]:
    """Split SQL into statements, respecting quotes, dollar quotes and comments."""
    out, buf, i, n = [], [], 0, len(sql)
    dollar = None
    while i < n:
        ch = sql[i]
        if dollar:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(ch)
            i += 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and not sql.startswith("''", j):
                    break
                j += 2 if sql.startswith("''", j) else 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                dollar = m.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _norm(stmt: str) -> str:
    return re.sub(r"\s+", " ", stmt).strip()


# A name: quoted (policy names often contain spaces) or bare.
_Q = r'(?:"([^"]+)"|([\w$]+))'
_TBL = rf'(?:"?public"?\.)?{_Q}'

PATTERNS = [
    ("ignore", re.compile(r"^(SET |SELECT pg_catalog\.set_config|COMMENT ON SCHEMA|RESET ALL|CREATE SCHEMA|ALTER SCHEMA)", re.I)),
    ("owner", re.compile(r"^ALTER (?:TABLE|FUNCTION|TYPE|SEQUENCE|VIEW|MATERIALIZED VIEW|SCHEMA) .* OWNER TO ", re.I)),
    ("table", re.compile(rf"^CREATE (?:UNLOGGED )?TABLE (?:IF NOT EXISTS )?{_TBL}", re.I)),
    ("view", re.compile(rf"^CREATE (?:OR REPLACE )?(?:MATERIALIZED )?VIEW {_TBL}", re.I)),
    ("type", re.compile(rf"^CREATE TYPE {_TBL}", re.I)),
    ("sequence", re.compile(rf"^CREATE SEQUENCE (?:IF NOT EXISTS )?{_TBL}", re.I)),
    ("function", re.compile(rf"^CREATE (?:OR REPLACE )?FUNCTION {_TBL}\s*\(([^)]*)\)", re.I)),
    ("trigger", re.compile(rf"^CREATE (?:OR REPLACE )?(?:CONSTRAINT )?TRIGGER {_Q} .*? ON {_TBL}", re.I | re.S)),
    ("index", re.compile(rf"^CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?{_Q} ON (?:ONLY )?{_TBL}", re.I)),
    ("policy", re.compile(rf"^CREATE POLICY {_Q} ON {_TBL}", re.I)),
    ("constraint", re.compile(rf"^ALTER TABLE (?:ONLY )?{_TBL} ADD CONSTRAINT {_Q}", re.I)),
    ("rls", re.compile(rf"^ALTER TABLE (?:ONLY )?{_TBL} ENABLE ROW LEVEL SECURITY", re.I)),
    ("rls_forced", re.compile(rf"^ALTER TABLE (?:ONLY )?{_TBL} FORCE ROW LEVEL SECURITY", re.I)),
    ("default", re.compile(rf"^ALTER TABLE (?:ONLY )?{_TBL} ALTER COLUMN {_Q} SET DEFAULT", re.I)),
    ("sequence_owned", re.compile(rf"^ALTER SEQUENCE {_TBL} OWNED BY", re.I)),
    ("view_option", re.compile(rf"^ALTER VIEW {_TBL} SET", re.I)),
    ("grant", re.compile(r"^(GRANT|REVOKE) (.+?) ON (?:TABLE |FUNCTION |SEQUENCE |SCHEMA |TYPE )?(.+?) (?:TO|FROM) (.+)$", re.I | re.S)),
    ("default_priv", re.compile(r"^ALTER DEFAULT PRIVILEGES", re.I)),
    ("comment", re.compile(r"^COMMENT ON (\w+(?: \w+)?) (.+?) IS", re.I | re.S)),
    ("publication", re.compile(r"^ALTER PUBLICATION", re.I)),
]


def key_of(stmt: str):
    s = _norm(stmt)
    for kind, rx in PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        if kind in ("ignore", "owner"):
            return None
        if kind == "table":
            return ("table", m.group(1) or m.group(2))
        if kind == "function":
            args = re.sub(r"\s+", " ", m.group(3)).strip()
            return ("function", f"{m.group(1) or m.group(2)}({args})")
        if kind == "grant":
            return ("grant", f"{_norm(m.group(3))} :: {_norm(m.group(4))} :: {m.group(1).upper()} {_norm(m.group(2))}")
        if kind in ("default_priv", "publication"):
            return (kind, s)
        return (kind, " / ".join(g for g in m.groups() if g))
    return ("other", s[:120])


def table_columns(stmt: str) -> dict[str, str]:
    """Column name -> definition, from a CREATE TABLE statement."""
    body = stmt[stmt.index("(") + 1: stmt.rindex(")")]
    cols, depth, cur = {}, 0, []
    for ch in body + ",":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            part = _norm("".join(cur))
            cur = []
            m = re.match(r'"?([\w$]+)"? (.*)', part)
            if m and not re.match(r"(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN)\b", part, re.I):
                cols[m.group(1)] = m.group(2)
            continue
        cur.append(ch)
    return cols


def load(path: Path) -> dict:
    objects = defaultdict(list)
    for stmt in split_statements(path.read_text(encoding="utf-8")):
        key = key_of(stmt)
        if key is None:
            continue
        objects[key].append(_norm(stmt))
    return {k: sorted(v) for k, v in objects.items()}


def compare(live: dict, repo: dict):
    only_live = sorted(k for k in live if k not in repo)
    only_repo = sorted(k for k in repo if k not in live)
    differs = []
    for k in sorted(set(live) & set(repo)):
        if live[k] == repo[k]:
            continue
        if k[0] == "table":
            lc, rc = table_columns(live[k][0]), table_columns(repo[k][0])
            detail = []
            detail += [f"column only in live: {c} {lc[c]}" for c in lc if c not in rc]
            detail += [f"column only in repo: {c} {rc[c]}" for c in rc if c not in lc]
            detail += [f"column {c}: live `{lc[c]}` / repo `{rc[c]}`" for c in lc if c in rc and lc[c] != rc[c]]
            differs.append((k, detail or ["table options differ"]))
        else:
            differs.append((k, [f"live: {' | '.join(live[k])}", f"repo: {' | '.join(repo[k])}"]))
    return only_live, only_repo, differs


def render(only_live, only_repo, differs, live_name, repo_name) -> str:
    def group(keys):
        by_kind = defaultdict(list)
        for kind, name in keys:
            by_kind[kind].append(name)
        return by_kind

    lines = [f"# Schema drift: `{live_name}` vs `{repo_name}`\n"]
    lines.append(f"- Only in live: **{len(only_live)}**\n- Only in repo: **{len(only_repo)}**\n- Defined differently: **{len(differs)}**\n")
    for title, keys in (("Only in live", only_live), ("Only in repo", only_repo)):
        lines.append(f"## {title}\n")
        if not keys:
            lines.append("None.\n")
        for kind, names in sorted(group(keys).items()):
            lines.append(f"**{kind}** ({len(names)})\n")
            lines += [f"- `{n}`" for n in names]
            lines.append("")
    lines.append("## Defined differently\n")
    if not differs:
        lines.append("None.\n")
    for (kind, name), detail in differs:
        lines.append(f"**{kind}** `{name}`\n")
        lines += [f"- {d}" for d in detail]
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("live", type=Path)
    p.add_argument("repo", type=Path)
    p.add_argument("--markdown", type=Path)
    args = p.parse_args()
    only_live, only_repo, differs = compare(load(args.live), load(args.repo))
    text = render(only_live, only_repo, differs, args.live.name, args.repo.name)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if not (only_live or only_repo or differs) else 1


if __name__ == "__main__":
    sys.exit(main())
