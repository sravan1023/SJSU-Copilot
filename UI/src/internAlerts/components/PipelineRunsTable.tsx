import type { PipelineRun } from '../types/pipeline';
import { formatDateTime } from '../lib/format';

interface PipelineRunsTableProps {
  runs: PipelineRun[];
}

const STATUS_CLASS: Record<PipelineRun['status'], string> = {
  running: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300',
  success: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300',
  failed: 'bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300',
};

export default function PipelineRunsTable({ runs }: PipelineRunsTableProps) {
  if (runs.length === 0) {
    return <div className="rounded-xl border border-border-color bg-bg-surface p-4 text-sm text-text-secondary">No pipeline runs yet.</div>;
  }

  return (
    <div className="overflow-auto rounded-xl border border-border-color bg-bg-surface">
      <table className="min-w-full text-sm text-text-primary">
        <thead className="bg-bg-hover text-left text-xs uppercase tracking-wide text-text-secondary">
          <tr>
            <th className="px-4 py-3">Started</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Top Count</th>
            <th className="px-4 py-3">New Jobs</th>
            <th className="px-4 py-3">Email Sent</th>
            <th className="px-4 py-3">Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-t border-border-color">
              <td className="px-4 py-3 text-text-secondary">{formatDateTime(run.started_at)}</td>
              <td className="px-4 py-3">
                <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${STATUS_CLASS[run.status]}`}>
                  {run.status}
                </span>
              </td>
              <td className="px-4 py-3">{run.top_count}</td>
              <td className="px-4 py-3">{run.new_jobs_count}</td>
              <td className="px-4 py-3">{run.email_sent ? 'yes' : 'no'}</td>
              <td className="max-w-sm truncate px-4 py-3 text-red-600 dark:text-red-300">{run.error_message ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
