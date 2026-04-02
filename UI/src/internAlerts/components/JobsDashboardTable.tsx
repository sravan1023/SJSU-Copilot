import type { SnapshotJob } from '../types/pipeline';

interface JobsDashboardTableProps {
  jobs: SnapshotJob[];
}

export default function JobsDashboardTable({ jobs }: JobsDashboardTableProps) {
  if (jobs.length === 0) {
    return <div className="rounded-xl border border-border-color bg-bg-surface p-4 text-sm text-text-secondary">No jobs in the latest snapshot.</div>;
  }

  return (
    <div className="overflow-auto rounded-xl border border-border-color bg-bg-surface">
      <table className="min-w-full table-fixed text-sm text-text-primary">
        <colgroup>
          <col className="w-20" />
          <col className="w-64" />
          <col />
          <col className="w-28" />
        </colgroup>
        <thead className="bg-bg-hover text-[11px] uppercase tracking-wide text-text-secondary">
          <tr>
            <th className="px-4 py-2.5">Rank</th>
            <th className="px-4 py-2.5">Company</th>
            <th className="px-4 py-2.5">Title</th>
            <th className="px-4 py-2.5">Link</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="border-t border-border-color">
              <td className="px-4 py-2.5 align-top text-text-secondary">{job.rank_position}</td>
              <td className="px-4 py-2.5 align-top font-medium">{job.company}</td>
              <td className="px-4 py-2.5 align-top leading-6">{job.title}</td>
              <td className="px-4 py-2.5 align-top">
                <a
                  href={job.job_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex text-sjsu-gold underline decoration-sjsu-gold/60 underline-offset-2 hover:text-sjsu-gold-hover"
                >
                  Open job
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
