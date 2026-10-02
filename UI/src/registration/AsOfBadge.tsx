import { AlertTriangle, ExternalLink } from 'lucide-react';
import type { Snapshot } from './lib/api';

// A bare 'YYYY-MM-DD' (page_last_updated) is a calendar day, not an instant.
// new Date() reads it as UTC midnight, which is the previous evening in San Jose,
// so it is formatted in UTC to keep the day SJSU printed. A full timestamp
// (verified_at) is an instant and is shown in Pacific time like the deadlines.
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

function fmtDay(iso: string | null): string {
  if (!iso) return 'unknown';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    ...(DATE_ONLY.test(iso)
      ? { timeZone: 'UTC' }
      : { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }),
  });
}

/** Where a set of dates came from and how fresh it is. One badge per source. */
export default function AsOfBadge({ title, snapshot }: { title: string; snapshot: Snapshot | null }) {
  if (!snapshot) return null;
  return (
    <div
      className={`rounded-md border p-3 text-xs ${
        snapshot.stale
          ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200'
          : 'border-border-color bg-bg-surface text-text-secondary'
      }`}
    >
      <div className="font-semibold text-text-primary">{title}</div>
      <div className="mt-1 break-words">
        SJSU page last updated: {snapshot.page_last_updated ? fmtDay(snapshot.page_last_updated) : 'not stated'}
        <span aria-hidden="true"> · </span>
        We last checked: {fmtDay(snapshot.verified_at)}
      </div>
      {snapshot.stale && (
        <div className="mt-1 flex items-start gap-1.5 font-medium" role="alert">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>This may be out of date. Check SJSU&apos;s source page.</span>
        </div>
      )}
      {snapshot.source_url && (
        <a
          href={snapshot.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-flex items-center gap-1 underline hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-sjsu-gold"
        >
          View SJSU&apos;s page <ExternalLink size={12} aria-hidden="true" />
        </a>
      )}
    </div>
  );
}
