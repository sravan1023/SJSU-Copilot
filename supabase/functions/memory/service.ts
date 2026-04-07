// ============================================================
// Memory Service — Main Orchestrator
//
// Request lifecycle:
//   1. User sends message
//   2. retrieveAndCompose() → loads memory context, builds prompt, returns meta
//   3. LLM generates response (caller handles this)
//   4. processExchange() → extracts, upserts memories (pass meta from step 2)
//
// The meta object avoids redundant DB queries between read and write paths.
// ============================================================

import type { SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import type { MemoryContext, ConversationMeta } from './types.ts';
import { retrieveMemoryContext, composeMemoryPrompt } from './retrieval.ts';
import { extractMemories, shouldExtract } from './extraction.ts';
import { upsertMemories } from './upsert.ts';

type LlmCallFn = (systemPrompt: string, userPrompt: string) => Promise<string>;

export class MemoryService {
  constructor(
    private supabase: SupabaseClient,
    private llmCall: LlmCallFn,
  ) {}

  // ------------------------------------------------------------------
  // READ PATH: Called before generating a response
  // ------------------------------------------------------------------

  async retrieveAndCompose(
    userId: string,
    conversationId: string,
  ): Promise<{ memoryPrompt: string; context: MemoryContext; meta: ConversationMeta }> {
    // Look up project info once — return it so processExchange doesn't re-query
    const meta = await this.getConversationMeta(conversationId);

    const context = await retrieveMemoryContext(this.supabase, {
      userId,
      conversationId,
      projectId: meta.projectId,
    });

    const memoryPrompt = composeMemoryPrompt(context);
    return { memoryPrompt, context, meta };
  }

  // ------------------------------------------------------------------
  // WRITE PATH: Called after the assistant responds
  // ------------------------------------------------------------------

  /**
   * Process a user-assistant exchange.
   *
   * Pass the `meta` and `context` from retrieveAndCompose to avoid
   * redundant DB queries. If not available, they'll be fetched.
   */
  async processExchange(
    userId: string,
    conversationId: string,
    messageId: string,
    userMessage: string,
    assistantMessage: string,
    meta?: ConversationMeta,
    context?: MemoryContext,
  ): Promise<void> {
    // Gate: skip trivial exchanges
    if (!shouldExtract(userMessage, assistantMessage)) {
      return;
    }

    // Reuse meta from read path, or fetch if not provided
    const { projectId, projectName } = meta ?? await this.getConversationMeta(conversationId);

    // Reuse context from read path to get existing memories + summaries
    // This avoids re-querying what we already fetched
    const existingContext = context ?? await retrieveMemoryContext(this.supabase, {
      userId,
      conversationId,
      projectId,
    });

    // All existing active memories (extraction needs these to avoid re-extraction)
    const existingMemories = [
      ...existingContext.global_memories,
      ...existingContext.project_memories,
      ...existingContext.conversation_memories,
    ];

    // Single LLM call: extract + classify + identify supersessions
    const extractionResult = await extractMemories(
      {
        userMessage,
        assistantMessage,
        existingConversationSummary: existingContext.conversation_summary?.summary ?? null,
        existingProjectSummary: existingContext.project_summary?.summary ?? null,
        existingMemories,
        projectName,
      },
      this.llmCall,
    );

    // Upsert memories and summaries (no second LLM call needed)
    await upsertMemories(this.supabase, {
      userId,
      conversationId,
      projectId,
      messageId,
      extractionResult,
    });
  }

  // ------------------------------------------------------------------
  // PROJECT MANAGEMENT
  // ------------------------------------------------------------------

  /**
   * Assign a standalone conversation to a project.
   * Optionally promote high-importance conversation memories to project scope.
   */
  async assignConversationToProject(
    conversationId: string,
    projectId: string,
    promoteMinImportance: number = 7,
  ): Promise<void> {
    // Link conversation to project
    await this.supabase
      .from('conversations')
      .update({ project_id: projectId })
      .eq('id', conversationId);

    // Find high-importance conversation memories worth promoting
    const { data: candidates } = await this.supabase
      .from('memories')
      .select('id')
      .eq('conversation_id', conversationId)
      .eq('scope', 'conversation')
      .eq('status', 'active')
      .gte('importance', promoteMinImportance);

    if (candidates && candidates.length > 0) {
      // Promote each via the DB function (handles supersession atomically)
      await Promise.all(
        candidates.map(m =>
          this.supabase.rpc('promote_memory_to_project', {
            p_memory_id: m.id,
            p_project_id: projectId,
          }),
        ),
      );
    }
  }

  /**
   * Manually promote a single conversation memory to project scope.
   */
  async promoteMemory(memoryId: string, projectId: string): Promise<string | null> {
    const { data } = await this.supabase.rpc('promote_memory_to_project', {
      p_memory_id: memoryId,
      p_project_id: projectId,
    });
    return data as string | null;
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  private async getConversationMeta(conversationId: string): Promise<ConversationMeta> {
    const { data } = await this.supabase
      .from('conversations')
      .select('project_id, projects(name)')
      .eq('id', conversationId)
      .single();

    const projectId = data?.project_id ?? null;
    const projectName = (data?.projects as { name: string } | null)?.name ?? null;
    return { projectId, projectName };
  }
}
