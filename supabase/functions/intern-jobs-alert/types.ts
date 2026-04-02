export type PipelineStatus = 'running' | 'success' | 'failed';

export interface AirtableJobRow {
  sourceRecordId: string;
  company: string;
  title: string;
  jobUrl: string;
  rawRecord: Record<string, unknown>;
}

export interface PipelineStateRow {
  pipeline_key: string;
  previous_top_url: string | null;
}

export interface PipelineRunRow {
  id: string;
  pipeline_key: string;
  status: PipelineStatus;
  top_count: number;
  new_jobs_count: number;
  email_sent: boolean;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface JobSnapshotInsert {
  run_id: string;
  rank_position: number;
  job_url: string;
  company: string;
  title: string;
  source_record_id: string;
  raw_record: Record<string, unknown>;
}

export interface PipelineConfig {
  pipelineKey: string;
  airtableSharedViewUrl: string;
  airtableApiToken?: string;
  airtableBaseId?: string;
  airtableTable?: string;
  airtableView?: string;
}

export interface PipelineExecutionResult {
  runId: string;
  topCount: number;
  newJobsCount: number;
  emailSent: boolean;
  previousTopUrl: string | null;
  nextTopUrl: string | null;
}
