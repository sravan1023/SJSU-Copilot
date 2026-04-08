-- Fix #4: Feedback Logging
-- Stores a row per assistant response capturing the behavior snapshot,
-- validator results, and the user's thumbs-up/down vote.

CREATE TABLE IF NOT EXISTS behavior_feedback_log (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  response_id       uuid        NOT NULL,
  user_id           uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  conversation_id   uuid        REFERENCES conversations(id) ON DELETE SET NULL,
  behavior_snapshot jsonb       NOT NULL DEFAULT '{}',
  validators_run    text[]      NOT NULL DEFAULT '{}',
  validators_passed boolean     NOT NULL DEFAULT true,
  repairs_applied   text[]      NOT NULL DEFAULT '{}',
  user_feedback     text        CHECK (user_feedback IN ('thumbs_up', 'thumbs_down')),
  model_used        text,
  created_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE behavior_feedback_log ENABLE ROW LEVEL SECURITY;

-- Users may only insert rows where user_id matches their own auth.uid()
CREATE POLICY "Users can insert own feedback logs"
  ON behavior_feedback_log
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Users may only read their own rows
CREATE POLICY "Users can read own feedback logs"
  ON behavior_feedback_log
  FOR SELECT
  USING (auth.uid() = user_id);

-- Users may only update their own rows (used to set user_feedback)
CREATE POLICY "Users can update own feedback logs"
  ON behavior_feedback_log
  FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_feedback_log_response
  ON behavior_feedback_log(response_id);

CREATE INDEX IF NOT EXISTS idx_feedback_log_user_conv
  ON behavior_feedback_log(user_id, conversation_id);
