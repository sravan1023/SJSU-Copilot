// @ts-ignore Deno Edge Functions support URL imports at runtime.
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.49.8';
import { fetchTopAirtableJobs } from './airtable.ts';
import type {
  AirtableJobRow,
  JobSnapshotInsert,
  PipelineConfig,
  PipelineExecutionResult,
  PipelineRunRow,
  PipelineStateRow,
} from './types.ts';

function requireEnv(name: string): string {
  const value = Deno.env.get(name);
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export function loadPipelineConfig(): PipelineConfig {
  const airtableApiToken = Deno.env.get('AIRTABLE_API_TOKEN') ?? undefined;
  const airtableBaseId = Deno.env.get('AIRTABLE_BASE_ID') ?? undefined;
  const airtableTable = Deno.env.get('AIRTABLE_TABLE') ?? undefined;
  const airtableView = Deno.env.get('AIRTABLE_VIEW') ?? undefined;

  return {
    pipelineKey: Deno.env.get('PIPELINE_KEY') ?? 'intern_jobs_alert',
    airtableSharedViewUrl: requireEnv('AIRTABLE_SHARED_VIEW_URL'),
    airtableApiToken,
    airtableBaseId,
    airtableTable,
    airtableView,
  };
}

function determineNewJobs(topRows: AirtableJobRow[], previousTopUrl: string | null): AirtableJobRow[] {
  if (topRows.length === 0) {
    return [];
  }

  if (!previousTopUrl) {
    return topRows;
  }

  const previousIndex = topRows.findIndex((row) => row.jobUrl === previousTopUrl);

  if (previousIndex === -1) {
    return topRows;
  }

  return topRows.slice(0, previousIndex);
}

function toSnapshotRows(runId: string, rows: AirtableJobRow[]): JobSnapshotInsert[] {
  return rows.map((row, index) => ({
    run_id: runId,
    rank_position: index + 1,
    job_url: row.jobUrl,
    company: row.company,
    title: row.title,
    source_record_id: row.sourceRecordId,
    raw_record: row.rawRecord,
  }));
}

export async function executeInternJobsPipeline(): Promise<PipelineExecutionResult> {
  const config = loadPipelineConfig();

  const supabaseUrl = requireEnv('SUPABASE_URL');
  const serviceRoleKey = requireEnv('SUPABASE_SERVICE_ROLE_KEY');

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  const runInsert = await supabase
    .from('pipeline_runs')
    .insert({
      pipeline_key: config.pipelineKey,
      status: 'running',
    })
    .select('*')
    .single();

  if (runInsert.error || !runInsert.data) {
    throw new Error(`Failed to insert pipeline run: ${runInsert.error?.message ?? 'unknown error'}`);
  }

  const runId = (runInsert.data as PipelineRunRow).id;

  let topRows: AirtableJobRow[] = [];
  let newRows: AirtableJobRow[] = [];
  let previousTopUrl: string | null = null;
  let emailSent = false;

  try {
    topRows = await fetchTopAirtableJobs({
      sharedViewUrl: config.airtableSharedViewUrl,
      apiToken: config.airtableApiToken,
      baseId: config.airtableBaseId,
      table: config.airtableTable,
      view: config.airtableView,
    });

    const stateQuery = await supabase
      .from('pipeline_state')
      .select('pipeline_key, previous_top_url')
      .eq('pipeline_key', config.pipelineKey)
      .maybeSingle();

    if (stateQuery.error) {
      throw new Error(`Failed to load pipeline state: ${stateQuery.error.message}`);
    }

    previousTopUrl = (stateQuery.data as PipelineStateRow | null)?.previous_top_url ?? null;

    newRows = determineNewJobs(topRows, previousTopUrl);

    if (topRows.length > 0) {
      const snapshotRows = toSnapshotRows(runId, topRows);
      const snapshotInsert = await supabase.from('jobs_snapshot').insert(snapshotRows);
      if (snapshotInsert.error) {
        throw new Error(`Failed to insert jobs snapshot: ${snapshotInsert.error.message}`);
      }
    }

    if (newRows.length > 0) {
      const markerUpdate = await supabase.rpc('update_pipeline_marker_if_expected', {
        p_pipeline_key: config.pipelineKey,
        p_expected_previous_top_url: previousTopUrl,
        p_new_previous_top_url: newRows[0].jobUrl,
        p_last_run_id: runId,
      });

      if (markerUpdate.error) {
        throw new Error(`Failed to update pipeline marker: ${markerUpdate.error.message}`);
      }

      if (!markerUpdate.data) {
        throw new Error('Pipeline marker update rejected due to stale previous_top_url (concurrent run).');
      }
    }

    const runUpdate = await supabase
      .from('pipeline_runs')
      .update({
        status: 'success',
        top_count: topRows.length,
        new_jobs_count: newRows.length,
        email_sent: emailSent,
        error_message: null,
        finished_at: new Date().toISOString(),
      })
      .eq('id', runId);

    if (runUpdate.error) {
      throw new Error(`Failed to finalize pipeline run: ${runUpdate.error.message}`);
    }

    return {
      runId,
      topCount: topRows.length,
      newJobsCount: newRows.length,
      emailSent,
      previousTopUrl,
      nextTopUrl: newRows.length > 0 ? newRows[0].jobUrl : previousTopUrl,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown pipeline error';

    await supabase
      .from('pipeline_runs')
      .update({
        status: 'failed',
        top_count: topRows.length,
        new_jobs_count: newRows.length,
        email_sent: emailSent,
        error_message: message,
        finished_at: new Date().toISOString(),
      })
      .eq('id', runId);

    throw error;
  }
}
