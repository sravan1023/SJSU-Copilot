// ============================================================
// Memory Extraction & Classification
//
// After each user-assistant exchange, this module:
// 1. Gates on message quality — skips trivial exchanges
// 2. Sends the exchange to the LLM with a structured extraction prompt
// 3. Includes existing memories so the LLM avoids re-extracting known facts
// 4. Parses the LLM response into candidate memories
// ============================================================

import type { CandidateMemory, ExtractionResult, Memory } from './types.ts';

const EXTRACTION_SYSTEM_PROMPT = `You are a memory extraction system. Given a user-assistant exchange and the memories already stored, extract NEW structured memories worth saving.

Rules:
- Extract ONLY stable, reusable information: preferences, decisions, constraints, facts, tasks, context.
- Do NOT extract casual greetings, filler, or one-off throwaway statements.
- Do NOT extract information that is only relevant to the immediate turn.
- Do NOT re-extract information that is already in the existing memories list.
- If an existing memory should be updated or contradicted, include the new version and set "supersedes_id" to the old memory's ID.
- Each memory should be a single, self-contained statement.
- Classify each memory into exactly one scope and one category.

Scopes:
- "global": user-wide preferences/facts that apply everywhere (e.g., "prefers dark mode", "is a senior engineer")
- "project": decisions/constraints/context specific to the current project (e.g., "use Supabase for backend", "no RAG yet")
- "conversation": details only relevant to this specific chat thread

Categories:
- "preference": user likes/dislikes, style choices
- "decision": explicit choices made
- "constraint": limits, rules, requirements
- "fact": stable truths about user or domain
- "task": ongoing work items, goals
- "context": background info

Respond with JSON only. Format:
{
  "candidates": [
    {
      "content": "the memory statement",
      "scope": "global|project|conversation",
      "category": "preference|decision|constraint|fact|task|context",
      "confidence": 0.0-1.0,
      "importance": 1-10,
      "supersedes_id": null or "uuid-of-old-memory-this-replaces"
    }
  ],
  "conversation_summary_update": "updated summary of this conversation so far, or null if no update needed",
  "project_summary_update": "updated summary of the project state, or null if no update needed"
}

If nothing new worth saving, return {"candidates": [], "conversation_summary_update": null, "project_summary_update": null}.`;

interface ExtractionInput {
  userMessage: string;
  assistantMessage: string;
  existingConversationSummary: string | null;
  existingProjectSummary: string | null;
  existingMemories: Memory[];
  projectName: string | null;
}

/**
 * Gate: should we even bother running extraction on this exchange?
 * Returns false for trivial messages that won't contain memorable info.
 */
export function shouldExtract(userMessage: string, assistantMessage: string): boolean {
  const userLen = userMessage.trim().length;
  // Very short user messages with no substance
  if (userLen < 8) {
    const trivial = /^(ok|okay|thanks|thank you|got it|sure|yes|no|yep|nope|cool|nice|great|k|ty|thx|lol|haha)\.?!?$/i;
    if (trivial.test(userMessage.trim())) return false;
  }
  // If the combined exchange is extremely short, skip
  if (userLen + assistantMessage.trim().length < 30) return false;
  return true;
}

export async function extractMemories(
  input: ExtractionInput,
  llmCall: (systemPrompt: string, userPrompt: string) => Promise<string>,
): Promise<ExtractionResult> {
  const userPrompt = buildExtractionPrompt(input);
  const raw = await llmCall(EXTRACTION_SYSTEM_PROMPT, userPrompt);

  try {
    const parsed = JSON.parse(raw);
    return {
      candidates: validateCandidates(parsed.candidates ?? []),
      conversation_summary_update: parsed.conversation_summary_update ?? null,
      project_summary_update: parsed.project_summary_update ?? null,
    };
  } catch {
    console.error('Memory extraction: failed to parse LLM response', raw);
    return { candidates: [], conversation_summary_update: null, project_summary_update: null };
  }
}

function buildExtractionPrompt(input: ExtractionInput): string {
  const parts: string[] = [];

  if (input.projectName) {
    parts.push(`[Project: ${input.projectName}]`);
  }
  if (input.existingConversationSummary) {
    parts.push(`[Conversation so far: ${input.existingConversationSummary}]`);
  }
  if (input.existingProjectSummary) {
    parts.push(`[Project state: ${input.existingProjectSummary}]`);
  }

  // Feed existing memories so the LLM can avoid re-extraction and identify supersessions
  if (input.existingMemories.length > 0) {
    const memList = input.existingMemories
      .map(m => `  - id:${m.id} [${m.scope}/${m.category}] ${m.content}`)
      .join('\n');
    parts.push(`[Already stored memories:\n${memList}\n]`);
  }

  parts.push(`User: ${input.userMessage}`);
  parts.push(`Assistant: ${input.assistantMessage}`);

  return parts.join('\n\n');
}

function validateCandidates(raw: unknown[]): CandidateMemory[] {
  const validScopes = ['global', 'project', 'conversation'];
  const validCategories = ['preference', 'decision', 'constraint', 'fact', 'task', 'context'];

  return raw
    .filter((c): c is Record<string, unknown> => typeof c === 'object' && c !== null)
    .filter(c =>
      typeof c.content === 'string' &&
      c.content.length > 0 &&
      validScopes.includes(c.scope as string) &&
      validCategories.includes(c.category as string),
    )
    .map(c => ({
      content: c.content as string,
      scope: c.scope as CandidateMemory['scope'],
      category: c.category as CandidateMemory['category'],
      confidence: clamp(Number(c.confidence) || 0.8, 0, 1),
      importance: clamp(Math.round(Number(c.importance) || 5), 1, 10),
      supersedes_id: typeof c.supersedes_id === 'string' ? c.supersedes_id : null,
    }));
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
