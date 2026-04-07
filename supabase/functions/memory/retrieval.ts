// ============================================================
// Memory Retrieval Pipeline
//
// On each new user message, this module composes the memory context
// that gets injected into the LLM prompt.
//
// Retrieval order:
// 1. Global memories (user-wide preferences/facts)
// 2. Project memories (if conversation belongs to a project)
// 3. Conversation memories (this chat's specific context)
// 4. Conversation summary
// 5. Project summary
// 6. Recent messages (working memory)
//
// Token budget: caps the composed prompt to avoid blowing up context.
// ============================================================

import type { SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import type { Memory, MemoryContext, ConversationSummary, ProjectSummary } from './types.ts';

interface RetrievalInput {
  userId: string;
  conversationId: string;
  projectId: string | null;
  recentMessageLimit?: number;
  maxMemoryChars?: number;
}

export async function retrieveMemoryContext(
  supabase: SupabaseClient,
  input: RetrievalInput,
): Promise<MemoryContext> {
  const { userId, conversationId, projectId } = input;
  const recentMessageLimit = input.recentMessageLimit ?? 10;

  // Run all queries in parallel
  const [memoriesResult, convSummary, projSummary, recentMessages] = await Promise.all([
    supabase.rpc('get_memory_context', {
      p_user_id: userId,
      p_conversation_id: conversationId,
      p_project_id: projectId,
      p_max_global: 20,
      p_max_project: 30,
      p_max_conversation: 20,
    }),

    supabase
      .from('conversation_summaries')
      .select('*')
      .eq('conversation_id', conversationId)
      .maybeSingle(),

    projectId
      ? supabase
          .from('project_summaries')
          .select('*')
          .eq('project_id', projectId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),

    supabase
      .from('messages')
      .select('role, content')
      .eq('conversation_id', conversationId)
      .order('created_at', { ascending: false })
      .limit(recentMessageLimit),
  ]);

  const allMemories: Memory[] = memoriesResult.data ?? [];

  return {
    global_memories: allMemories.filter(m => m.scope === 'global'),
    project_memories: allMemories.filter(m => m.scope === 'project'),
    conversation_memories: allMemories.filter(m => m.scope === 'conversation'),
    conversation_summary: convSummary.data as ConversationSummary | null,
    project_summary: projSummary.data as ProjectSummary | null,
    recent_messages: (recentMessages.data ?? []).reverse(),
  };
}

/**
 * Compose the memory context into a system prompt section.
 *
 * maxChars caps the total output to avoid blowing up the context window.
 * Default ~6000 chars ≈ ~1500 tokens — leaves plenty of room for the
 * actual system prompt, user message, and response.
 */
export function composeMemoryPrompt(ctx: MemoryContext, maxChars: number = 6000): string {
  const sections: string[] = [];
  let charBudget = maxChars;

  // Helper: add a section only if it fits in the budget
  const addSection = (text: string): boolean => {
    if (text.length > charBudget) return false;
    sections.push(text);
    charBudget -= text.length;
    return true;
  };

  // Priority order: project > global > conversation (project consistency is the primary goal)

  // 1. Project context (highest priority for project-scoped consistency)
  if (ctx.project_summary) {
    addSection(`## Project Overview\n${ctx.project_summary.summary}`);
  }
  if (ctx.project_memories.length > 0) {
    const text = '## Project Memory\n' +
      ctx.project_memories.map(m => `- [${m.category}] ${m.content}`).join('\n');
    addSection(text);
  }

  // 2. Global memories
  if (ctx.global_memories.length > 0) {
    const text = '## User Profile & Preferences\n' +
      ctx.global_memories.map(m => `- [${m.category}] ${m.content}`).join('\n');
    addSection(text);
  }

  // 3. Conversation context (lowest priority — most of this is in recent messages anyway)
  if (ctx.conversation_summary) {
    addSection(`## Conversation So Far\n${ctx.conversation_summary.summary}`);
  }
  if (ctx.conversation_memories.length > 0) {
    const text = '## Conversation Notes\n' +
      ctx.conversation_memories.map(m => `- [${m.category}] ${m.content}`).join('\n');
    addSection(text);
  }

  if (sections.length === 0) return '';
  return '# Memory Context\n\n' + sections.join('\n\n');
}
