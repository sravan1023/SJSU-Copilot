# Department program page fixtures

Captured 2026-10-01, one request per page from www.sjsu.edu, with the KB bot user agent (`kb.robots.USER_AGENT`), after a robots.txt check and with the crawl delay between requests. This was the approved pilot fetch for `campus/programs`.

Each file keeps only the page's `<main id="sjsu-maincontent">`, unchanged, plus its `Last Updated` line in a `<footer>`. The page chrome is dropped. Each file also gets a fake `<nav>` holding `CMPE 999 - Navigation must be ignored`, so a test can prove the extractor reads only `<main>`. None of the main sections contain a `mailto:` link.

| File | Source URL | Last Updated | Notes |
|---|---|---|---|
| `ae_programs_bsae_prerequisite.html` | https://www.sjsu.edu/ae/programs/bsae/prerequisite.php | Apr 25, 2025 | A table with one row per course: `Math 30 - Calculus I` and a prerequisite cell that also names codes (`Math 30 or 30X`). 42 courses. |
| `bme_programs_bs-bme_curriculum.html` | https://www.sjsu.edu/bme/programs/bs-bme/curriculum.php | Feb 26, 2026 | `<br>`-separated lines under bold section titles. The units suffix is `4 unit(s) (B2+B3)`. 41 courses, including the four alternative American Institutions sequences. |
| `cmpe_undergraduate_programs_undergraduate_courses.html` | https://www.sjsu.edu/cmpe/undergraduate_programs/undergraduate_courses.php | May 9, 2026 | Two `<br>`-separated paragraphs of `CMPE n - Title`, 47 courses. A special-topics table with no codes follows. |
| `ee_undergraduate-program_syllabi.html` | https://www.sjsu.edu/ee/undergraduate-program/syllabi.php | Aug 12, 2026 | `<ul>` lists under `<h2>` "Required EE Courses" (17) and "Technical Electives" (20). |
| `ise_programs_bs-ise_program-requirements.html` | https://www.sjsu.edu/ise/programs/bs-ise/program-requirements.php | Jan 19, 2022 | Shorthand with no titles: `MATH 30, 31, 32, 123 - 13 units`, `PHYS 50/51`. Prose under "Additional Requirements/Policies" names codes that are not part of the list. 29 courses. |

Don't re-fetch these casually, because www.sjsu.edu asks not to be overloaded. `python -m campus.programs.build --save-pages DIR` keeps whole pages if a layout changes.

## Graduate program pages

Captured 2026-10-02 the same way, for the hand-written rule files in `campus/programs/graduate/`. The tests check that every course a rule file names still appears on its pages (`graduate.drift`).

| File | Source URL | Last Updated |
|---|---|---|
| `cmpe_graduate_programs_ms-ai_msaiprogram-requirements.html` | https://www.sjsu.edu/cmpe/graduate_programs/ms-ai/msaiprogram-requirements.php | May 6, 2026 |
| `msse_program-requirements_index.html` | https://www.sjsu.edu/msse/program-requirements/index.php | May 24, 2026 |
| `msse_program-requirements_enterprise-software-technologies.html` | https://www.sjsu.edu/msse/program-requirements/enterprise-software-technologies.php | Aug 17, 2024 |
| `msse_program-requirements_data-science.html` | https://www.sjsu.edu/msse/program-requirements/data-science.php | Aug 17, 2024 |
| `msse_program-requirements_cloud-computing-and-virtualization.html` | https://www.sjsu.edu/msse/program-requirements/cloud-computing-and-virtualization.php | Aug 17, 2024 |
| `msse_program-requirements_software-systems-engineering.html` | https://www.sjsu.edu/msse/program-requirements/software-systems-engineering.php | Aug 17, 2024 |
| `msse_program-requirements_networking-software.html` | https://www.sjsu.edu/msse/program-requirements/networking-software.php | Aug 17, 2024 |
| `msse_program-requirements_cybersecurity.html` | https://www.sjsu.edu/msse/program-requirements/cybersecurity.php | Aug 17, 2024 |

MS CS has no fixture. Its rules live only in the catalog, which can't be fetched, so `mscs.json` was written from catalog text a student pasted on 2026-10-02.
