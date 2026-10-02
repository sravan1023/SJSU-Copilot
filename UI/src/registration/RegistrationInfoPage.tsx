import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import AsOfBadge from './AsOfBadge';
import {
  fetchDeadlines,
  fetchTerms,
  RegistrationApiError,
  type DeadlineEvent,
  type DeadlinesPayload,
  type TermsPayload,
} from './lib/api';

const REGISTRAR_URL = 'https://www.sjsu.edu/registrar/';

// Add future tabs (class search, final exam, payments) here.
const TABS = [{ id: 'deadlines', label: 'Deadlines' }] as const;
type TabId = (typeof TABS)[number]['id'];

type CategoryFilter = 'all' | 'registrar' | 'academic';
const FILTERS: { id: CategoryFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'registrar', label: 'Registration deadlines' },
  { id: 'academic', label: 'Academic calendar' },
];
const CATEGORY_TAG: Record<DeadlineEvent['category'], string> = {
  registrar: 'Registrar',
  academic: 'Academic calendar',
};

/** Parse YYYY-MM-DD as a calendar day, immune to the viewer's timezone. */
function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return d.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
  });
}

function fmtRange(start: string | null, end: string | null): string | null {
  if (!start) return null;
  if (!end || end === start) return fmtDate(start);
  return `${fmtDate(start)} – ${fmtDate(end)}`;
}

type Failure = { kind: 'unavailable' | 'rate' | 'network' | 'auth' | 'other' };

function classify(e: unknown): Failure {
  if (e instanceof RegistrationApiError) {
    if (e.status === 503) return { kind: 'unavailable' };
    if (e.status === 429) return { kind: 'rate' };
    if (e.status === 0) return { kind: 'network' };
    if (e.status === 401 || e.status === 403) return { kind: 'auth' };
  }
  return { kind: 'other' };
}

function RegistrarLink({ children }: { children: ReactNode }) {
  return (
    <a
      href={REGISTRAR_URL}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 underline focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
    >
      {children} <ExternalLink size={12} aria-hidden="true" />
    </a>
  );
}

function Notice({ tone, children }: { tone: 'info' | 'error'; children: ReactNode }) {
  const cls =
    tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300'
      : 'border-border-color bg-bg-surface text-text-primary';
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`rounded-md border p-4 text-sm ${cls}`}>
      {children}
    </div>
  );
}

function EventRow({ ev, next }: { ev: DeadlineEvent; next: boolean }) {
  const dates = fmtRange(ev.start_date, ev.end_date);
  return (
    <li
      className={`rounded-md border p-3 ${
        next ? 'border-sjsu-gold bg-bg-surface ring-1 ring-sjsu-gold' : 'border-border-color bg-bg-surface'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        {next && (
          <span className="rounded bg-sjsu-gold px-1.5 py-0.5 text-[11px] font-semibold text-black">Next up</span>
        )}
        <span className="rounded border border-border-color px-1.5 py-0.5 text-[11px] text-text-secondary">
          {CATEGORY_TAG[ev.category]}
        </span>
      </div>
      {/* label_raw is SJSU's wording; never rewrite it. */}
      <div className="mt-1 break-words text-sm font-medium text-text-primary">{ev.label_raw}</div>
      <div className="mt-1 break-words text-sm text-text-primary" title={`SJSU prints: ${ev.date_raw}`}>
        {dates ?? ev.date_raw}
      </div>
      {dates && <div className="break-words text-xs text-text-secondary">SJSU prints: {ev.date_raw}</div>}
    </li>
  );
}

function DeadlinesTab() {
  const [terms, setTerms] = useState<TermsPayload | null>(null);
  const [termsFailed, setTermsFailed] = useState(false);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [data, setData] = useState<DeadlinesPayload | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<CategoryFilter>('all');
  const [showPassed, setShowPassed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // The term list only feeds the selector; the page still works without it.
  useEffect(() => {
    const ac = new AbortController();
    setTermsFailed(false);
    fetchTerms(ac.signal).then((value) => {
      if (!ac.signal.aborted) setTerms(value);
    }).catch(() => {
      if (!ac.signal.aborted) setTermsFailed(true);
    });
    return () => ac.abort();
  }, [attempt]);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setFailure(null);
    fetchDeadlines(selected, ac.signal)
      .then((d) => {
        if (ac.signal.aborted) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (ac.signal.aborted) return;
        setFailure(classify(e));
        setLoading(false);
      });
    return () => ac.abort();
  }, [selected, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const events = useMemo(() => {
    const all = (data?.events ?? []).filter((e) => filter === 'all' || e.category === filter);
    return [...all].sort((a, b) => (a.start_date ?? '9999').localeCompare(b.start_date ?? '9999'));
  }, [data, filter]);
  const upcoming = events.filter((e) => !e.passed);
  const passed = events.filter((e) => e.passed);
  const nextEvent = upcoming.find((e) => e.start_date) ?? null;

  const currentTerm = selected ?? data?.term ?? '';
  // The API lists loaded terms only. Include this/next even before their
  // calendars are loaded, so the selector always describes the displayed data.
  const termOptions = useMemo(() => {
    const choices = new Map<string, { term: string; label: string; loaded: boolean }>();
    for (const t of terms?.terms ?? []) choices.set(t.term, { ...t, loaded: true });
    for (const t of [terms?.this, terms?.next]) {
      if (t?.term && t.label && !choices.has(t.term)) {
        choices.set(t.term, { term: t.term, label: t.label, loaded: t.loaded });
      }
    }
    if (data && !choices.has(data.term)) {
      choices.set(data.term, { term: data.term, label: data.label, loaded: data.status === 'ok' });
    }
    return [...choices.values()];
  }, [terms, data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        {termOptions.length > 0 && (
          <div className="min-w-0 max-w-full">
            <label htmlFor="reg-term" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-text-secondary">
              Term
            </label>
            <select
              id="reg-term"
              value={currentTerm}
              onChange={(e) => {
                setSelected(e.target.value);
                setShowPassed(false);
              }}
              className="max-w-full rounded-md border border-border-color bg-bg-surface px-3 py-1.5 text-sm text-text-primary hover:bg-bg-hover focus:outline-none focus:ring-1 focus:ring-sjsu-gold"
            >
              {!currentTerm && <option value="" disabled>Loading current term…</option>}
              {termOptions.map((t) => (
                <option key={t.term} value={t.term}>
                  {t.label}
                  {t.term === terms?.this.term ? ' (this semester)' : t.term === terms?.next.term ? ' (next)' : ''}
                  {!t.loaded ? ' — not loaded' : ''}
                </option>
              ))}
            </select>
          </div>
        )}
        <div role="group" aria-label="Filter deadlines" className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`rounded-full border px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold ${
                filter === f.id
                  ? 'border-sjsu-gold bg-sjsu-gold text-black'
                  : 'border-border-color bg-bg-surface text-text-primary hover:bg-bg-hover'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {termsFailed && !failure && !loading && (
        <Notice tone="info">
          The term list could not be loaded. Other terms may be missing.{' '}
          <button type="button" onClick={retry} className="underline focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold">
            Retry term list
          </button>
        </Notice>
      )}

      {data && !failure && !loading && <p className="text-xs text-text-secondary">{data.how}</p>}

      {loading && (
        <div role="status" className="text-sm text-text-secondary">
          Loading deadlines…
        </div>
      )}

      {!loading && failure && (
        <Notice tone="error">
          {failure.kind === 'unavailable' && (
            <>
              Registration info is temporarily unavailable. See <RegistrarLink>SJSU&apos;s registrar page</RegistrarLink>.
            </>
          )}
          {failure.kind === 'rate' && <>Too many requests, try again in a moment.</>}
          {failure.kind === 'network' && <>Could not reach the server. Check your connection and try again.</>}
          {failure.kind === 'auth' && <>Your session has expired. Reload the app to start a new session or sign in again.</>}
          {failure.kind === 'other' && <>Something went wrong loading deadlines.</>}
          <div className="mt-2">
            <button
              type="button"
              onClick={retry}
              className="rounded-md border border-border-color bg-bg-surface px-3 py-1.5 text-sm text-text-primary hover:bg-bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
            >
              Try again
            </button>
          </div>
        </Notice>
      )}

      {!loading && !failure && data?.status === 'not_loaded' && (
        <Notice tone="info">
          SJSU&apos;s calendar for {data.label} hasn&apos;t been loaded yet. See{' '}
          <RegistrarLink>SJSU&apos;s registrar page</RegistrarLink>.
        </Notice>
      )}

      {!loading && !failure && data?.status === 'ok' && (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <AsOfBadge title="Registrar calendar" snapshot={data.snapshot} />
            <AsOfBadge title="Academic calendar" snapshot={data.academic_snapshot} />
          </div>

          {!data.academic_snapshot && filter !== 'registrar' && (
            <Notice tone="info">The academic calendar has not been loaded for this term. Registration deadlines are shown where available.</Notice>
          )}

          {events.length === 0 && <Notice tone="info">No dates to show for this term and filter.</Notice>}

          {upcoming.length > 0 && (
            <ul className="space-y-2">
              {upcoming.map((ev, i) => (
                <EventRow key={`${ev.label_raw}-${i}`} ev={ev} next={ev === nextEvent} />
              ))}
            </ul>
          )}

          {passed.length > 0 && (
            <div>
              <button
                type="button"
                aria-expanded={showPassed}
                aria-controls="reg-passed-events"
                onClick={() => setShowPassed((v) => !v)}
                className="flex items-center gap-1 text-sm text-text-secondary hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
              >
                {showPassed ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                Earlier this term ({passed.length})
              </button>
              {showPassed && (
                <ul id="reg-passed-events" className="mt-2 space-y-2">
                  {passed.map((ev, i) => (
                    <EventRow key={`${ev.label_raw}-${i}`} ev={ev} next={false} />
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function RegistrationInfoPage({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<TabId>('deadlines');

  return (
    <section aria-labelledby="reg-heading" className="flex h-full min-h-0 min-w-0 w-full flex-col bg-bg-main text-text-primary transition-colors duration-300">
      <div className="border-b border-border-color px-4 py-3 md:px-6">
        <div className="flex items-center justify-between gap-3">
          <h2 id="reg-heading" className="text-xl font-semibold">Registration Info</h2>
          <button
            type="button"
            onClick={onBack}
            className="rounded-md border border-border-color bg-bg-surface px-3 py-2 text-sm text-text-primary transition-colors hover:bg-bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
          >
            Back
          </button>
        </div>
        {/* Only one tab exists today, so the tab strip stays hidden until a second arrives. */}
        {TABS.length > 1 && (
          <div role="tablist" aria-label="Registration info sections" className="mt-3 flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                type="button"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`rounded-md px-3 py-1.5 text-sm ${tab === t.id ? 'bg-bg-hover font-semibold' : 'hover:bg-bg-hover'}`}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-6">
        <div className="mx-auto w-full max-w-3xl">{tab === 'deadlines' && <DeadlinesTab />}</div>
      </div>

      <footer className="border-t border-border-color px-4 py-3 text-xs text-text-secondary md:px-6">
        Dates are copied from SJSU&apos;s published calendars. MySJSU is authoritative for your own enrollment.
      </footer>
    </section>
  );
}
