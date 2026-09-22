import { internAlertsSupabase } from './supabase';
import type { DashboardPayload, PipelineRun, PipelineState, SnapshotJob } from '../types/pipeline';

const PIPELINE_KEY = 'intern_jobs_alert';

export async function fetchInternJobsDashboard(): Promise<DashboardPayload> {
  const [{ data: latestRun, error: latestRunError }, { data: state, error: stateError }] = await Promise.all([
    internAlertsSupabase
      .from('pipeline_runs')
      .select('*')
      .eq('pipeline_key', PIPELINE_KEY)
      .order('started_at', { ascending: false })
      .limit(1)
      .maybeSingle<PipelineRun>(),
    internAlertsSupabase
      .from('pipeline_state')
      .select('*')
      .eq('pipeline_key', PIPELINE_KEY)
      .maybeSingle<PipelineState>(),
  ]);

  if (latestRunError) {
    throw new Error(`Failed to fetch latest run: ${latestRunError.message}`);
  }

  if (stateError) {
    throw new Error(`Failed to fetch pipeline state: ${stateError.message}`);
  }

  if (!latestRun) {
    return {
      latestRun: null,
      jobs: [],
      state: state ?? null,
    };
  }

  const { data: jobs, error: jobsError } = await internAlertsSupabase
    .from('jobs_snapshot')
    .select('*')
    .eq('run_id', latestRun.id)
    .order('rank_position', { ascending: true })
    .limit(100)
    .returns<SnapshotJob[]>();

  if (jobsError) {
    throw new Error(`Failed to fetch snapshot jobs: ${jobsError.message}`);
  }

  return {
    latestRun,
    jobs: jobs ?? [],
    state: state ?? null,
  };
}
