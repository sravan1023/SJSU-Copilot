import { useCallback, useRef, useState } from 'react';
import type { SnapshotJob } from '../types/pipeline';

interface JobsDashboardTableProps {
  jobs: SnapshotJob[];
}

const DEFAULT_WIDTHS = [52, 155, 400, 195, 95, 72];

export default function JobsDashboardTable({ jobs }: JobsDashboardTableProps) {
  const [colWidths, setColWidths] = useState<number[]>(DEFAULT_WIDTHS);
  const dragRef = useRef<{ colIdx: number; startX: number; startW: number } | null>(null);

  const onPointerDown = useCallback(
    (e: React.PointerEvent, colIdx: number) => {
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = { colIdx, startX: e.clientX, startW: colWidths[colIdx] };
    },
    [colWidths],
  );

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const { colIdx, startX, startW } = dragRef.current;
    const newW = Math.max(40, startW + (e.clientX - startX));
    setColWidths((prev) => {
      const next = [...prev];
      next[colIdx] = newW;
      return next;
    });
  }, []);

  const onPointerUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  if (jobs.length === 0) {
    return <div className="rounded-xl border border-border-color bg-bg-surface p-4 text-sm text-text-secondary">No jobs in the latest snapshot.</div>;
  }

  const headers = ['#', 'Company', 'Title', 'Location', 'Pay', 'Link'];

  return (
    <div className="overflow-auto rounded-lg border border-border-color bg-bg-surface" onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
      <table className="text-[13px] text-text-primary" style={{ width: colWidths.reduce((a, b) => a + b, 0) }}>
        <thead className="sticky top-0 z-10 bg-bg-surface text-[11px] uppercase tracking-wider text-text-secondary">
          <tr className="border-b border-border-color">
            {headers.map((h, i) => (
              <th
                key={h}
                className="relative select-none px-3 py-2.5 text-left font-medium"
                style={{ width: colWidths[i], minWidth: 40 }}
              >
                {h}
                {i < headers.length - 1 && (
                  <span
                    className="absolute right-0 top-1/4 h-1/2 w-px bg-border-color cursor-col-resize hover:w-0.5 hover:bg-sjsu-gold/60"
                    onPointerDown={(e) => onPointerDown(e, i)}
                    style={{ padding: '0 2px', backgroundClip: 'content-box' }}
                  />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const location = (job.raw_record?.location as string) || '—';
            const salary = (job.raw_record?.salary as string) || '—';
            return (
              <tr key={job.id} className="border-b border-border-color/50 transition-colors hover:bg-bg-hover/60">
                <td className="whitespace-nowrap px-3 py-2 text-text-secondary tabular-nums">{job.rank_position}</td>
                <td className="whitespace-nowrap px-3 py-2 font-medium">{job.company}</td>
                <td className="whitespace-nowrap px-3 py-2">{job.title}</td>
                <td className="whitespace-nowrap px-3 py-2 text-text-secondary">{location}</td>
                <td className="whitespace-nowrap px-3 py-2 text-text-secondary">{salary}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <a
                    href={job.job_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sjsu-gold hover:text-sjsu-gold-hover"
                  >
                    Open ↗
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
