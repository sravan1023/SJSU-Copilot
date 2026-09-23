"""
Static checks over the shared audience configuration.

Run from backend/ with:
    python -m tests.test_audiences

`UI/src/config/audiences.json` is read by two languages. Nothing at runtime
compares them, so every way they can disagree with each other -- or with the
database -- has to be caught here instead.

The three failures these exist to prevent, in order of how quietly they break:

1. **A fifth audience id.** The three check constraints in
   20260917000100_audience_model.sql accept exactly four values. A fifth in the
   JSON reaches the user as "save failed" in the browser with nothing in any
   log.
2. **A profileField naming an ungranted `profiles` column.** PostgREST rejects
   the *whole* UPDATE statement if any one named column is outside the
   allowlist granted by 20260917000200_profiles_privilege_guard.sql, so one bad
   entry breaks saving every other field with it.
3. **An affiliation with nowhere to go.** `affiliation_kind` has six values and
   the audience set has four; a value missing from the map would silently fall
   back to 'student'.

These parse the migrations rather than trusting a hand-copied list, so the
assertion is against what will actually be applied.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audiences  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations"
AUDIENCE_MODEL = MIGRATIONS / "20260917000100_audience_model.sql"
PRIVILEGE_GUARD = MIGRATIONS / "20260917000200_profiles_privilege_guard.sql"

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


def _sql(path):
    return path.read_text(encoding="utf-8")


def _check_constraint_values(sql, constraint):
    """The literal values a `check (col in ('a','b'))` constraint accepts.

    Handles both shapes this migration set uses: `alter table ... add
    constraint x check (...)` and a `constraint x check (...)` clause declared
    inline in `create table`. The body is read by balancing parentheses rather
    than by a lazy regex, because `check (audience in ('a','b'))` nests one.
    """
    match = re.search(
        rf"constraint\s+{constraint}\s*check\s*\(", sql, re.IGNORECASE | re.DOTALL
    )
    if not match:
        return None

    depth, start = 1, match.end()
    for index in range(start, len(sql)):
        if sql[index] == "(":
            depth += 1
        elif sql[index] == ")":
            depth -= 1
            if depth == 0:
                return set(re.findall(r"'([a-z_]+)'", sql[start:index]))
    return None


def _enum_values(sql, type_name):
    """The values of a `create type ... as enum (...)` declaration."""
    match = re.search(
        rf"create type public\.{type_name} as enum\s*\((.*?)\)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _update_allowlist(sql):
    """The columns 20260917000200 grants UPDATE on, to `authenticated`."""
    match = re.search(
        r"grant update\s*\((.*?)\)\s*on public\.profiles to authenticated",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return {c.strip() for c in match.group(1).split(",") if c.strip()}


# ── 1. The JSON agrees with the database ──────────────────────────────────────


def test_audience_ids_match_every_check_constraint():
    print("\n[1.1] the four audience ids match all three check constraints")
    sql = _sql(AUDIENCE_MODEL)
    configured = set(audiences.all_audiences())

    for constraint in (
        "profiles_active_audience_check",
        "profile_audience_details_audience_check",
        "conversations_audience_check",
    ):
        allowed = _check_constraint_values(sql, constraint)
        _check(
            f"{constraint} parsed from the migration",
            allowed is not None,
            "the constraint's shape changed; this test cannot see it",
        )
        if allowed is None:
            continue
        _check(
            f"{constraint} accepts exactly the configured audiences",
            allowed == configured,
            f"migration={sorted(allowed)} config={sorted(configured)}",
        )

    _check(
        "the default audience is one of them",
        audiences.default_audience() in configured,
        audiences.default_audience(),
    )


def test_every_affiliation_maps_to_an_audience():
    print("\n[1.2] all six affiliation_kind values map to an audience")
    sql = _sql(AUDIENCE_MODEL)
    enum_values = _enum_values(sql, "affiliation_kind")
    _check("affiliation_kind parsed from the migration", enum_values is not None)
    if enum_values is None:
        return

    mapping = audiences.affiliation_to_audience()
    _check(
        "the map covers every enum value",
        enum_values <= set(mapping),
        f"unmapped: {sorted(enum_values - set(mapping))}",
    )
    _check(
        "the map invents no affiliation the enum lacks",
        set(mapping) <= enum_values,
        f"unknown: {sorted(set(mapping) - enum_values)}",
    )

    configured = set(audiences.all_audiences())
    bad = {k: v for k, v in mapping.items() if v not in configured}
    _check("every mapped target is a real audience", not bad, str(bad))

    # affiliations[] on each audience is the same relation read the other way;
    # they must not contradict each other.
    reverse = {}
    for name, audience in audiences.all_audiences().items():
        for affiliation in audience.get("affiliations", []):
            reverse[affiliation] = name
    _check(
        "affiliations[] and affiliationToAudience agree",
        reverse == mapping,
        f"from affiliations[]={reverse} from map={mapping}",
    )


def test_declared_affiliations_are_real_enum_values():
    print("\n[1.3] declaresAffiliation is an enum value or null")
    enum_values = _enum_values(_sql(AUDIENCE_MODEL), "affiliation_kind") or set()
    for name, audience in audiences.all_audiences().items():
        declared = audience.get("declaresAffiliation")
        _check(
            f"{name}.declaresAffiliation is usable",
            declared is None or declared in enum_values,
            str(declared),
        )


# ── 2. The profile fields are writable ────────────────────────────────────────


def test_profile_columns_are_in_the_update_allowlist():
    print("\n[2.1] every profileField column is granted to authenticated")
    allowlist = _update_allowlist(_sql(PRIVILEGE_GUARD))
    _check("the UPDATE allowlist parsed from the migration", allowlist is not None)
    if allowlist is None:
        return

    for name, audience in audiences.all_audiences().items():
        for field in audience.get("profileFields", []):
            column = field.get("column")
            if column is None:
                continue  # goes to profile_audience_details.details as jsonb
            _check(
                f"{name}.{field['name']} -> profiles.{column} is granted",
                column in allowlist,
                "PostgREST rejects the whole UPDATE if any named column is "
                "ungranted, so this breaks saving every other field too",
            )


def test_profile_fields_are_well_formed():
    print("\n[2.2] profile fields carry what the renderer needs")
    known_types = {"text", "select", "month", "number", "tel", "email"}
    for name, audience in audiences.all_audiences().items():
        seen = set()
        for field in audience.get("profileFields", []):
            label = f"{name}.{field.get('name', '?')}"
            _check(f"{label} has a name and label", bool(field.get("name")) and bool(field.get("label")))
            _check(f"{label} has a known type", field.get("type") in known_types, str(field.get("type")))
            if field.get("type") == "select":
                _check(f"{label} is a select with options", bool(field.get("options")))
            _check(f"{label} is not declared twice", field.get("name") not in seen)
            seen.add(field.get("name"))


# ── 3. The prompt half is complete ────────────────────────────────────────────


def test_every_audience_has_prompt_material():
    print("\n[3.1] each audience can actually build a prompt")
    for name in audiences.all_audiences():
        _check(f"{name} has promptContext", len(audiences.prompt_context(name)) > 20)
        _check(f"{name} names at least one office", len(audiences.offices(name)) >= 1)
        _check(f"{name} has at least one source collection", len(audiences.source_collections(name)) >= 1)
        audience = audiences.get(name)
        _check(f"{name} has a welcome headline", bool(audience.get("welcomeHeadline")))
        _check(f"{name} has a label", bool(audience.get("label")))


def test_suggestions_are_usable():
    print("\n[3.2] suggestion cards carry a prompt to send")
    for name, audience in audiences.all_audiences().items():
        cards = audience.get("suggestions", [])
        _check(f"{name} has suggestions", len(cards) >= 4, str(len(cards)))
        for card in cards:
            label = f"{name}.{card.get('title', '?')}"
            _check(
                f"{label} has title, subtitle and prompt",
                all(card.get(k) for k in ("title", "subtitle", "prompt")),
            )


def test_site_filter_shape():
    print("\n[3.3] site: filters are well formed")
    single = audiences.site_filter("student")
    _check("a single domain yields a bare site:", single == "site:sjsu.edu", single)

    multi = audiences.site_filter("alumni")
    _check(
        "several domains are OR-ed inside parentheses",
        multi.startswith("(") and " OR " in multi and multi.endswith(")"),
        multi,
    )
    _check(
        "every collection appears in the filter",
        all(f"site:{d}" in multi for d in audiences.source_collections("alumni")),
        multi,
    )


def test_unknown_audience_falls_back_rather_than_raising():
    print("\n[3.4] an unknown audience degrades to the default")
    # ChatRequest.audience is client-supplied. The server must not trust it for
    # anything authorization-shaped, and it must not 500 on a bad value either.
    for bogus in (None, "", "admin", "Student", "'; drop table profiles;--"):
        _check(f"get({bogus!r}) returns the default", audiences.get(bogus)["id"] == audiences.default_audience())
        _check(f"is_audience({bogus!r}) is False", not audiences.is_audience(bogus))


def test_the_ui_and_backend_read_the_same_file():
    print("\n[3.5] both halves resolve to one file")
    path = audiences.config_path()
    _check("the configured path exists", path.exists(), str(path))
    _check(
        "it is the file UI/src/config/audiences.ts imports",
        path.parts[-4:] == ("UI", "src", "config", "audiences.json"),
        str(path),
    )


def main():
    for fn in (
        test_audience_ids_match_every_check_constraint,
        test_every_affiliation_maps_to_an_audience,
        test_declared_affiliations_are_real_enum_values,
        test_profile_columns_are_in_the_update_allowlist,
        test_profile_fields_are_well_formed,
        test_every_audience_has_prompt_material,
        test_suggestions_are_usable,
        test_site_filter_shape,
        test_unknown_audience_falls_back_rather_than_raising,
        test_the_ui_and_backend_read_the_same_file,
    ):
        fn()

    print("\n" + "=" * 60)
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
