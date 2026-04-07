-- Adds: projects, memory records, summaries, and project linkage

-- 1. Projects table
-- A project groups multiple conversations that share context.

create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  description text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_projects_user
on projects(user_id, updated_at desc);

-- 2. Link conversations to projects
alter table conversations
add column if not exists project_id uuid references projects(id) on delete set null;

create index if not exists idx_conversations_project
on conversations(project_id) where project_id is not null;

-- 3. Memories table
-- Core structured memory records with scope, provenance, and lifecycle fields.

create type memory_scope as enum ('global', 'project', 'conversation');
create type memory_status as enum ('active', 'superseded', 'archived');
create type memory_category as enum (
  'preference',    -- user likes/dislikes, style choices
  'decision',      -- explicit choices made ("use Supabase", "no RAG yet")
  'constraint',    -- limits, rules, requirements
  'fact',          -- stable truths about the user or domain
  'task',          -- ongoing work items, goals
  'context'        -- background info, project context
);

create table if not exists memories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,

  -- Scope and ownership
  scope memory_scope not null,
  project_id uuid references projects(id) on delete cascade,
  conversation_id uuid references conversations(id) on delete cascade,

  -- Content
  content text not null,
  category memory_category not null,

  -- Quality signals
  confidence float not null default 0.8 check (confidence >= 0 and confidence <= 1),
  importance int not null default 5 check (importance >= 1 and importance <= 10),

  -- Lifecycle
  status memory_status not null default 'active',
  superseded_by uuid references memories(id) on delete set null,

  -- Provenance
  source_conversation_id uuid references conversations(id) on delete set null,
  source_message_id uuid references messages(id) on delete set null,

  -- Timestamps
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Constraints: scope determines which FK must be set
  constraint scope_global_check check (
    scope != 'global' or (project_id is null and conversation_id is null)
  ),
  constraint scope_project_check check (
    scope != 'project' or (project_id is not null and conversation_id is null)
  ),
  constraint scope_conversation_check check (
    scope != 'conversation' or conversation_id is not null
  )
);

-- Indexes for the retrieval paths
create index if not exists idx_memories_user_global
on memories(user_id, status, importance desc)
where scope = 'global' and status = 'active';

create index if not exists idx_memories_project
on memories(project_id, status, importance desc)
where scope = 'project' and status = 'active';

create index if not exists idx_memories_conversation
on memories(conversation_id, status, importance desc)
where scope = 'conversation' and status = 'active';

create index if not exists idx_memories_source_conversation
on memories(source_conversation_id);

-- 4. Conversation summaries
-- Rolling summary of each conversation, updated after each exchange.

create table if not exists conversation_summaries (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade unique,
  summary text not null,
  message_count int not null default 0,
  last_summarized_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 5. Project summaries
-- Rolling summary of the project state, updated when project memory changes.

create table if not exists project_summaries (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade unique,
  summary text not null,
  memory_count int not null default 0,
  last_summarized_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 6. Row Level Security

alter table projects enable row level security;

create policy "users can manage own projects"
on projects for all
using (user_id = auth.uid())
with check (user_id = auth.uid());

alter table memories enable row level security;

create policy "users can view own memories"
on memories for select
using (user_id = auth.uid());

create policy "users can insert own memories"
on memories for insert
with check (user_id = auth.uid());

create policy "users can update own memories"
on memories for update
using (user_id = auth.uid());

create policy "users can delete own memories"
on memories for delete
using (user_id = auth.uid());

alter table conversation_summaries enable row level security;

create policy "users can manage own conversation summaries"
on conversation_summaries for all
using (
  exists (
    select 1 from conversations c
    where c.id = conversation_summaries.conversation_id
      and c.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1 from conversations c
    where c.id = conversation_summaries.conversation_id
      and c.user_id = auth.uid()
  )
);

alter table project_summaries enable row level security;

create policy "users can manage own project summaries"
on project_summaries for all
using (
  exists (
    select 1 from projects p
    where p.id = project_summaries.project_id
      and p.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1 from projects p
    where p.id = project_summaries.project_id
      and p.user_id = auth.uid()
  )
);

-- 7. Auto-update updated_at timestamps via trigger

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trg_projects_updated_at
  before update on projects
  for each row execute function set_updated_at();

create trigger trg_memories_updated_at
  before update on memories
  for each row execute function set_updated_at();

create trigger trg_conversation_summaries_updated_at
  before update on conversation_summaries
  for each row execute function set_updated_at();

create trigger trg_project_summaries_updated_at
  before update on project_summaries
  for each row execute function set_updated_at();

-- 8. Memory decay: archive old low-value memories automatically.
-- Run this on a cron (e.g. pg_cron weekly) or call manually.

create or replace function archive_stale_memories(
  p_conversation_age_days int default 90,
  p_min_importance int default 3
)
returns int
language plpgsql
as $$
declare
  archived_count int;
begin
  update memories
  set status = 'archived'
  where status = 'active'
    and scope = 'conversation'
    and importance <= p_min_importance
    and updated_at < now() - make_interval(days => p_conversation_age_days);

  get diagnostics archived_count = row_count;
  return archived_count;
end;
$$;

-- 9. Promote conversation memories to project scope.
-- Used when a standalone conversation is assigned to a project,
-- or when the user explicitly wants to share a memory across the project.

create or replace function promote_memory_to_project(
  p_memory_id uuid,
  p_project_id uuid
)
returns uuid
language plpgsql
as $$
declare
  new_id uuid;
begin
  insert into memories (
    user_id, scope, project_id, conversation_id,
    content, category, confidence, importance, status,
    source_conversation_id, source_message_id
  )
  select
    user_id, 'project', p_project_id, null,
    content, category, confidence, importance, 'active',
    source_conversation_id, source_message_id
  from memories
  where id = p_memory_id
    and scope = 'conversation'
    and status = 'active'
  returning id into new_id;

  -- Mark the original as superseded
  if new_id is not null then
    update memories
    set status = 'superseded', superseded_by = new_id
    where id = p_memory_id;
  end if;

  return new_id;
end;
$$;

-- 11. Helper function: get all active memories for a conversation's context
-- Returns global + project + conversation memories in one call.

create or replace function get_memory_context(
  p_user_id uuid,
  p_conversation_id uuid,
  p_project_id uuid default null,
  p_max_global int default 20,
  p_max_project int default 30,
  p_max_conversation int default 20
)
returns table (
  id uuid,
  scope memory_scope,
  content text,
  category memory_category,
  confidence float,
  importance int,
  created_at timestamptz,
  updated_at timestamptz
)
language sql stable
as $$
  -- Global memories (user-wide)
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.user_id = p_user_id
      and m.scope = 'global'
      and m.status = 'active'
    order by m.importance desc, m.updated_at desc
    limit p_max_global
  )
  union all
  -- Project memories (if conversation belongs to a project)
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.project_id = p_project_id
      and m.scope = 'project'
      and m.status = 'active'
      and p_project_id is not null
    order by m.importance desc, m.updated_at desc
    limit p_max_project
  )
  union all
  -- Conversation memories
  (
    select m.id, m.scope, m.content, m.category, m.confidence, m.importance, m.created_at, m.updated_at
    from memories m
    where m.conversation_id = p_conversation_id
      and m.scope = 'conversation'
      and m.status = 'active'
    order by m.importance desc, m.updated_at desc
    limit p_max_conversation
  );
$$;
