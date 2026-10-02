"""
Static checks over supabase/migrations.

Run from backend/ with:
    python -m tests.test_migrations

These do not execute SQL -- there is no database available in this environment.
They catch the structural mistakes that broke this migration set before: version
collisions (schema_migrations.version is a primary key) and objects being
altered before they are created.

A green run here means the directory is *structurally* replayable. It is not a
substitute for `supabase db reset`.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIGRATIONS = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations"

PASS = 0
FAIL = 0
FAILURES = []


def _check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append((label, detail))
        print(f"  FAIL {label}  {detail}")


def _files():
    return sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name)


def _version(path):
    return path.name.split("_", 1)[0]


# ── 1. Versions ───────────────────────────────────────────────────────────────


def test_versions_are_unique():
    print("\n[1.1] migration versions are unique")
    seen = {}
    for path in _files():
        seen.setdefault(_version(path), []).append(path.name)

    dupes = {v: names for v, names in seen.items() if len(names) > 1}
    _check(
        "no duplicate versions (schema_migrations.version is a primary key)",
        not dupes,
        str(dupes),
    )


def test_versions_are_numeric():
    print("\n[1.2] versions parse as numbers")
    for path in _files():
        version = _version(path)
        _check(f"{path.name} has a numeric version", version.isdigit(), version)


# ── 2. Dependency ordering ────────────────────────────────────────────────────

# (object, regex that creates it, regex that requires it to already exist)
CREATE_TABLE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)", re.IGNORECASE
)
ALTER_TABLE_RE = re.compile(r"alter\s+table\s+(?:public\.)?(\w+)", re.IGNORECASE)
CREATE_POLICY_ON_RE = re.compile(
    r"create\s+policy\s+\"[^\"]+\"\s+on\s+(?:public\.)?(\w+)", re.IGNORECASE
)


def _strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def test_tables_exist_before_they_are_altered():
    print("\n[2.1] no table is altered or policied before it is created")
    created = set()
    # auth.* and storage.* are provided by the platform.
    external = {"users", "objects"}

    problems = []
    for path in _files():
        sql = _strip_comments(path.read_text(encoding="utf-8"))

        for table in CREATE_TABLE_RE.findall(sql):
            created.add(table.lower())

        for pattern, kind in ((ALTER_TABLE_RE, "alter"), (CREATE_POLICY_ON_RE, "policy")):
            for table in pattern.findall(sql):
                name = table.lower()
                if name in external or name in created:
                    continue
                problems.append(f"{path.name}: {kind} on '{table}' before it is created")

    _check("every altered/policied table is created first", not problems, "; ".join(problems))


def test_behavior_settings_chain_is_ordered():
    print("\n[2.2] the behavior_settings chain is in dependency order")
    names = [p.name for p in _files()]

    def index_of(fragment):
        for i, name in enumerate(names):
            if fragment in name:
                return i
        return -1

    settings = index_of("behavior_settings")
    priorities = index_of("priorities")
    scoping = index_of("behavior_scoping")

    _check("all three present", min(settings, priorities, scoping) >= 0, str(names))
    _check(
        "behavior_settings creates the table first",
        settings < priorities and settings < scoping,
        f"settings={settings} priorities={priorities} scoping={scoping}",
    )
    _check(
        "priorities adds priority_stack before scoping alters it",
        priorities < scoping,
        f"priorities={priorities} scoping={scoping}",
    )


def test_priority_stack_added_before_it_is_altered():
    print("\n[2.3] priority_stack exists before its NOT NULL is dropped")
    added_at = None
    altered_at = None
    for i, path in enumerate(_files()):
        sql = _strip_comments(path.read_text(encoding="utf-8")).lower()
        if "add column if not exists priority_stack" in sql and added_at is None:
            added_at = i
        if "alter column priority_stack" in sql and altered_at is None:
            altered_at = i

    _check("priority_stack is added somewhere", added_at is not None)
    _check("priority_stack is altered somewhere", altered_at is not None)
    _check(
        "added before altered",
        added_at is not None and altered_at is not None and added_at < altered_at,
        f"added at {added_at}, altered at {altered_at}",
    )


# ── 3. Known regressions stay fixed ───────────────────────────────────────────


def test_on_conflict_user_id_is_gone():
    print("\n[3.1] the default-settings trigger no longer needs a (user_id) unique index")
    # 20260407030000 drops uq_behavior_settings_user, which is the index that
    # `on conflict (user_id)` relied on. Creating a profile would then fail.
    offenders = []
    for path in _files():
        sql = _strip_comments(path.read_text(encoding="utf-8")).lower()
        if "on conflict (user_id)" in sql:
            offenders.append(path.name)

    # It may still appear in the original migration, as long as a later one
    # redefines the function without it.
    last_definition = None
    for path in _files():
        sql = _strip_comments(path.read_text(encoding="utf-8")).lower()
        if "function public.create_default_behavior_settings" in sql or \
           "function create_default_behavior_settings" in sql:
            last_definition = (path.name, sql)

    _check("the function is defined somewhere", last_definition is not None)
    _check(
        "the final definition does not use on conflict (user_id)",
        last_definition is not None and "on conflict (user_id)" not in last_definition[1],
        f"last defined in {last_definition[0] if last_definition else '?'}; "
        f"on-conflict still present in {offenders}",
    )


def test_ats_registry_has_rls():
    print("\n[3.2] ats_registry RLS stays enabled")
    enabled = any(
        "alter table public.ats_registry enable row level security"
        in _strip_comments(p.read_text(encoding="utf-8")).lower()
        for p in _files()
    )
    _check("RLS is enabled on ats_registry", enabled)


# `revoke update (role) on ...`, `revoke select (email) on ...` -- a privilege
# name followed by a column list.
COLUMN_REVOKE_RE = re.compile(
    r"revoke\s+(select|insert|update|references)\s*\(", re.IGNORECASE
)


def test_no_column_level_revoke():
    """PostgreSQL cannot revoke a column privilege against a table-level grant.

    Live holds `GRANT ALL ON TABLE public.profiles TO authenticated`, so
    `revoke update (role) on public.profiles from authenticated` emits
    `WARNING: no privileges could be revoked for column "role"` and changes
    nothing -- silently, while looking exactly like a lockdown.

    The working form is to revoke the whole privilege and grant an explicit
    column allowlist back, as 20260917000200 does.

    This is a static check because the failure mode is silence: nothing raises,
    and the migration reports success.
    """
    print("\n[3.3] no migration tries to revoke a single column's privilege")
    offenders = []
    for path in _files():
        sql = _strip_comments(path.read_text(encoding="utf-8"))
        if COLUMN_REVOKE_RE.search(sql):
            offenders.append(path.name)

    _check(
        "no column-level revoke (it is a silent no-op against a table grant)",
        not offenders,
        "; ".join(offenders),
    )


# ── 4. New tables start closed ────────────────────────────────────────────────

# 20260930000100 is the first migration written to the rule below. Everything
# before it predates the rule and is covered by verify_policies.sql instead.
# Versions compare as strings, which is the order the CLI applies them in
# (filename order, the same order _files() uses).
CLOSED_TABLES_SINCE = "20260930000100"

ENABLE_RLS_RE = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?(?:public\.)?(\w+)"
    r"\s+enable\s+row\s+level\s+security",
    re.IGNORECASE,
)
# `revoke all [privileges] on [table] public.a[, public.b] from anon, authenticated;`
# The table list cannot contain `(`, so `revoke all on function f(...)` never
# matches.
REVOKE_ALL_RE = re.compile(
    r"revoke\s+all(?:\s+privileges)?\s+on\s+(?:table\s+)?([\w.,\s]+?)\s+from\s+([\w,\s]+?)\s*;",
    re.IGNORECASE,
)


def _unclosed_tables(sql):
    """[(table, [what is missing])] for every table `sql` creates.

    Each `create table` must be followed, later in the same file, by
    `enable row level security` on it and by `revoke all` from both anon and
    authenticated (in one statement or several).
    """
    results = []
    for match in CREATE_TABLE_RE.finditer(sql):
        table = match.group(1).lower()
        rest = sql[match.end():]

        has_rls = any(name.lower() == table for name in ENABLE_RLS_RE.findall(rest))

        revoked_from = set()
        for tables_part, roles_part in REVOKE_ALL_RE.findall(rest):
            names = {
                name.strip().lower().removeprefix("public.")
                for name in tables_part.split(",")
            }
            if table in names:
                revoked_from |= {role.strip().lower() for role in roles_part.split(",")}

        missing = []
        if not has_rls:
            missing.append("enable row level security")
        for role in ("anon", "authenticated"):
            if role not in revoked_from:
                missing.append(f"revoke all from {role}")
        results.append((table, missing))
    return results


def test_new_tables_enable_rls_and_revoke():
    """A new table is open to the anon key until its migration closes it.

    20260918000100 left default privileges that grant SELECT, INSERT, UPDATE
    and DELETE on every new public table to anon and authenticated. So a table
    created without `revoke all ... from anon, authenticated` is reachable over
    PostgREST with the browser's key, and one without RLS is world-writable.

    Static because the failure is silent: the table works perfectly for the
    backend, nothing raises, and the migration reports success.
    """
    print(f"\n[4.1] every table created after {CLOSED_TABLES_SINCE} enables RLS "
          "and revokes anon + authenticated")
    checked = []
    problems = []
    for path in _files():
        if _version(path) <= CLOSED_TABLES_SINCE:
            continue
        sql = _strip_comments(path.read_text(encoding="utf-8"))
        for table, missing in _unclosed_tables(sql):
            checked.append(table)
            if missing:
                problems.append(f"{path.name}: {table} lacks {', '.join(missing)}")

    # An empty `checked` means the create-table regex or the cutoff is wrong,
    # not that every table passed, so it fails rather than passing vacuously.
    _check(
        f"every new table is closed to anon and authenticated ({len(checked)} checked)",
        bool(checked) and not problems,
        "; ".join(problems) or f"no create table found after {CLOSED_TABLES_SINCE}",
    )


def run():
    test_versions_are_unique()
    test_versions_are_numeric()
    test_tables_exist_before_they_are_altered()
    test_behavior_settings_chain_is_ordered()
    test_priority_stack_added_before_it_is_altered()
    test_on_conflict_user_id_is_gone()
    test_ats_registry_has_rls()
    test_no_column_level_revoke()
    test_new_tables_enable_rls_and_revoke()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    for label, detail in FAILURES:
        print(f"  - {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
