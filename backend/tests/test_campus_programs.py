"""
Degree program course lists (campus/programs): the extractor against the five
pilot department pages and hand-written samples, and the build CLI's
validation, failure handling and output. Offline: pages come from
tests/fixtures/programs.

Run from backend/ with:
    python -m pytest tests/test_campus_programs.py
"""
import json
from datetime import date
from pathlib import Path

import pytest

from campus.programs import build, graduate
from campus.programs.extract import Course, extract, extract_codes, merge, page_lines

FIXTURES = Path(__file__).parent / "fixtures" / "programs"
TODAY = date(2026, 10, 1)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _codes(courses):
    return [c.code for c in courses]


def _by_code(courses):
    return {c.code: c for c in courses}


def _html(body: str) -> str:
    return f"<html><body><main>{body}</main></body></html>"


# -- the pilot pages ------------------------------------------------------------

def test_aerospace_reads_the_whole_degree_and_not_the_prerequisite_cells():
    courses = extract(_page("ae_programs_bsae_prerequisite.html"))
    got = _by_code(courses)
    assert len(courses) == 42
    assert got["MATH 30"].title == "Calculus I"  # "Math" upper-cased
    assert got["ENGR 100W"].title == "Engineering Reports"
    assert got["AE 171A"].title == "Aircraft Design I"  # same row as its prerequisites
    assert got["AE 15"].title.startswith("AE Past, Present")
    # Codes that appear only in prerequisite cells are not courses of the program.
    for prereq_only in ("MATH 19", "MATH 30X", "CHEM 10", "ENGL 1A"):
        assert prereq_only not in got
    assert {c.code.split()[0] for c in courses} == {"MATH", "CHEM", "PHYS", "ENGR", "AE", "EE"}


def test_biomedical_keeps_its_section_groups_and_drops_the_units_suffix():
    courses = extract(_page("bme_programs_bs-bme_curriculum.html"), groups=True)
    got = _by_code(courses)
    assert len(courses) == 41
    assert got["MATH 30"].title == "Calculus I"  # from "Calculus I 3 unit(s) (B4) (or MATH 30X)"
    assert got["BME 198A"].title == "Senior Design Project I"
    assert got["ENGL 1B"].title == "Argument and Analysis"
    assert got["BIOL 30"].group == "Major Preparation (45 units)"
    assert got["BME 25"].group == "Core Courses (11 units)"
    assert got["BME 115"].group == "Major Courses (43 units)"
    assert got["HIST 15"].group == "U.S. History and Government (6 units)"


def test_computer_engineering_reads_br_separated_lines_and_ignores_the_nav():
    courses = extract(_page("cmpe_undergraduate_programs_undergraduate_courses.html"))
    got = _by_code(courses)
    assert len(courses) == 47
    assert got["CMPE 30"].title == "Programming Concepts and Methodology"
    assert got["CMPE 195F"].title == "Senior Design Project II"
    assert "CMPE 999" not in got  # only in the fixture's <nav>, outside <main>
    assert all(c.code.startswith("CMPE ") for c in courses)


def test_electrical_splits_required_courses_from_electives():
    courses = extract(_page("ee_undergraduate-program_syllabi.html"), groups=True)
    groups = [c.group for c in courses]
    assert len(courses) == 37
    assert groups.count("Required EE Courses") == 17
    assert groups.count("Technical Electives") == 20
    assert _by_code(courses)["EE 110L"].title == "Continuous and Discrete Time Systems Lab"


def test_industrial_shorthand_expands_and_stops_before_the_policy_prose():
    courses = extract(
        _page("ise_programs_bs-ise_program-requirements.html"),
        mode="codes", stop_at="Additional Requirements",
    )
    got = _codes(courses)
    assert len(courses) == 29
    assert got[:4] == ["MATH 30", "MATH 31", "MATH 32", "MATH 123"]
    for code in ("PHYS 50", "PHYS 51", "CMPE 30", "ENGR 10", "ME 20", "MATE 25", "ISE 195A", "ISE 195B", "CMPE 131"):
        assert code in got
    # "Engl 1A requires the English Placement Test", "Math 30 or 20 requires ..."
    assert "ENGL 1A" not in got and "MATH 20" not in got


# -- samples --------------------------------------------------------------------

def test_titled_line_shapes():
    html = _html(
        "<ul>"
        "<li>AE 015 [pdf] - Air &amp; Space Flight: Past, Present, and Future</li>"
        "<li>Engr. 195A - Global &amp; Societal Issues in Engineering I</li>"
        "<li>CmpE 131 – Software Engineering</li>"
        "<li>BUS1 170 - Business Law</li>"
        "</ul>"
    )
    got = _by_code(extract(html))
    assert got["AE 15"].title == "Air & Space Flight: Past, Present, and Future"
    assert got["ENGR 195A"].title == "Global & Societal Issues in Engineering I"
    assert got["CMPE 131"].title == "Software Engineering"
    assert got["BUS1 170"].title == "Business Law"


def test_prose_and_contact_lines_are_not_courses():
    html = _html(
        "<p>Phone: 408-924-4000</p>"
        "<p>Fall 2021 - the new catalog</p>"
        "<p>Total 120 - units</p>"
        "<p>CHEM 1A - 5 units</p>"
        "<p>Students take Math 30 or 30X first.</p>"
    )
    assert extract(html) == []


def test_shorthand_shapes():
    lines = page_lines(_html(
        "<p>PHYS 50/51 - 8 units</p>"
        "<p>CMPE 30, ENGR 10, ME 20, MATE 25</p>"
        "<p>ISE 102 and 105</p>"
        "<p>Of the 39 units, 30 may be met in GE.</p>"
        "<p>One Washington Square, San Jose, CA 95192</p>"
    ))
    assert _codes(extract_codes(lines)) == [
        "PHYS 50", "PHYS 51", "CMPE 30", "ENGR 10", "ME 20", "MATE 25", "ISE 102", "ISE 105",
    ]


def test_headings_and_bold_only_lines_become_groups_only_when_asked():
    html = _html(
        "<h2>Required</h2><ul><li>EE 98 - Circuits</li></ul>"
        "<p><strong>Electives</strong><br>EE 104 - Applied Programming</p>"
    )
    grouped = _by_code(extract(html, groups=True))
    assert grouped["EE 98"].group == "Required" and grouped["EE 104"].group == "Electives"
    assert all(c.group is None for c in extract(html))


def test_merge_keeps_first_order_and_prefers_a_title():
    merged = merge([
        Course("MATH 30"), Course("PHYS 50", "Mechanics", "Prep"), Course("MATH 30", "Calculus I", "Core"),
    ])
    assert merged == [Course("MATH 30", "Calculus I", "Core"), Course("PHYS 50", "Mechanics", "Prep")]


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        extract(_html(""), mode="guess")


# -- the build ------------------------------------------------------------------

def _sources(tmp_path, programs):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"programs": programs}), encoding="utf-8")
    return path


def test_the_checked_in_sources_load_and_name_saved_pages():
    programs = build.load_sources()
    assert [p.id for p in programs] == ["bsae", "bsbme", "bscmpe", "bsse", "bsee", "bsise"]
    for p in programs:
        for s in p.sources:
            assert (FIXTURES / build.page_name(s.url)).is_file(), s.url


@pytest.mark.parametrize("url", [
    "https://catalog.sjsu.edu/preview_program.php?catoid=17",
    "http://www.sjsu.edu/ae/",
    "https://docs.google.com/spreadsheets/d/x",
])
def test_sources_off_the_allowlist_are_refused(tmp_path, url):
    path = _sources(tmp_path, [{"id": "x", "name": "X", "department": "X", "sources": [{"url": url}]}])
    with pytest.raises(build.SourceError):
        build.load_sources(path)


def test_duplicate_or_malformed_ids_are_refused(tmp_path):
    src = [{"url": "https://www.sjsu.edu/a.php"}]
    for programs in (
        [{"id": "a", "name": "A", "department": "A", "sources": src}] * 2,
        [{"id": "../a", "name": "A", "department": "A", "sources": src}],
    ):
        with pytest.raises(build.SourceError):
            build.load_sources(_sources(tmp_path, programs))


def test_aliases_must_be_a_list_of_names(tmp_path):
    src = [{"url": "https://www.sjsu.edu/a.php"}]
    for aliases in ("Aerospace", [""], [3]):
        programs = [{"id": "a", "name": "A", "department": "A", "aliases": aliases, "sources": src}]
        with pytest.raises(build.SourceError):
            build.load_sources(_sources(tmp_path, programs))


def test_no_alias_names_two_programs_of_the_same_level():
    named = [("undergraduate", p.id, (p.name, *p.aliases)) for p in build.load_sources()]
    named += [("graduate", d["id"], (d["name"], *d["aliases"])) for d in graduate.load_definitions()]
    seen = {}
    for level, pid, names in named:
        for name in names:
            key = (level, " ".join(name.casefold().replace("&", "and").split()))
            assert seen.setdefault(key, pid) == pid, f"{name!r} names {seen[key]} and {pid}"


def _all_pages():
    urls = [s.url for p in build.load_sources() for s in p.sources]
    urls += [u for d in graduate.load_definitions() for u in graduate.page_urls(d)]
    return build.read_pages(list(dict.fromkeys(urls)), FIXTURES)


def test_a_full_offline_build_writes_every_program_and_the_index(tmp_path):
    code, text = build.run(
        build.load_sources(), pages=_all_pages(), out_dir=tmp_path, today=TODAY,
        dry_run=False, merge_index=False,
    )
    assert code == 0, text
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert {p["id"] for p in index["programs"]} == {"bsae", "bsbme", "bscmpe", "bsse", "bsee", "bsise"}
    bsae = next(p for p in index["programs"] if p["id"] == "bsae")
    assert bsae["course_count"] == 42
    assert "Aerospace Engineering" in bsae["aliases"]  # the UI matches the profile's major on these

    ise = json.loads((tmp_path / "bsise.json").read_text(encoding="utf-8"))
    titles = {c["code"]: c["title"] for c in ise["courses"]}
    assert titles["MATH 30"] == "Calculus I"  # filled from the Aerospace page
    assert titles["CMPE 131"] == "Software Engineering"  # filled from the CMPE page
    assert titles["ISE 102"] is None  # no page titles it; never invented
    assert ise["sources"][0]["last_updated"] == "2022-01-19"
    assert "over two years ago" in text


def test_a_dry_run_writes_nothing(tmp_path):
    code, text = build.run(
        build.load_sources(), pages=_all_pages(), out_dir=tmp_path, today=TODAY,
        dry_run=True, merge_index=False,
    )
    assert code == 0 and "Dry run" in text
    assert list(tmp_path.iterdir()) == []


def test_a_missing_page_or_a_thin_list_fails_the_whole_build(tmp_path):
    programs = build.load_sources()
    pages = _all_pages()
    pages[programs[0].sources[0].url] = build.Page(programs[0].sources[0].url, error="http_4xx HTTP 404")
    code, text = build.run(programs, pages=pages, out_dir=tmp_path, today=TODAY, dry_run=False, merge_index=False)
    assert code == 1 and "HTTP 404" in text
    assert list(tmp_path.iterdir()) == []

    thin = dict(_all_pages())
    url = programs[0].sources[0].url
    thin[url] = build.Page(url, html=_html("<ul><li>AE 15 - Only one course</li></ul>"))
    code, text = build.run(programs, pages=thin, out_dir=tmp_path, today=TODAY, dry_run=False, merge_index=False)
    assert code == 1 and "has the page layout changed" in text
    assert list(tmp_path.iterdir()) == []


def test_building_one_program_merges_into_the_existing_index(tmp_path):
    programs = build.load_sources()
    build.run(programs, pages=_all_pages(), out_dir=tmp_path, today=TODAY, dry_run=False, merge_index=False)
    only = [p for p in programs if p.id == "bsee"]
    code, _ = build.run(only, pages=_all_pages(), out_dir=tmp_path, today=date(2026, 11, 1), dry_run=False, merge_index=True)
    assert code == 0
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(index["programs"]) == 6 and index["built_at"] == "2026-11-01"


# -- graduate definitions -------------------------------------------------------

def _defn(pid):
    return next(d for d in graduate.load_definitions() if d["id"] == pid)


def _html_pages(urls):
    return {u: build.read_pages([u], FIXTURES)[u].html for u in urls}


@pytest.mark.parametrize("pid", ["msai", "msse", "mscs"])
def test_the_checked_in_definitions_are_consistent(pid):
    assert graduate.validate(_defn(pid)) == []


@pytest.mark.parametrize("pid", ["msai", "msse"])
def test_every_defined_course_is_on_a_source_page(pid):
    defn = _defn(pid)
    missing, _ = graduate.drift(defn, _html_pages(graduate.page_urls(defn)))
    assert missing == []


def test_drift_flags_a_dropped_course_and_lists_new_ones_but_not_exclusions():
    defn = json.loads(json.dumps(_defn("msse")))
    defn["courses"]["CMPE 999X"] = {"title": "Gone", "units": 3}
    missing, unlisted = graduate.drift(defn, _html_pages(graduate.page_urls(defn)))
    assert missing == ["CMPE 999X"]
    assert "CMPE 255" in unlisted  # the page's "no longer a required core class" note
    assert "CMPE 270" not in unlisted  # named only to exclude it from electives


def test_a_course_added_on_evidence_is_exempt_from_the_page_check_and_must_say_why():
    defn = _defn("msai")
    assert "CMPE 255" in defn["courses"] and defn["courses"]["CMPE 255"]["evidence"]
    missing, _ = graduate.drift(defn, _html_pages(graduate.page_urls(defn)))
    assert "CMPE 255" not in missing  # not on the department page, vouched for by MyProgress

    bare = json.loads(json.dumps(defn))
    del bare["courses"]["CMPE 255"]["evidence"]
    missing, _ = graduate.drift(bare, _html_pages(graduate.page_urls(bare)))
    assert missing == ["CMPE 255"]

    bare["courses"]["CMPE 255"]["evidence"] = "  "
    assert any("evidence must say" in e for e in graduate.validate(bare))


def _broken(mutate):
    defn = json.loads(json.dumps(_defn("msai")))
    mutate(defn)
    return " | ".join(graduate.validate(defn))


def test_validation_catches_each_kind_of_mistake():
    def units(d): d["requirements"][0]["units"] = 12
    assert "add up to 36 units, not 33" in _broken(units)

    def unknown(d): d["requirements"][0]["all"].append("CMPE 999")
    assert "not in courses" in _broken(unknown)

    def option(d): d["requirements"][2]["by_option"].pop("autonomous-systems")
    assert "every option" in _broken(option)

    def prereq(d): d["prerequisites"]["CMPE 295A"]["requirements"] = ["nope"]
    assert "unknown requirement 'nope'" in _broken(prereq)

    def host(d): d["sources"][0]["url"] = "https://catalog.sjsu.edu/x"
    assert "is not on" in _broken(host)

    def copied(d): d["sources"][0] = {"url": "https://catalog.sjsu.edu/x", "kind": "catalog-copy"}
    assert "needs a 'copied' date" in _broken(copied)

    def code(d): d["courses"]["cmpe252"] = {"title": "x", "units": 3}
    assert "not in 'SUBJ NUM' form" in _broken(code)

    def level(d): d["level"] = "undergraduate"
    assert "level must be 'graduate'" in _broken(level)

    def merge(d): d["merge_groups"] = ["No such group"]
    assert "merge_groups names 'No such group'" in _broken(merge)

    def infer(d): d["choices"][0]["infer"] = "yes"
    assert "infer must be true or false" in _broken(infer)


def test_a_full_build_publishes_graduate_programs_with_levels(tmp_path):
    code, text = build.run(
        build.load_sources(), pages=_all_pages(), out_dir=tmp_path, today=TODAY,
        dry_run=False, merge_index=False, graduates=graduate.load_definitions(),
    )
    assert code == 0, text
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    levels = {p["id"]: p["level"] for p in index["programs"]}
    assert levels["msai"] == levels["msse"] == levels["mscs"] == "graduate"
    assert levels["bsae"] == "undergraduate"
    msai = json.loads((tmp_path / "msai.json").read_text(encoding="utf-8"))
    assert msai["total_units"] == 33 and msai["sources"][0]["last_updated"] == "2026-05-06"
    mscs = next(p for p in index["programs"] if p["id"] == "mscs")
    assert mscs["last_updated"] == "2026-10-02"  # the copy date stands in for a page date
    assert "copied by hand" in text


def test_a_missing_graduate_page_fails_the_build(tmp_path):
    pages = _all_pages()
    url = graduate.page_urls(_defn("msai"))[0]
    pages[url] = build.Page(url, error="http_4xx HTTP 404")
    code, text = build.run(
        [], pages=pages, out_dir=tmp_path, today=TODAY, dry_run=False, merge_index=False,
        graduates=graduate.load_definitions(),
    )
    assert code == 1 and "HTTP 404" in text
    assert list(tmp_path.iterdir()) == []


# -- published files, pool limits, conditions -----------------------------------

@pytest.mark.parametrize("pid", ["msai", "msse", "mscs"])
def test_the_published_file_matches_its_rule_file(pid):
    published = json.loads((build.DEFAULT_OUT / f"{pid}.json").read_text(encoding="utf-8"))
    assert graduate.unpublished_changes(_defn(pid), published) == [], (
        f"UI/public/data/programs/{pid}.json is stale: run campus.programs.build"
    )


def test_a_stale_published_file_names_the_key_that_drifted():
    defn = _defn("msai")
    published = {**json.loads(json.dumps(defn)), "built_at": "2026-01-01", "sources": []}
    assert graduate.unpublished_changes(defn, published) == []  # built_at/sources may differ
    published["total_units"] = 30
    assert graduate.unpublished_changes(defn, published) == ["total_units"]


def test_pool_limits_must_make_sense():
    def pools(d):
        return d["requirements"][3]["pools"]  # MS AI electives: Area A, Area B

    def negative(d): pools(d)[0]["min_units"] = 0
    assert "min_units must be a positive integer" in _broken(negative)

    def inverted(d): pools(d)[1]["min_units"] = 9; pools(d)[1]["max_units"] = 6
    assert "min_units is above its max_units" in _broken(inverted)

    def over(d): pools(d)[1]["max_units"] = 12
    assert "max_units is above the requirement's 9 units" in _broken(over)

    def short(d): pools(d)[0]["from"] = ["CMPE 249"]; pools(d)[0]["min_units"] = 6
    assert "lists 3 units but needs at least 6" in _broken(short)


def test_conditions_are_short_sourced_sentences():
    assert all(1 <= len(_defn(p).get("conditions", ["x"])) <= 8 for p in ("msai", "msse", "mscs"))

    def empty(d): d["conditions"] = []
    assert "conditions must be 1-8" in _broken(empty)

    def long(d): d["conditions"] = ["x" * 241]
    assert "conditions must be 1-8" in _broken(long)
