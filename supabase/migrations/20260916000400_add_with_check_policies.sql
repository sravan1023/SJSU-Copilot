-- Security hardening: add WITH CHECK to four UPDATE policies.
--
-- A policy with USING but no WITH CHECK controls which rows a user may update,
-- but not what the row may become. On these four tables the ownership column is
-- an ordinary updatable column, so a user could update a row they own and set
-- user_id to somebody else's id -- moving their own row out of their scope and
-- into another user's. For memories that also means planting arbitrary content
-- into another user's memory context, which is fed to the model.
--
-- PostgreSQL applies WITH CHECK to the post-update row, so repeating the
-- ownership predicate there closes it.

-- 001_initial_schema.sql:114
drop policy if exists "Users can update own academic records" on public.student_academic_records;
create policy "Users can update own academic records"
  on public.student_academic_records for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- 001_initial_schema.sql:139
drop policy if exists "Users can update own conversations" on public.saved_conversations;
create policy "Users can update own conversations"
  on public.saved_conversations for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- 20260405_chat_schema.sql:43
drop policy if exists "users can update own conversations" on public.conversations;
create policy "users can update own conversations"
  on public.conversations for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- 20260406_memory_system.sql:141
drop policy if exists "users can update own memories" on public.memories;
create policy "users can update own memories"
  on public.memories for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
