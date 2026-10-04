import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft, GraduationCap, CalendarRange, CheckCircle2, Circle, Diamond, Info, X, Trash2, ExternalLink, Copy, Search,
} from 'lucide-react';
import {
  emptyReport,
  isEmptyReport,
  localDay,
  MAX_LOAD,
  MIN_LOAD,
  REPORT_CHAR_BUDGET,
  reportChars,
  rolloverDue,
  sanitizeReport,
  setStatus,
} from './selfReport.ts';
import { fetchProgram, fetchProgramIndex, matchProgram } from './programs.ts';
import {
  completion,
  evaluate,
  listed,
  nextTerm,
  planNext,
  planText,
  requirementState,
  resolveChoices,
  scheduleUrl,
  searchCourses,
  shortfall,
  summarize,
  titleOf,
  withPlanned,
} from './requirements.ts';
import { createSaveQueue } from './saveQueue.ts';
import { fetchDegreeProgress, saveDegreeProgress } from '../services/profileService';

/**
 * Degree Progress (graduate programs): a service page, like Settings.
 *
 * The student's major and class standing in Settings pick the program. They
 * mark courses Done, Taking or Planned; the page shows how Done and Taking
 * count toward each requirement (requirements.ts evaluate), summarizes it the
 * way MyProgress does, and plans next semester (planNext). Planned courses are
 * next term's intent: they never count, only show in a "with your plan" line.
 *
 * Changes save themselves (saveQueue.ts): debounced, one save at a time,
 * flushed on Back and on leaving the page, with a beforeunload guard while a
 * change is unsaved. The record lives in
 * profile_audience_details.details.degree_progress.
 *
 * It is a planning guide, not an audit: it never says the degree is done, and
 * MyProgress and the student's advisor are authoritative.
 */

const STATUS_META = {
  done: { label: 'Done', active: 'border-emerald-600/60 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
  taking: { label: 'Taking', active: 'border-sjsu-gold bg-sjsu-gold/15 text-text-primary' },
  planned: { label: 'Planned', active: 'border-sky-600/60 bg-sky-500/15 text-sky-700 dark:text-sky-300' },
};
const ROW_STATUSES = ['done', 'taking', 'planned'];
const COLLAPSE_AFTER = 8;
const UNDO_MS = 10000;

const selectClass =
  'w-full bg-bg-surface border border-border-color rounded-lg px-3 py-2.5 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-sjsu-gold/40 focus:border-sjsu-gold transition-all';
const labelClass = 'block text-xs font-semibold text-text-secondary mb-1.5 uppercase tracking-wide';
const linkButton = 'underline hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold rounded';

const isMarked = (s) => s === 'done' || s === 'taking' || s === 'planned';
const byCode = (a, b) => a.localeCompare(b, undefined, { numeric: true });

/** "Area A (at least 3 units)" -> "Area A". */
function shortLabel(label) {
  return label.replace(/\s*\(.*\)\s*$/, '');
}

function scrollToRequirement(id) {
  const el = document.getElementById(`dp-req-${id}`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  el.focus({ preventScroll: true });
}

function Notice({ tone = 'info', children, action }) {
  const cls =
    tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300'
      : 'border-border-color bg-bg-surface text-text-secondary';
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4 text-sm ${cls}`}>
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  );
}

function ActionButton({ onClick, children, primary = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-h-11 sm:min-h-0 shrink-0 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
        primary ? 'border-sjsu-gold bg-sjsu-gold text-white hover:bg-sjsu-gold-hover' : 'border-border-color bg-bg-surface text-text-primary hover:bg-bg-hover'
      }`}
    >
      {children}
    </button>
  );
}

function Card({ children, className = '', id }) {
  return (
    <section id={id} tabIndex={id ? -1 : undefined} className={`rounded-xl border border-border-color bg-bg-surface p-5 focus:outline-none ${className}`}>
      {children}
    </section>
  );
}

/** MyProgress's three states: check, diamond, empty circle. Never icon-only for screen readers. */
function StateIcon({ state, size = 17 }) {
  if (state === 'complete') {
    return (
      <>
        <CheckCircle2 size={size} className="shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
        <span className="sr-only">Complete:</span>
      </>
    );
  }
  if (state === 'in-progress') {
    return (
      <>
        <Diamond size={size} className="shrink-0 fill-amber-400 text-amber-500" aria-hidden="true" />
        <span className="sr-only">In progress:</span>
      </>
    );
  }
  return (
    <>
      <Circle size={size} className="shrink-0 text-text-secondary" aria-hidden="true" />
      <span className="sr-only">Not started:</span>
    </>
  );
}

/**
 * Done / Taking / Planned for one course, as a radio group: arrow keys move
 * and select, and choosing the selected option again clears it.
 */
function StatusControl({ code, status, onChange }) {
  const refs = useRef([]);
  const current = ROW_STATUSES.indexOf(status);
  const focusIndex = current >= 0 ? current : 0;
  const onKeyDown = (e, i) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (i + step + ROW_STATUSES.length) % ROW_STATUSES.length;
    refs.current[next]?.focus();
    onChange(ROW_STATUSES[next]);
  };
  return (
    <div role="radiogroup" aria-label={`${code} status`} className="flex shrink-0 gap-1">
      {ROW_STATUSES.map((s, i) => {
        const on = status === s;
        return (
          <button
            key={s}
            ref={(el) => { refs.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={i === focusIndex ? 0 : -1}
            onKeyDown={(e) => onKeyDown(e, i)}
            onClick={() => onChange(on ? null : s)}
            title={on ? 'Select again to clear' : undefined}
            className={`min-h-11 sm:min-h-0 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
              on ? STATUS_META[s].active : 'border-border-color text-text-secondary hover:bg-bg-hover hover:text-text-primary'
            }`}
          >
            {STATUS_META[s].label}
          </button>
        );
      })}
    </div>
  );
}

function CourseRow({ code, title, status, onChange, note, tags, onRemove }) {
  return (
    <li className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <div className="min-w-0 text-sm">
        <span className="font-semibold text-text-primary">{code}</span>
        {title && <span className="text-text-secondary"> &middot; {title}</span>}
        {(tags?.length > 0 || note) && (
          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-text-secondary">
            {tags?.map((t) => (
              <span key={t} className="rounded border border-border-color px-1.5 py-px">{t}</span>
            ))}
            {note && <span>{note}</span>}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1">
        <StatusControl code={code} status={status} onChange={onChange} />
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${code}`}
            className="min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 rounded-md p-1 text-text-secondary hover:bg-bg-hover hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
          >
            <X size={14} className="mx-auto" />
          </button>
        )}
      </div>
    </li>
  );
}

function UnitsBar({ done, taking, planned, total }) {
  const pct = (n) => `${Math.min(100, (n / total) * 100)}%`;
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-bg-hover" aria-hidden="true">
      <div className="flex h-full">
        <div className="bg-emerald-500" style={{ width: pct(done) }} />
        <div className="bg-sjsu-gold" style={{ width: pct(taking) }} />
        <div className="bg-sky-400/70" style={{ width: pct(planned) }} />
      </div>
    </div>
  );
}

/** Is a course marked anywhere, and where did it count? */
function rowNote(program, code, status, assignedTo, reqId) {
  if (status === 'planned') return 'Planned for next term (not counted yet)';
  if (status !== 'done' && status !== 'taking') return null;
  const owner = assignedTo.get(code)?.req;
  if (!owner) return 'Not counted: this requirement or its limit is already full';
  if (owner !== reqId) return `Counted in ${program.requirements.find((r) => r.id === owner)?.label ?? 'another requirement'}`;
  return null;
}

/** One requirement: its state, what it still needs, and its courses. */
function RequirementCard({ program, rp, marks, assignedTo, onMark, choiceLabel }) {
  const [expanded, setExpanded] = useState(false);
  const req = rp.requirement;
  const counted = rp.doneUnits + rp.takingUnits;
  const missing = shortfall(rp);

  return (
    <Card id={`dp-req-${req.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="flex items-center gap-2 font-semibold text-text-primary">
            <StateIcon state={requirementState(rp)} />
            {req.label}
          </h4>
          {req.description && <p className="mt-0.5 text-xs text-text-secondary">{req.description}</p>}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-medium text-text-primary">{Math.min(counted, req.units)} of {req.units} units</div>
          {missing && rp.body && <div className="text-xs text-text-secondary">{missing}</div>}
        </div>
      </div>

      {!rp.body ? (
        <p className="mt-3 text-sm text-text-secondary">Choose your {choiceLabel?.toLowerCase() || 'option'} above to see these courses.</p>
      ) : (
        rp.pools.map((pool) => {
          // Courses a rule accepts but doesn't list (MS SE electives) show once marked.
          const extra = pool.match
            ? Object.keys(marks).filter((c) => !pool.codes.includes(c) && assignedTo.get(c)?.req === req.id)
            : [];
          const codes = [...pool.codes, ...extra];
          const marked = codes.filter((c) => isMarked(marks[c]));
          const visible = expanded || codes.length <= COLLAPSE_AFTER
            ? codes
            : [...new Set([...codes.slice(0, COLLAPSE_AFTER - 2), ...marked])];
          return (
            <div key={pool.id} className="mt-3">
              {pool.label && <div className="text-xs font-semibold uppercase tracking-wide text-text-secondary">{pool.label}</div>}
              <ul className="divide-y divide-border-color">
                {visible.map((code) => (
                  <CourseRow
                    key={code}
                    code={code}
                    title={titleOf(program, code)}
                    status={isMarked(marks[code]) ? marks[code] : null}
                    note={rowNote(program, code, marks[code], assignedTo, req.id)}
                    onChange={(s) => onMark(code, s)}
                  />
                ))}
              </ul>
              {codes.length > visible.length && (
                <button
                  type="button"
                  aria-expanded={false}
                  onClick={() => setExpanded(true)}
                  className={`mt-1 text-xs font-medium text-text-secondary ${linkButton}`}
                >
                  Show all {codes.length} courses
                </button>
              )}
            </div>
          );
        })
      )}
    </Card>
  );
}

/**
 * Requirements whose course lists overlap, shown as one section: the
 * specialization and the electives. Each keeps its own heading, units and
 * shortfall; below them is one shared list, each course tagged with the
 * specializations and elective areas that list it.
 */
function MergedGroupCard({ program, title, items, marks, assignedTo, onMark, choices }) {
  const [expanded, setExpanded] = useState(false);
  const units = items.reduce((n, rp) => n + rp.requirement.units, 0);
  const counted = items.reduce((n, rp) => n + Math.min(rp.doneUnits + rp.takingUnits, rp.requirement.units), 0);
  const states = items.map(requirementState);
  const groupState = states.every((s) => s === 'complete') ? 'complete' : states.some((s) => s !== 'open') ? 'in-progress' : 'open';

  const inferred = program.choices.find((c) => c.infer && items.some((rp) => rp.requirement.choice === c.id));
  const trackReq = inferred ? items.find((rp) => rp.requirement.choice === inferred.id)?.requirement : null;
  const trackId = inferred ? choices[inferred.id] : null;
  const trackLabel = trackId ? inferred.options.find((o) => o.id === trackId)?.label : null;

  const tags = new Map();
  const tag = (code, t) => tags.set(code, [...new Set([...(tags.get(code) ?? []), t])]);
  const codes = new Set();
  if (trackReq) {
    for (const [opt, body] of Object.entries(trackReq.by_option ?? {})) {
      const label = inferred.options.find((o) => o.id === opt)?.label ?? opt;
      for (const c of listed(body)) {
        codes.add(c);
        tag(c, label);
      }
    }
  }
  for (const rp of items) {
    for (const p of rp.pools) {
      for (const c of p.codes) {
        codes.add(c);
        if (!rp.requirement.choice && p.label) tag(c, shortLabel(p.label));
      }
    }
  }
  for (const [code, a] of assignedTo) if (items.some((rp) => rp.requirement.id === a.req)) codes.add(code);
  for (const [code, s] of Object.entries(marks)) if (s === 'planned' && program.courses[code] && tags.has(code)) codes.add(code);

  const all = [...codes].sort(byCode);
  const visible = expanded || all.length <= 12 ? all : all.filter((c, i) => i < 10 || isMarked(marks[c]));
  const labelOf = (id) => program.requirements.find((r) => r.id === id)?.label ?? 'another requirement';

  const where = (code) => {
    const status = marks[code];
    if (status === 'planned') return 'Planned for next term';
    if (status !== 'done' && status !== 'taking') return null;
    const a = assignedTo.get(code);
    if (!a) return 'Not counted: a limit is reached';
    const pool = items.find((rp) => rp.requirement.id === a.req)?.pools.find((p) => p.id === a.pool);
    return `Counts toward ${labelOf(a.req)}${pool && pool.id !== 'main' && pool.label ? ` · ${shortLabel(pool.label)}` : ''}`;
  };

  return (
    <Card id={`dp-req-${items[0].requirement.id}`}>
      <div className="flex items-start justify-between gap-3">
        <h4 className="flex items-center gap-2 font-semibold text-text-primary">
          <StateIcon state={groupState} />
          {shortLabel(title)}
        </h4>
        <span className="shrink-0 text-sm font-medium text-text-primary">{counted} of {units} units</span>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {items.map((rp) => {
          const req = rp.requirement;
          const isTrack = inferred && req.choice === inferred.id;
          const missing = shortfall(rp);
          return (
            <div key={req.id} id={`dp-req-${req.id}`} tabIndex={-1} className="rounded-lg border border-border-color p-3 focus:outline-none">
              <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                <StateIcon state={requirementState(rp)} size={14} />
                {req.label}
                <span className="ml-auto text-xs font-normal text-text-secondary">
                  {Math.min(rp.doneUnits + rp.takingUnits, req.units)} of {req.units}
                </span>
              </div>
              {isTrack && (
                <p className="mt-1 text-xs text-text-secondary">
                  {trackLabel ? (
                    <><span className="font-semibold text-text-primary">{trackLabel}</span>, set by your courses</>
                  ) : (
                    <>Not set yet: {req.units} units from the same {inferred.label.toLowerCase()} set it.</>
                  )}
                </p>
              )}
              {!req.choice && rp.pools.some((p) => p.min_units || p.max_units) && (
                <p className="mt-1 text-xs text-text-secondary">
                  {rp.pools
                    .filter((p) => p.min_units || p.max_units)
                    .map((p) => {
                      const got = rp.assigned.filter((a) => a.pool === p.id).reduce((n, a) => n + a.units, 0);
                      return `${shortLabel(p.label)}: ${got}${p.min_units ? ` (at least ${p.min_units})` : ''}${p.max_units ? ` (up to ${p.max_units})` : ''}`;
                    })
                    .join(' · ')}
                </p>
              )}
              {missing && rp.body && <p className="mt-1 text-xs text-text-primary">Needs {missing}.</p>}
            </div>
          );
        })}
      </div>

      <ul className="mt-3 divide-y divide-border-color">
        {visible.map((code) => (
          <CourseRow
            key={code}
            code={code}
            title={titleOf(program, code)}
            status={isMarked(marks[code]) ? marks[code] : null}
            tags={tags.get(code)}
            note={where(code)}
            onChange={(s) => onMark(code, s)}
          />
        ))}
      </ul>
      {all.length > visible.length && (
        <button
          type="button"
          aria-expanded={false}
          onClick={() => setExpanded(true)}
          className={`mt-1 text-xs font-medium text-text-secondary ${linkButton}`}
        >
          Show all {all.length} courses
        </button>
      )}
    </Card>
  );
}

/** Search the program's courses by code or title; Enter marks the course Done. */
function CourseSearch({ program, marks, onPick }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const hits = useMemo(() => searchCourses(program, query), [program, query]);
  const showList = open && hits.length > 0;
  const activeIndex = Math.min(active, Math.max(hits.length - 1, 0));

  const pick = (hit) => {
    onPick(hit);
    setQuery('');
    setOpen(false);
    setActive(0);
  };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, hits.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Enter') {
      if (showList && hits[activeIndex]) {
        e.preventDefault();
        pick(hits[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="relative">
      <label htmlFor="dp-search" className={labelClass}>Add a course</label>
      <div className="relative">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" aria-hidden="true" />
        <input
          id="dp-search"
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls="dp-search-list"
          aria-autocomplete="list"
          aria-activedescendant={showList ? `dp-search-opt-${activeIndex}` : undefined}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          maxLength={40}
          autoComplete="off"
          placeholder="Type a code or title, e.g. CMPE 258 or deep learning, then Enter"
          className={`${selectClass} pl-9`}
        />
      </div>
      {showList && (
        <ul
          id="dp-search-list"
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-border-color bg-bg-surface py-1 shadow-lg"
        >
          {hits.map((h, i) => (
            <li
              key={h.code}
              id={`dp-search-opt-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(h);
              }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer px-3 py-2 text-sm ${i === activeIndex ? 'bg-bg-hover' : ''}`}
            >
              <span className="font-semibold text-text-primary">{h.code}</span>
              {h.title && <span className="text-text-secondary"> &middot; {h.title}</span>}
              <div className="text-xs text-text-secondary">
                {h.other
                  ? 'Not listed for your program: counts only where a rule accepts any such course'
                  : h.requirements.join(', ')}
                {isMarked(marks[h.code]) && ` · already ${STATUS_META[marks[h.code]].label}`}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PlanCard({ program, plan, ev, term, load, onLoad, marks, onMark }) {
  const [copied, setCopied] = useState('');
  const state = completion(ev);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(planText(program, plan, term, ev));
      setCopied('Plan copied');
    } catch {
      setCopied("Couldn't copy the plan");
    }
    setTimeout(() => setCopied(''), 2500);
  };
  const togglePlanned = (code) => onMark(code, marks[code] === 'planned' ? null : 'planned');

  return (
    <Card className="border-sjsu-gold/50">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-lg font-bold text-text-primary">
          <CalendarRange size={18} className="text-sjsu-gold" aria-hidden="true" />
          Plan for {term.label}
        </h3>
        <div className="flex flex-wrap items-center gap-3 text-sm text-text-secondary">
          <div className="flex items-center gap-2">
            <span id="dp-load-label">Courses</span>
            <div role="radiogroup" aria-labelledby="dp-load-label" className="flex gap-1">
              {Array.from({ length: MAX_LOAD - MIN_LOAD + 1 }, (_, i) => MIN_LOAD + i).map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={load === n}
                  onClick={() => onLoad(n)}
                  className={`h-11 w-11 sm:h-8 sm:w-8 rounded-md border text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
                    load === n ? 'border-sjsu-gold bg-sjsu-gold/15 text-text-primary' : 'border-border-color hover:bg-bg-hover'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button"
            onClick={copy}
            className="flex min-h-11 sm:min-h-0 items-center gap-1 rounded-md border border-border-color px-2.5 py-1 text-xs font-medium hover:bg-bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
          >
            <Copy size={13} aria-hidden="true" /> Copy plan
          </button>
        </div>
      </div>
      <p className="sr-only" aria-live="polite">{copied}</p>
      {copied && <p className="mt-1 text-xs text-text-secondary" aria-hidden="true">{copied}</p>}

      {state !== 'open' ? (
        <div className="mt-4 text-sm text-text-primary">
          <p>
            Your marked courses fill every requirement listed here
            {state === 'covered-if-passed' ? ', once this term’s courses are passed' : ''}.
          </p>
          {program.conditions?.length > 0 && (
            <>
              <p className="mt-2">Graduation also needs:</p>
              <ul className="mt-1 list-disc pl-5 text-text-secondary">
                {program.conditions.map((c) => <li key={c}>{c}</li>)}
              </ul>
            </>
          )}
          <p className="mt-2 text-text-secondary">MyProgress decides; check it and talk to your advisor about applying to graduate.</p>
        </div>
      ) : plan.slots.length === 0 ? (
        <p className="mt-4 text-sm text-text-secondary">
          Nothing to plan right now{plan.notes.length ? ': see the notes below' : ''}.
        </p>
      ) : (
        <ol className="mt-4 space-y-3">
          {plan.slots.map((slot, i) => (
            <li key={`${slot.requirement.id}-${i}`} className="rounded-lg border border-border-color p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
                  {slot.requirement.label}
                  {slot.pool && ` · ${slot.pool}`}
                </span>
                {slot.kind === 'choose' && (
                  <span className="text-xs text-text-secondary">
                    Pick {slot.count} of {slot.options.length}
                    {slot.planned?.length ? ` · ${slot.planned.length} planned` : ''}
                  </span>
                )}
              </div>
              {slot.kind === 'take' ? (
                <ul className="mt-1 space-y-1">
                  {slot.options.map((code) => {
                    const planned = marks[code] === 'planned';
                    return (
                      <li key={code} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                        <span>
                          <span className="font-semibold text-text-primary">{code}</span>
                          {titleOf(program, code) && <span className="text-text-secondary"> &middot; {titleOf(program, code)}</span>}
                        </span>
                        <button
                          type="button"
                          aria-pressed={planned}
                          onClick={() => togglePlanned(code)}
                          className={`min-h-11 sm:min-h-0 rounded-md border px-2.5 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
                            planned ? STATUS_META.planned.active : 'border-border-color hover:bg-bg-hover'
                          }`}
                        >
                          {planned ? `Planned for ${term.label}` : `Add to ${term.label}`}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {slot.options.map((code) => {
                    const planned = marks[code] === 'planned';
                    return (
                      <button
                        key={code}
                        type="button"
                        aria-pressed={planned}
                        onClick={() => togglePlanned(code)}
                        title={planned ? `Planned for ${term.label}: select to remove` : `Add to ${term.label}`}
                        className={`min-h-11 sm:min-h-0 rounded-full border px-2.5 py-0.5 text-left text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
                          planned ? STATUS_META.planned.active : 'border-border-color text-text-primary hover:bg-bg-hover'
                        }`}
                      >
                        {code}
                        {titleOf(program, code) && <span className="text-text-secondary"> &middot; {titleOf(program, code)}</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      {state === 'open' && plan.notes.length > 0 && (
        <ul className="mt-4 space-y-1 text-xs text-text-secondary">
          {plan.notes.map((n, i) => (
            <li key={i}>
              {n.course ? <span className="font-semibold">{n.course}: </span> : null}
              {n.text}
            </li>
          ))}
        </ul>
      )}

      {state === 'open' && (
        <p className="mt-4 text-xs text-text-secondary">
          Select a course to add it to {term.label}. Assumes your Taking courses are passed this term. The plan doesn&apos;t
          know what&apos;s offered:{' '}
          <a href={scheduleUrl(term.key)} target="_blank" rel="noopener noreferrer" className={`inline-flex items-center gap-0.5 ${linkButton}`}>
            check the {term.label} class schedule<ExternalLink size={11} aria-hidden="true" />
          </a>
          .
        </p>
      )}
    </Card>
  );
}

function SaveStatus({ state, onRetry }) {
  let text = '';
  if (state === 'pending' || state === 'saving') text = 'Saving…';
  else if (state === 'saved') text = 'All changes saved';
  return (
    <div className="flex items-center gap-2 text-xs text-text-secondary" aria-live="polite">
      {state === 'error' ? (
        <>
          <span className="text-red-600 dark:text-red-400">Couldn&apos;t save</span>
          <button type="button" onClick={onRetry} className={linkButton}>Retry</button>
        </>
      ) : (
        text
      )}
    </div>
  );
}

export default function DegreeProgressPage({ onBack, user, profile, onOpenSettings }) {
  const [report, setReport] = useState(emptyReport);
  const reportRef = useRef(report);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const [programs, setPrograms] = useState(null);
  const [programsError, setProgramsError] = useState('');
  const [program, setProgram] = useState(null);
  const [programError, setProgramError] = useState('');

  const queueRef = useRef(null);
  const [saveState, setSaveState] = useState('idle');
  const [leaveBlocked, setLeaveBlocked] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [undoReport, setUndoReport] = useState(null);
  const undoTimer = useRef(null);
  const [rolloverDismissed, setRolloverDismissed] = useState(false);
  const [announce, setAnnounce] = useState('');

  // The student's saved record.
  useEffect(() => {
    let alive = true;
    async function load() {
      setLoadError('');
      if (!user?.id) {
        setLoading(false);
        return;
      }
      try {
        const loaded = sanitizeReport(await fetchDegreeProgress(user.id));
        if (alive) {
          reportRef.current = loaded;
          setReport(loaded);
        }
      } catch (err) {
        if (alive) setLoadError(err?.message || 'Could not load your saved progress.');
      } finally {
        if (alive) setLoading(false);
      }
    }
    load();
    return () => { alive = false; };
  }, [user?.id, reloadKey]);

  // Autosave: one queue per signed-in student, flushed when the page goes away.
  useEffect(() => {
    if (!user?.id) return undefined;
    const userId = user.id;
    const queue = createSaveQueue(async (next) => {
      const clean = sanitizeReport(next);
      if (reportChars(clean) > REPORT_CHAR_BUDGET) throw new Error('Too many courses to save.');
      await saveDegreeProgress(userId, isEmptyReport(clean) ? null : clean);
    });
    queueRef.current = queue;
    const off = queue.onState(setSaveState);
    return () => {
      off();
      queueRef.current = null;
      queue.flush().finally(() => queue.dispose());
    };
  }, [user?.id]);

  // Closing the tab with an unsaved change asks first.
  useEffect(() => {
    if (saveState !== 'pending' && saveState !== 'saving' && saveState !== 'error') return undefined;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [saveState]);

  useEffect(() => () => clearTimeout(undoTimer.current), []);

  // The programs on offer; graduate programs only for now.
  useEffect(() => {
    let alive = true;
    async function load() {
      setProgramsError('');
      try {
        const list = await fetchProgramIndex();
        if (alive) setPrograms(list.filter((p) => p.level === 'graduate'));
      } catch (err) {
        if (alive) setProgramsError(err?.message || 'Could not load the program list.');
      }
    }
    load();
    return () => { alive = false; };
  }, [reloadKey]);

  const isGraduate = profile?.class_standing === 'Graduate';
  const major = (profile?.major || '').trim();
  const matched = useMemo(
    () => (programs && isGraduate ? matchProgram(major, programs, 'graduate') : null),
    [programs, isGraduate, major]
  );
  const programId = matched?.id ?? null;

  // That program's rules. A stale response is dropped.
  useEffect(() => {
    let alive = true;
    async function load() {
      setProgramError('');
      if (!programId) {
        setProgram(null);
        return;
      }
      try {
        const data = await fetchProgram(programId);
        if (alive) setProgram(data);
      } catch (err) {
        if (alive) {
          setProgram(null);
          setProgramError(err?.message || 'Could not load this program’s requirements.');
        }
      }
    }
    load();
    return () => { alive = false; };
  }, [programId, reloadKey]);

  // The specialization comes from the courses; project or thesis is asked.
  const choices = useMemo(
    () => (program ? resolveChoices(program, report.courses, report.choices) : {}),
    [program, report.courses, report.choices]
  );
  const ev = useMemo(() => (program ? evaluate(program, report.courses, choices) : null), [program, report.courses, choices]);
  const plan = useMemo(
    () => (program ? planNext(program, report.courses, choices, report.load) : null),
    [program, report.courses, choices, report.load]
  );
  const anyPlanned = Object.values(report.courses).includes('planned');
  const projected = useMemo(() => {
    if (!program || !anyPlanned) return null;
    const marks = withPlanned(report.courses);
    return evaluate(program, marks, resolveChoices(program, marks, report.choices));
  }, [program, anyPlanned, report.courses, report.choices]);
  const summary = useMemo(() => (ev ? summarize(ev) : null), [ev]);
  const term = useMemo(() => nextTerm(), []);
  const assignedTo = useMemo(() => {
    const m = new Map();
    for (const rp of ev?.requirements ?? []) for (const a of rp.assigned) m.set(a.code, { req: rp.requirement.id, pool: a.pool });
    return m;
  }, [ev]);

  /** Every change goes through here: state, then a scheduled save. */
  const apply = useCallback((fn) => {
    const next = { ...fn(reportRef.current), updated_at: localDay() };
    reportRef.current = next;
    setReport(next);
    setLeaveBlocked(false);
    queueRef.current?.schedule(next);
  }, []);
  const mark = useCallback(
    (code, status) => apply((prev) => ({ ...prev, courses: setStatus(prev.courses, code, status) })),
    [apply]
  );
  const choose = (id, value) =>
    apply((prev) => {
      const next = { ...prev.choices };
      if (value) next[id] = value;
      else delete next[id];
      return { ...prev, choices: next };
    });

  const handleBack = async () => {
    const queue = queueRef.current;
    if (queue && queue.unsaved()) {
      const ok = await queue.flush();
      if (!ok) {
        setLeaveBlocked(true);
        return;
      }
    }
    onBack();
  };

  const doClear = () => {
    const previous = reportRef.current;
    apply(() => ({ ...emptyReport(), load: previous.load }));
    setConfirmClear(false);
    setUndoReport(previous);
    clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setUndoReport(null), UNDO_MS);
  };
  const doUndo = () => {
    const previous = undoReport;
    setUndoReport(null);
    clearTimeout(undoTimer.current);
    if (previous) apply(() => previous);
  };

  const onPick = (hit) => {
    if (report.courses[hit.code] === 'done') {
      setAnnounce(`${hit.code} is already Done`);
      return;
    }
    mark(hit.code, 'done');
    setAnnounce(`Marked ${hit.code} Done`);
  };

  const takingCodes = Object.keys(report.courses).filter((c) => report.courses[c] === 'taking').sort(byCode);
  const plannedCodes = Object.keys(report.courses).filter((c) => report.courses[c] === 'planned').sort(byCode);
  const showRollover = !rolloverDismissed && rolloverDue(report);

  const covered = programs ? programs.map((p) => p.name).join(', ') : '';
  const groups = useMemo(() => {
    const out = [];
    for (const rp of ev?.requirements ?? []) {
      const title = rp.requirement.group ?? null;
      const last = out[out.length - 1];
      if (last && last.title === title) last.items.push(rp);
      else out.push({ title, items: [rp] });
    }
    return out;
  }, [ev]);
  const uncountedListed = (ev?.uncounted ?? []).filter((u) => program?.courses[u.code]);
  const others = program
    ? Object.keys(report.courses).filter((c) => isMarked(report.courses[c]) && !program.courses[c]).sort(byCode)
    : [];
  const retry = () => setReloadKey((k) => k + 1);
  const settingsAction = (label) => (onOpenSettings ? <ActionButton onClick={onOpenSettings}>{label}</ActionButton> : null);

  return (
    <div className="flex-1 flex flex-col bg-bg-main overflow-y-auto transition-colors duration-300">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-bg-main/80 backdrop-blur-md border-b border-border-color px-4 sm:px-8 py-4 flex items-center gap-4">
        <button
          type="button"
          onClick={handleBack}
          className="p-2 rounded-lg hover:bg-bg-hover transition-colors text-text-secondary hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
          aria-label="Back to Chat"
          title="Back to Chat"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-text-primary">Degree Progress</h1>
          <p className="text-sm text-text-secondary">Track your degree requirements and plan next semester</p>
        </div>
        <SaveStatus state={saveState} onRetry={() => queueRef.current?.retry()} />
      </div>

      <div className="flex-1 flex justify-center px-4 sm:px-6 py-6">
        <div className="w-full max-w-3xl space-y-5">
          <p className="sr-only" aria-live="polite">{announce}</p>

          {leaveBlocked && (
            <Notice
              tone="error"
              action={
                <div className="flex gap-2">
                  <ActionButton onClick={() => queueRef.current?.retry()}>Try again</ActionButton>
                  <ActionButton onClick={onBack}>Leave anyway</ActionButton>
                </div>
              }
            >
              Your latest changes didn&apos;t save.
            </Notice>
          )}
          {(loadError || programsError || programError) && (
            <Notice tone="error" action={<ActionButton onClick={retry}>Retry</ActionButton>}>
              {loadError || programsError || programError}
            </Notice>
          )}

          {/* Which program, from Settings: one clear card per missing piece */}
          {programs && !isGraduate && (
            <Notice action={settingsAction('Update class standing')}>
              Degree Progress covers graduate programs for now: {covered}. If you&apos;re a graduate student, set your
              Class Standing to Graduate in Settings.
            </Notice>
          )}
          {programs && isGraduate && !major && (
            <Notice action={settingsAction('Add your major')}>
              Add your major in Settings and your program&apos;s requirements will show up here.
            </Notice>
          )}
          {programs && isGraduate && major && !matched && (
            <Notice action={settingsAction('Check your major')}>
              There&apos;s no plan for &ldquo;{major}&rdquo; yet. Covered programs: {covered}. If yours is one of them,
              check how your major is spelled in Settings.
            </Notice>
          )}
          {(loading || (matched && !program && !programError)) && (
            <div className="space-y-3" aria-busy="true">
              <div className="h-24 animate-pulse rounded-xl bg-bg-surface" />
              <div className="h-40 animate-pulse rounded-xl bg-bg-surface" />
              <span className="sr-only">Loading…</span>
            </div>
          )}

          {program && ev && plan && summary && !loading && (
            <>
              {/* The program and its authority, in one line */}
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <GraduationCap size={18} className="text-sjsu-gold" aria-hidden="true" />
                <span className="font-bold text-text-primary">{program.name}</span>
                <span className="text-text-secondary">&middot; {program.total_units} units &middot;</span>
                {onOpenSettings && (
                  <button type="button" onClick={onOpenSettings} className={`text-text-secondary ${linkButton}`}>
                    from your major in Settings
                  </button>
                )}
              </div>
              <p className="-mt-3 flex gap-1.5 text-xs text-text-secondary">
                <Info size={13} className="mt-0.5 shrink-0 text-sjsu-gold" aria-hidden="true" />
                <span>
                  A planning guide from{' '}
                  {program.sources.some((s) => s.kind === 'page') ? (
                    <>
                      the {program.department} department&apos;s{' '}
                      <a href={program.sources.find((s) => s.kind === 'page').url} target="_blank" rel="noopener noreferrer" className={linkButton}>
                        published requirements
                      </a>
                    </>
                  ) : (
                    <>the SJSU catalog, copied {program.sources[0]?.copied}</>
                  )}
                  . MyProgress in MySJSU and your advisor are authoritative.
                </span>
              </p>

              {showRollover && (
                <Notice
                  action={
                    <div className="flex flex-wrap gap-2">
                      {takingCodes.length > 0 && (
                        <ActionButton
                          primary
                          onClick={() => apply((prev) => {
                            let courses = prev.courses;
                            for (const c of takingCodes) courses = setStatus(courses, c, 'done');
                            return { ...prev, courses };
                          })}
                        >
                          Mark all Done
                        </ActionButton>
                      )}
                      {plannedCodes.length > 0 && (
                        <ActionButton
                          onClick={() => apply((prev) => {
                            let courses = prev.courses;
                            for (const c of plannedCodes) courses = setStatus(courses, c, 'taking');
                            return { ...prev, courses };
                          })}
                        >
                          Move Planned to Taking
                        </ActionButton>
                      )}
                      <ActionButton onClick={() => setRolloverDismissed(true)}>Not yet</ActionButton>
                    </div>
                  }
                >
                  It&apos;s a new term.
                  {takingCodes.length > 0 && <> Did you finish {takingCodes.join(', ')}?</>}
                  {plannedCodes.length > 0 && <> Are you now taking {plannedCodes.join(', ')}?</>}
                </Notice>
              )}

              {/* Am I on track? */}
              <Card>
                <p className="text-sm font-medium text-text-primary">
                  {summary.complete} of {summary.items.length} requirements complete
                  {summary.inProgress > 0 && <> &middot; {summary.inProgress} in progress</>}
                </p>
                <div className="mt-2 flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-2xl font-bold text-text-primary">
                    {ev.doneUnits + ev.takingUnits}
                    <span className="text-base font-medium text-text-secondary"> of {program.total_units} units</span>
                  </p>
                  <p className="text-sm text-text-secondary" aria-live="polite">
                    {ev.doneUnits} done &middot; {ev.takingUnits} taking &middot;{' '}
                    {Math.max(0, program.total_units - ev.doneUnits - ev.takingUnits)} to go
                  </p>
                </div>
                <div className="mt-2">
                  <UnitsBar
                    done={ev.doneUnits}
                    taking={ev.takingUnits}
                    planned={projected ? projected.doneUnits + projected.takingUnits - ev.doneUnits - ev.takingUnits : 0}
                    total={program.total_units}
                  />
                </div>
                {projected && (
                  <p className="mt-1 text-xs text-text-secondary">
                    With your plan: {projected.doneUnits + projected.takingUnits} of {program.total_units} units
                  </p>
                )}
                <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Requirements">
                  {summary.items.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => scrollToRequirement(item.id)}
                        className="flex min-h-11 sm:min-h-0 items-center gap-1.5 rounded-full border border-border-color px-2.5 py-1 text-xs text-text-primary hover:bg-bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
                      >
                        <StateIcon state={item.state} size={13} />
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>

              {/* Asked choices; the specialization is read from the courses */}
              {program.choices.some((c) => !c.infer) && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {program.choices.filter((c) => !c.infer).map((c) => (
                    <div key={c.id}>
                      <label htmlFor={`dp-choice-${c.id}`} className={labelClass}>{c.label}</label>
                      <select
                        id={`dp-choice-${c.id}`}
                        value={choices[c.id] ?? ''}
                        onChange={(e) => choose(c.id, e.target.value)}
                        className={selectClass}
                      >
                        <option value="">Not decided yet</option>
                        {c.options.map((o) => (
                          <option key={o.id} value={o.id}>{o.label}</option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
              )}

              <PlanCard
                program={program}
                plan={plan}
                ev={ev}
                term={term}
                load={report.load}
                onLoad={(n) => apply((prev) => ({ ...prev, load: n }))}
                marks={report.courses}
                onMark={mark}
              />

              <CourseSearch program={program} marks={report.courses} onPick={onPick} />

              {/* Requirements */}
              <div className="space-y-4">
                <h3 className="text-lg font-bold text-text-primary">Requirements</h3>
                <p className="-mt-2 text-sm text-text-secondary">
                  Mark finished courses Done, this term&apos;s Taking, and next term&apos;s Planned. Changes save on their own.
                </p>
                {groups.map((g, i) => program.merge_groups?.includes(g.title) ? (
                  <MergedGroupCard
                    key={g.title}
                    program={program}
                    title={g.title}
                    items={g.items}
                    marks={report.courses}
                    assignedTo={assignedTo}
                    onMark={mark}
                    choices={choices}
                  />
                ) : (
                  <div key={g.title ?? `g${i}`} className="space-y-3">
                    {g.title && <div className={labelClass}>{g.title}</div>}
                    {g.items.map((rp) => (
                      <RequirementCard
                        key={rp.requirement.id}
                        program={program}
                        rp={rp}
                        marks={report.courses}
                        assignedTo={assignedTo}
                        onMark={mark}
                        choiceLabel={program.choices.find((c) => c.id === rp.requirement.choice)?.label}
                      />
                    ))}
                  </div>
                ))}
              </div>

              {uncountedListed.length > 0 && (
                <Notice>
                  Marked but not counted toward a requirement (a limit is reached or the requirement is full):{' '}
                  {uncountedListed.map((u) => u.code).join(', ')}.
                </Notice>
              )}

              {/* Courses the rules don't list */}
              {others.length > 0 && (
                <Card>
                  <h3 className="font-semibold text-text-primary">Other courses</h3>
                  <p className="mt-0.5 text-xs text-text-secondary">
                    Added with the search above. They count only where the rules accept any course of that kind.
                  </p>
                  <ul className="mt-2 divide-y divide-border-color">
                    {others.map((code) => {
                      const owner = assignedTo.get(code)?.req;
                      const ownerLabel = owner && program.requirements.find((r) => r.id === owner)?.label;
                      const status = report.courses[code];
                      return (
                        <CourseRow
                          key={code}
                          code={code}
                          title={null}
                          status={status}
                          note={
                            status === 'planned'
                              ? 'Planned for next term'
                              : ownerLabel ? `Counts toward ${ownerLabel}` : "Doesn't count automatically; ask your advisor"
                          }
                          onChange={(s) => mark(code, s)}
                          onRemove={() => mark(code, null)}
                        />
                      );
                    })}
                  </ul>
                </Card>
              )}

              {program.notes?.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-xs text-text-secondary">
                  {program.notes.map((n) => <li key={n}>{n}</li>)}
                </ul>
              )}

              {/* Clear, with a confirm and an undo */}
              <div className="flex flex-wrap items-center gap-3 pt-2 pb-8 text-sm">
                {undoReport ? (
                  <span role="status" className="flex items-center gap-2 text-text-secondary">
                    Cleared.
                    <button type="button" onClick={doUndo} className={`font-medium text-text-primary ${linkButton}`}>Undo</button>
                  </span>
                ) : confirmClear ? (
                  <span className="flex flex-wrap items-center gap-2 text-text-secondary">
                    Clear every mark and choice?
                    <ActionButton onClick={doClear}>Clear</ActionButton>
                    <ActionButton onClick={() => setConfirmClear(false)}>Cancel</ActionButton>
                  </span>
                ) : (
                  !isEmptyReport(report) && (
                    <button
                      type="button"
                      onClick={() => setConfirmClear(true)}
                      className="flex min-h-11 sm:min-h-0 items-center gap-2 rounded-lg border border-border-color px-4 py-2 text-text-secondary hover:bg-bg-hover hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
                    >
                      <Trash2 size={15} aria-hidden="true" />
                      Clear all
                    </button>
                  )
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
