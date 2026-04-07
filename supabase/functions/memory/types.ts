// ============================================================
// Memory System Types
// ============================================================

export type MemoryScope = 'global' | 'project' | 'conversation';
export type MemoryStatus = 'active' | 'superseded' | 'archived';
export type MemoryCategory =
  | 'preference'
  | 'decision'
  | 'constraint'
  | 'fact'
  | 'task'
  | 'context';

export interface Memory {
  id: string;
  user_id: string;
  scope: MemoryScope;
  project_id: string | null;
  conversation_id: string | null;
  content: string;
  category: MemoryCategory;
  confidence: number;
  importance: number;
  status: MemoryStatus;
  superseded_by: string | null;
  source_conversation_id: string | null;
  source_message_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CandidateMemory {
  content: string;
  scope: MemoryScope;
  category: MemoryCategory;
  confidence: number;
  importance: number;
  supersedes_id: string | null;
}

export interface ConversationSummary {
  id: string;
  conversation_id: string;
  summary: string;
  message_count: number;
  last_summarized_at: string;
}

export interface ProjectSummary {
  id: string;
  project_id: string;
  summary: string;
  memory_count: number;
  last_summarized_at: string;
}

export interface MemoryContext {
  global_memories: Memory[];
  project_memories: Memory[];
  conversation_memories: Memory[];
  conversation_summary: ConversationSummary | null;
  project_summary: ProjectSummary | null;
  recent_messages: Array<{ role: string; content: string }>;
}

export interface ExtractionResult {
  candidates: CandidateMemory[];
  conversation_summary_update: string | null;
  project_summary_update: string | null;
}

/**
 * Passed between retrieveAndCompose and processExchange
 * so we don't re-query the same data twice.
 */
export interface ConversationMeta {
  projectId: string | null;
  projectName: string | null;
}
