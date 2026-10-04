"""Build the Degree Progress course lists from department pages.

Undergraduate lists are read from the pages named in sources.json; graduate
programs are hand-written rule files in graduate/ (see graduate.py), validated
and checked against their pages here.

    python -m campus.programs.build                    # fetch live, write UI/public/data/programs
    python -m campus.programs.build --dry-run          # fetch and report, write nothing
    python -m campus.programs.build --from-dir DIR     # read saved pages instead of fetching
    python -m campus.programs.build --program bsae     # one program; index.json is merged
    python -m campus.programs.build --save-pages DIR   # also keep the fetched pages

An offline CLI, never run inside a request. Fetching goes through the same path
as the campus refresh: robots.txt is checked, one request at a time per host
with the crawl delay, redirects walked by hand with every hop re-checked against
www.sjsu.edu (kb.fetcher.conditional_get).

The output is static JSON the UI loads when a student picks a program:
`<id>.json` with the courses, and `index.json` listing the programs. A program
whose page fails, comes back truncated, or yields fewer than MIN_COURSES codes
fails the build, and nothing is written, so a layout change can't quietly
replace a good list with an empty one. Review the printed report before
committing the files.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

from campus import terms
from campus.programs import graduate
from campus.programs.extract import Course, extract, merge
from campus.registration.parse_calendar import parse_last_updated
from campus.registration.refresh import Pacer, crawl_delay
from kb.fetcher import conditional_get
from kb.robots import USER_AGENT, RobotsCache

SOURCES_PATH = Path(__file__).with_name("sources.json")
DEFAULT_OUT = Path(__file__).resolve().parents[3] / "UI" / "public" / "data" / "programs"
MAX_BYTES = 2_000_000  # department pages are ~80-120 KB
MIN_COURSES = 10
STALE_AFTER = timedelta(days=2 * 365)
MODES = ("titled", "codes")
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    url: str
    mode: str = "titled"
    groups: bool = False
    stop_at: str | None = None


@dataclass(frozen=True)
class Program:
    id: str
    name: str
    department: str
    college: str
    own_subjects: tuple[str, ...]
    sources: tuple[Source, ...]
    # What a student might type as their major in Settings, matched by the UI
    # (degreeProgress/programs.ts matchProgram) alongside `name`.
    aliases: tuple[str, ...] = ()


def load_sources(path: Path = SOURCES_PATH) -> list[Program]:
    data = json.loads(path.read_text(encoding="utf-8"))
    programs: list[Program] = []
    seen: set[str] = set()
    for raw in data["programs"]:
        pid = raw["id"]
        if not pid or set(pid) - ID_CHARS or pid in seen:
            raise SourceError(f"bad or duplicate program id: {pid!r}")
        seen.add(pid)
        sources = []
        for s in raw["sources"]:
            if not terms.host_allowed(s["url"]):
                raise SourceError(f"{pid}: {s['url']} is not on {terms.ALLOWED_HOSTS}")
            if s.get("mode", "titled") not in MODES:
                raise SourceError(f"{pid}: unknown mode {s.get('mode')!r}")
            sources.append(Source(s["url"], s.get("mode", "titled"), bool(s.get("groups")), s.get("stop_at")))
        if not sources:
            raise SourceError(f"{pid}: no sources")
        aliases = raw.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise SourceError(f"{pid}: aliases must be a list of non-empty strings")
        programs.append(Program(
            pid, raw["name"], raw["department"], raw.get("college", ""),
            tuple(raw.get("own_subjects", [])), tuple(sources), tuple(aliases),
        ))
    return programs


def page_name(url: str) -> str:
    """The file a page is saved as or read from: '/ae/programs/bsae/x.php' -> 'ae_programs_bsae_x.html'."""
    path = urlsplit(url).path.strip("/")
    if path.endswith(".php"):
        path = path[: -len(".php")]
    return (path.replace("/", "_") or "index") + ".html"


@dataclass
class Page:
    url: str
    html: str | None = None
    error: str | None = None
    final_url: str | None = None


async def fetch_pages(urls: list[str], *, save_dir: Path | None = None) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    pacer = Pacer()
    async with httpx.AsyncClient(follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        robots = RobotsCache(client)
        for url in urls:
            rules = await robots.for_url(url)
            await pacer.wait(rules.host, max(rules.crawl_delay or 0.0, crawl_delay()))
            res = await conditional_get(
                client, url, rules=rules, allowed_hosts=list(terms.ALLOWED_HOSTS),
                max_bytes=MAX_BYTES, extract=False,
            )
            if not res.ok:
                detail = res.detail or (f"HTTP {res.status}" if res.status else "")
                pages[url] = Page(url, error=f"{res.outcome.value} {detail}".strip())
            elif res.truncated:
                pages[url] = Page(url, error="truncated at the byte cap")
            else:
                pages[url] = Page(url, html=res.html, final_url=res.final_url)
                if save_dir is not None:
                    save_dir.mkdir(parents=True, exist_ok=True)
                    (save_dir / page_name(url)).write_text(res.html or "", encoding="utf-8")
    return pages


def read_pages(urls: list[str], from_dir: Path) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for url in urls:
        path = from_dir / page_name(url)
        if path.is_file():
            pages[url] = Page(url, html=path.read_text(encoding="utf-8"), final_url=url)
        else:
            pages[url] = Page(url, error=f"no saved page {path.name}")
    return pages


@dataclass
class Built:
    program: Program
    courses: list[Course] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def payload(self, built_at: date) -> dict:
        return {
            "id": self.program.id,
            "name": self.program.name,
            "department": self.program.department,
            "college": self.program.college,
            "own_subjects": list(self.program.own_subjects),
            "built_at": built_at.isoformat(),
            "sources": self.sources,
            "courses": [
                {"code": c.code, "title": c.title, "group": c.group} for c in self.courses
            ],
        }


def build_program(program: Program, pages: dict[str, Page], today: date) -> Built:
    out = Built(program)
    found: list[Course] = []
    for src in program.sources:
        page = pages.get(src.url)
        if page is None or page.html is None:
            out.errors.append(f"{src.url}: {page.error if page else 'not fetched'}")
            continue
        if page.final_url and page.final_url.rstrip("/") != src.url.rstrip("/"):
            out.warnings.append(f"{src.url} redirected to {page.final_url}")
        updated = parse_last_updated(page.html)
        if updated is None:
            out.warnings.append(f"{src.url}: no 'Last Updated' line")
        elif today - updated > STALE_AFTER:
            out.warnings.append(f"{src.url}: last updated {updated.isoformat()}, over two years ago")
        out.sources.append({"url": src.url, "last_updated": updated.isoformat() if updated else None})
        found.extend(extract(page.html, mode=src.mode, groups=src.groups, stop_at=src.stop_at))
    out.courses = merge(found)
    if out.ok and len(out.courses) < MIN_COURSES:
        out.errors.append(f"only {len(out.courses)} courses (minimum {MIN_COURSES}); has the page layout changed?")
    return out


def report(built: list[Built], grads: list[graduate.GradBuilt] = ()) -> str:
    lines = []
    for b in built:
        status = "OK" if b.ok else "FAILED"
        lines.append(f"{b.program.id:8} {status:6} {b.program.name}: {len(b.courses)} courses")
        if b.courses:
            subjects = Counter(c.code.split(" ", 1)[0] for c in b.courses)
            lines.append("         subjects: " + ", ".join(f"{s} {n}" for s, n in subjects.items()))
            untitled = [c.code for c in b.courses if c.title is None]
            if untitled:
                lines.append(f"         untitled: {len(untitled)} ({', '.join(untitled[:8])}{'...' if len(untitled) > 8 else ''})")
            groups = Counter(c.group for c in b.courses if c.group)
            if groups:
                lines.append("         groups: " + "; ".join(f"{g} ({n})" for g, n in groups.items()))
        for s in b.sources:
            lines.append(f"         source: {s['url']} (updated {s['last_updated'] or 'unknown'})")
        lines.extend(f"         warning: {w}" for w in b.warnings)
        lines.extend(f"         error: {e}" for e in b.errors)
    for g in grads:
        status = "OK" if g.ok else "FAILED"
        lines.append(
            f"{g.id:8} {status:6} {g.defn.get('name')}: {g.defn.get('total_units')} units, "
            f"{len(g.defn.get('requirements', []))} requirements, {len(g.defn.get('courses', {}))} courses"
        )
        for s in g.sources:
            when = s.get("last_updated") or (f"copied {s['copied']}" if s.get("copied") else "unknown")
            lines.append(f"         source: {s.get('url')} ({when})")
        lines.extend(f"         warning: {w}" for w in g.warnings)
        lines.extend(f"         error: {e}" for e in g.errors)
    return "\n".join(lines)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def write_outputs(
    built: list[Built], out_dir: Path, today: date, *, merge_index: bool,
    grads: list[graduate.GradBuilt] = (),
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict] = {}
    index_path = out_dir / "index.json"
    if merge_index and index_path.is_file():
        for e in json.loads(index_path.read_text(encoding="utf-8")).get("programs", []):
            entries[e["id"]] = e
    for b in built:
        (out_dir / f"{b.program.id}.json").write_text(_dump(b.payload(today)), encoding="utf-8", newline="\n")
        dates = [s["last_updated"] for s in b.sources if s["last_updated"]]
        entries[b.program.id] = {
            "id": b.program.id,
            "level": "undergraduate",
            "name": b.program.name,
            "department": b.program.department,
            "college": b.program.college,
            "aliases": list(b.program.aliases),
            "course_count": len(b.courses),
            "last_updated": min(dates) if dates else None,
        }
    for g in grads:
        (out_dir / f"{g.id}.json").write_text(_dump(g.payload(today)), encoding="utf-8", newline="\n")
        entries[g.id] = g.index_entry()
    index = {"built_at": today.isoformat(), "programs": sorted(entries.values(), key=lambda e: e["name"])}
    index_path.write_text(_dump(index), encoding="utf-8", newline="\n")


def fill_titles(built: list[Built]) -> None:
    """Give an untitled code the title another program's page prints for it.

    A shorthand page ("MATH 30, 31, 32") carries no titles, but the same course
    is usually titled on another department's page in the same run, and a code
    names one course across SJSU.
    """
    titles: dict[str, str] = {}
    for b in built:
        for c in b.courses:
            if c.title and c.code not in titles:
                titles[c.code] = c.title
    for b in built:
        filled = 0
        for i, c in enumerate(b.courses):
            if c.title is None and c.code in titles:
                b.courses[i] = Course(c.code, titles[c.code], c.group)
                filled += 1
        if filled:
            b.warnings.append(f"{filled} title(s) taken from other programs' pages")


def run(
    programs: list[Program],
    *,
    pages: dict[str, Page],
    out_dir: Path,
    today: date,
    dry_run: bool,
    merge_index: bool,
    graduates: list[dict] = (),
) -> tuple[int, str]:
    built = [build_program(p, pages, today) for p in programs]
    fill_titles(built)
    grads = [graduate.build_graduate(d, pages, today, stale_after=STALE_AFTER) for d in graduates]
    text = report(built, grads)
    if any(not b.ok for b in built) or any(not g.ok for g in grads):
        return 1, text + "\n\nNothing written: fix the failures above first."
    if dry_run:
        return 0, text + "\n\nDry run: nothing written."
    write_outputs(built, out_dir, today, merge_index=merge_index, grads=grads)
    return 0, text + f"\n\nWrote {len(built) + len(grads)} program file(s) and index.json to {out_dir}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Degree Progress course lists from department pages.")
    parser.add_argument("--program", action="append", help="build only this program id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report; write nothing")
    parser.add_argument("--from-dir", type=Path, help="read saved pages from DIR instead of fetching")
    parser.add_argument("--save-pages", type=Path, help="also save fetched pages to DIR")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    load_dotenv()  # a CLI has to do what main.py does for the server (KB_CRAWL_DELAY)

    programs = load_sources()
    graduates = graduate.load_definitions()
    if args.program:
        wanted = set(args.program)
        unknown = wanted - {p.id for p in programs} - {d["id"] for d in graduates}
        if unknown:
            print(f"unknown program id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        programs = [p for p in programs if p.id in wanted]
        graduates = [d for d in graduates if d["id"] in wanted]

    urls = list(dict.fromkeys(
        [s.url for p in programs for s in p.sources]
        + [u for d in graduates for u in graduate.page_urls(d)]
    ))
    if args.from_dir:
        pages = read_pages(urls, args.from_dir)
    else:
        pages = asyncio.run(fetch_pages(urls, save_dir=args.save_pages))

    code, text = run(
        programs, pages=pages, out_dir=args.out, today=date.today(),
        dry_run=args.dry_run, merge_index=bool(args.program), graduates=graduates,
    )
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
