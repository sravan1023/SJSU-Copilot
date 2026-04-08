import { supabase } from '../supabaseClient';

const COLUMNS = 'response_tone, response_length, response_format, emoji_usage, priority_stack, project_id, conversation_id';

export const DEFAULT_PRIORITY_STACK = [
  'safety',
  'accuracy',
  'task_completion',
  'clarity',
  'speed',
  'warmth',
];

/** Default behavior settings (matches DB defaults). Used as the final fallback. */
export const DEFAULT_BEHAVIOR = {
  response_tone: 'friendly',
  response_length: 'balanced',
  response_format: 'markdown',
  emoji_usage: 'occasional',
  priority_stack: DEFAULT_PRIORITY_STACK,
};

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Merge behavior settings from least-specific (base) to most-specific (override).
 * Only non-null fields from override replace fields in base.
 */
function mergeSettings(base, override) {
  if (!override) return base;
  const merged = { ...base };
  const fields = ['response_tone', 'response_length', 'response_format', 'emoji_usage', 'priority_stack'];
  for (const field of fields) {
    if (override[field] != null) merged[field] = override[field];
  }
  return merged;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Fetch the global (user-level) behavior settings.
 * Creates a default row if none exists.
 */
export async function fetchBehaviorSettings(userId) {
  const { data, error } = await supabase
    .from('behavior_settings')
    .select(COLUMNS)
    .eq('user_id', userId)
    .is('project_id', null)
    .is('conversation_id', null)
    .single();

  if (error && error.code === 'PGRST116') {
    const { data: inserted, error: insertErr } = await supabase
      .from('behavior_settings')
      .insert({ user_id: userId, project_id: null, conversation_id: null })
      .select(COLUMNS)
      .single();
    if (insertErr) throw insertErr;
    return inserted;
  }
  if (error) throw error;
  return data;
}

/**
 * Resolve the effective behavior for a given scope.
 *
 * Scope chain (highest specificity wins, missing fields fall through):
 *   conversation-level → project-level → user-level → DEFAULT_BEHAVIOR
 *
 * @param {string} userId
 * @param {string|null} projectId
 * @param {string|null} conversationId
 * @returns {Promise<Object>} merged behavior settings object
 */
export async function resolveEffectiveBehavior(userId, projectId, conversationId) {
  // Fetch all rows for this user matching any of the relevant scopes
  const { data, error } = await supabase
    .from('behavior_settings')
    .select(COLUMNS)
    .eq('user_id', userId);

  if (error) throw error;

  const rows = data || [];

  const userRow = rows.find(r => r.project_id == null && r.conversation_id == null);
  const projectRow = projectId ? rows.find(r => r.project_id === projectId && r.conversation_id == null) : null;
  const convoRow = conversationId ? rows.find(r => r.conversation_id === conversationId) : null;

  // Merge from least to most specific
  let resolved = { ...DEFAULT_BEHAVIOR };
  if (userRow) resolved = mergeSettings(resolved, userRow);
  if (projectRow) resolved = mergeSettings(resolved, projectRow);
  if (convoRow) resolved = mergeSettings(resolved, convoRow);

  return resolved;
}

/**
 * Update global (user-level) behavior settings.
 */
export async function updateBehaviorSettings(userId, updates) {
  const { data, error } = await supabase
    .from('behavior_settings')
    .update({ ...updates, updated_at: new Date().toISOString() })
    .eq('user_id', userId)
    .is('project_id', null)
    .is('conversation_id', null)
    .select(COLUMNS)
    .single();
  if (error) throw error;
  return data;
}

/**
 * Upsert behavior settings for a specific scope (project or conversation).
 * Pass projectId OR conversationId (not both) to set that scope's override.
 * Pass neither to update the global user-level settings (same as updateBehaviorSettings).
 */
export async function upsertScopedBehavior(userId, updates, { projectId = null, conversationId = null } = {}) {
  const row = {
    user_id: userId,
    project_id: projectId,
    conversation_id: conversationId,
    ...updates,
    updated_at: new Date().toISOString(),
  };

  const { data, error } = await supabase
    .from('behavior_settings')
    .upsert(row, { onConflict: 'user_id,project_id,conversation_id' })
    .select(COLUMNS)
    .single();
  if (error) throw error;
  return data;
}

/**
 * Delete a scoped behavior override (project or conversation level).
 * After deletion the scope will fall back to its parent.
 */
export async function deleteScopedBehavior(userId, { projectId = null, conversationId = null } = {}) {
  let query = supabase
    .from('behavior_settings')
    .delete()
    .eq('user_id', userId);

  if (projectId) query = query.eq('project_id', projectId);
  else query = query.is('project_id', null);

  if (conversationId) query = query.eq('conversation_id', conversationId);
  else query = query.is('conversation_id', null);

  const { error } = await query;
  if (error) throw error;
}
