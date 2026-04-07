// ============================================================
// Memory Upsert & Conflict Resolution
//
// Now that extraction handles dedup (it sees existing memories),
// this module only needs to:
// 1. Batch insert new memories
// 2. Batch supersede old memories
// 3. Update summaries
//
// The separate LLM dedup call is eliminated — extraction does it.
// ============================================================

import type { SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import type { CandidateMemory, ExtractionResult } from './types.ts';

interface UpsertInput {
  userId: string;
  conversationId: string;
  projectId: string | null;
  messageId: string;
  extractionResult: ExtractionResult;
}

export async function upsertMemories(
  supabase: SupabaseClient,
  input: UpsertInput,
): Promise<void> {
  const { userId, conversationId, projectId, messageId, extractionResult } = input;
  const { candidates, conversation_summary_update, project_summary_update } = extractionResult;

  if (candidates.length === 0 && !conversation_summary_update && !project_summary_update) {
    return;
  }

  // Insert memories and handle supersessions
  if (candidates.length > 0) {
    await insertAndSupersede(supabase, candidates, {
      userId, conversationId, projectId, messageId,
    });
  }

  // Update summaries in parallel
  await Promise.all([
    conversation_summary_update
      ? upsertConversationSummary(supabase, conversationId, conversation_summary_update)
      : Promise.resolve(),
    project_summary_update && projectId
      ? upsertProjectSummary(supabase, projectId, project_summary_update)
      : Promise.resolve(),
  ]);
}

// ------------------------------------------------------------------
// Internal helpers
// ------------------------------------------------------------------

interface InsertContext {
  userId: string;
  conversationId: string;
  projectId: string | null;
  messageId: string;
}

async function insertAndSupersede(
  supabase: SupabaseClient,
  candidates: CandidateMemory[],
  ctx: InsertContext,
): Promise<void> {
  // Build insert rows.
  // If project-scoped but no project exists yet, downgrade to conversation scope so
  // the DB constraint (scope='project' requires project_id != null) is never violated.
  // These memories will be promoted when the conversation is assigned to a project.
  const rows = candidates.map(c => {
    const scope = (c.scope === 'project' && !ctx.projectId) ? 'conversation' : c.scope;
    return {
      user_id: ctx.userId,
      scope,
      project_id: scope === 'project' ? ctx.projectId : null,
      conversation_id: scope === 'conversation' ? ctx.conversationId : null,
      content: c.content,
      category: c.category,
      confidence: c.confidence,
      importance: c.importance,
      status: 'active' as const,
      source_conversation_id: ctx.conversationId,
      source_message_id: ctx.messageId,
    };
  });

  // Batch insert all memories at once
  const { data: inserted, error } = await supabase
    .from('memories')
    .insert(rows)
    .select('id, content');

  if (error) {
    console.error('Failed to batch insert memories:', error);
    return;
  }

  // Batch supersede old memories
  // Match inserted memories back to their supersedes_id via content
  const supersessions: Array<{ oldId: string; newId: string }> = [];
  for (const candidate of candidates) {
    if (!candidate.supersedes_id) continue;
    const match = inserted?.find(i => i.content === candidate.content);
    if (match) {
      supersessions.push({ oldId: candidate.supersedes_id, newId: match.id });
    }
  }

  if (supersessions.length > 0) {
    // Update status + superseded_by in parallel (one call per old memory).
    // Can't batch because each old memory points to a different new memory.
    // The updated_at trigger handles timestamps automatically.
    await Promise.all(
      supersessions.map(s =>
        supabase
          .from('memories')
          .update({ status: 'superseded' as const, superseded_by: s.newId })
          .eq('id', s.oldId),
      ),
    );
  }
}

async function upsertConversationSummary(
  supabase: SupabaseClient,
  conversationId: string,
  summary: string,
): Promise<void> {
  const { count } = await supabase
    .from('messages')
    .select('*', { count: 'exact', head: true })
    .eq('conversation_id', conversationId);

  const { error } = await supabase
    .from('conversation_summaries')
    .upsert(
      {
        conversation_id: conversationId,
        summary,
        message_count: count ?? 0,
        last_summarized_at: new Date().toISOString(),
      },
      { onConflict: 'conversation_id' },
    );

  if (error) console.error('Failed to upsert conversation summary:', error);
}

async function upsertProjectSummary(
  supabase: SupabaseClient,
  projectId: string,
  summary: string,
): Promise<void> {
  // When two project chats write concurrently, the upsert on unique project_id
  // means last-write-wins. The summary is regenerated from scratch each time
  // (not appended), so the "losing" write gets a slightly stale summary
  // that will be corrected on the next exchange. Acceptable for v1.
  const { count } = await supabase
    .from('memories')
    .select('*', { count: 'exact', head: true })
    .eq('project_id', projectId)
    .eq('status', 'active');

  const { error } = await supabase
    .from('project_summaries')
    .upsert(
      {
        project_id: projectId,
        summary,
        memory_count: count ?? 0,
        last_summarized_at: new Date().toISOString(),
      },
      { onConflict: 'project_id' },
    );

  if (error) console.error('Failed to upsert project summary:', error);
}
