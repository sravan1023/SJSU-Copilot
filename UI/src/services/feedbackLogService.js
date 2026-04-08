import { supabase } from '../supabaseClient.js';

/**
 * Insert a new feedback-log row immediately after an assistant response is persisted.
 * All network errors are meant to be silently swallowed by the caller (.catch(() => {})).
 *
 * @param {Object} opts
 * @param {string} opts.responseId       - UUID of the saved assistant message (messages.id)
 * @param {string} opts.userId           - auth.uid() of the current user
 * @param {string|null} opts.conversationId
 * @param {Object|null} opts.behaviorSnapshot  - effective behavior settings at time of generation
 * @param {string[]} opts.validatorsRun  - rule names that were evaluated
 * @param {boolean} opts.validatorsPassed - true if no violations triggered
 * @param {string[]} opts.repairsApplied - rule names whose repairs were applied
 * @param {string|null} opts.modelUsed   - model key ('8b' | '70b')
 */
export async function insertFeedbackLog({
  responseId,
  userId,
  conversationId,
  behaviorSnapshot,
  validatorsRun,
  validatorsPassed,
  repairsApplied,
  modelUsed,
}) {
  const { error } = await supabase.from('behavior_feedback_log').insert({
    response_id:       responseId,
    user_id:           userId,
    conversation_id:   conversationId ?? null,
    behavior_snapshot: behaviorSnapshot ?? {},
    validators_run:    validatorsRun   ?? [],
    validators_passed: validatorsPassed ?? true,
    repairs_applied:   repairsApplied  ?? [],
    model_used:        modelUsed       ?? null,
  });
  if (error) throw error;
}

/**
 * Set (or clear) the thumbs vote on an existing feedback-log row.
 * Called whenever the user clicks the thumbs-up or thumbs-down button.
 *
 * @param {string} responseId - UUID of the message (= feedback_log.response_id)
 * @param {string} userId     - auth.uid()
 * @param {'up'|'down'|null} type  - null means the vote was toggled off
 */
export async function updateFeedbackVote(responseId, userId, type) {
  const feedback =
    type === 'up'   ? 'thumbs_up'   :
    type === 'down' ? 'thumbs_down' : null;

  const { error } = await supabase
    .from('behavior_feedback_log')
    .update({ user_feedback: feedback })
    .eq('response_id', responseId)
    .eq('user_id',     userId);
  if (error) throw error;
}
