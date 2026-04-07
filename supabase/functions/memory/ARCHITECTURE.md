# Memory System Architecture

## System Design Overview

The memory system provides **explicit, structured, application-layer memory** for a chatbot where project-scoped consistency is the primary goal. It does not rely on the LLM "remembering" anything — all memory is read from the database and injected into the prompt.

### Four Memory Scopes

```
┌─────────────────────────────────────────────────────────┐
│  GLOBAL MEMORY (user-wide)                              │
│  "prefers concise responses", "is a senior engineer"    │
│                                                         │
│  ┌──────────────────────────────────────────────┐       │
│  │  PROJECT MEMORY (shared across project chats)│       │
│  │  "use Supabase", "no RAG yet", "MVP first"  │       │
│  │                                              │       │
│  │  ┌────────────────┐  ┌────────────────┐      │       │
│  │  │ CONVERSATION A │  │ CONVERSATION B │      │       │
│  │  │ conv memories  │  │ conv memories  │      │       │
│  │  │ + working mem  │  │ + working mem  │      │       │
│  │  └────────────────┘  └────────────────┘      │       │
│  └──────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

- **Global**: User preferences, stable facts. Shared across ALL chats.
- **Project**: Decisions, constraints, context for a project. Shared across all chats IN that project.
- **Conversation**: Details only relevant to one specific chat thread.
- **Working**: Recent messages in the current turn (not persisted as "memory" — just the last N messages).

### How Project Consistency Works

When a user creates a project with multiple chats:
1. All chats share the same `project_id`
2. After each exchange, memories are extracted and classified by scope
3. A decision like "use Supabase" gets classified as `scope: project`
4. That memory is written with the project's ID
5. When ANY chat in that project loads, it retrieves all active project memories
6. Every chat in the project sees the same decisions, constraints, and context

**Key insight**: We don't copy messages between chats. We _promote_ important facts/decisions into shared project memory, which all chats read.

---

## Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `projects` | Groups conversations, owned by a user |
| `conversations` | Chat threads, optionally linked to a project via `project_id` |
| `messages` | Individual messages in a conversation |
| `memories` | Structured memory records with scope, category, provenance |
| `conversation_summaries` | Rolling summary per conversation |
| `project_summaries` | Rolling summary per project |

### `memories` table (core)

| Column | Type | Purpose |
|--------|------|---------|
| `scope` | enum | `global`, `project`, `conversation` |
| `category` | enum | `preference`, `decision`, `constraint`, `fact`, `task`, `context` |
| `confidence` | float | 0–1, how certain the extraction was |
| `importance` | int | 1–10, retrieval priority |
| `status` | enum | `active`, `superseded`, `archived` |
| `superseded_by` | uuid | Points to the memory that replaced this one |
| `source_conversation_id` | uuid | Which chat produced this memory |
| `source_message_id` | uuid | Which message produced this memory |

### Scope constraints (enforced at DB level)

- `global` → `project_id` and `conversation_id` must be NULL
- `project` → `project_id` must be set, `conversation_id` must be NULL
- `conversation` → `conversation_id` must be set

---

## Request Lifecycle

### 1. User Sends Message

```
Client → POST /chat
  { conversation_id, content }
```

### 2. Retrieve Memory (Read Path)

```
POST /memory/retrieve { conversation_id }

  1. Look up conversation's project_id + project name (single join query)
  2. In parallel:
     a. get_memory_context() → global + project + conversation memories
     b. Fetch conversation_summary
     c. Fetch project_summary (if project exists)
     d. Fetch last 10 messages (working memory)
  3. Compose into structured prompt section (with token budget cap)
  4. Return { memoryPrompt, context, meta }
```

### 3. Generate Response

```
System prompt = base_system_prompt + memoryPrompt
Messages = recent_messages + user_message
→ LLM call → assistant_message
```

### 4. Store Memory (Write Path)

```
POST /memory/process { conversation_id, message_id, user_message, assistant_message }

  0. Gate: skip if exchange is trivial ("ok", "thanks", very short)
  1. Single LLM call with:
     - the user-assistant exchange
     - existing summaries
     - existing memories (so the LLM avoids re-extraction and identifies supersessions)
  2. Parse response → candidate memories (with supersedes_id) + summary updates
  3. Batch insert all new memories in one DB call
  4. Batch mark superseded memories
  5. Upsert conversation + project summaries
```

**Key efficiency wins vs. original design:**
- Trivial exchanges are gated out before any LLM call
- One LLM call instead of two (extraction + dedup merged)
- Extraction sees existing memories → avoids redundant re-extraction
- Batch insert instead of sequential per-memory inserts
- Meta (project_id, project_name) fetched once via join, reused across read+write

---

## Conflict Resolution & Versioning

When a new memory contradicts an existing one:

1. The extraction LLM sees all existing memories and identifies the conflict
2. It returns the new memory with `supersedes_id` pointing to the old one
3. The old memory's `status` is set to `superseded`
4. Its `superseded_by` field points to the new memory
5. The old memory is NOT deleted — it remains for audit/inspection
6. Only `active` memories appear in retrieval queries

Example:
```
Memory A (old): "use PostgreSQL for the database" → status: superseded, superseded_by: B
Memory B (new): "use Supabase for the database"   → status: active
```

---

## Token Budget

The `composeMemoryPrompt` function enforces a character budget (default ~6000 chars ≈ ~1500 tokens) with priority ordering:

1. **Project memories** (highest priority — project consistency is the goal)
2. **Global memories** (user preferences)
3. **Conversation memories** (lowest — most context is in recent messages)

If the budget is exceeded, lower-priority sections are dropped entirely.

---

## Integration Pattern

```typescript
// In your chat handler:
import { MemoryService } from './memory/service.ts';

// 1. Before LLM call — retrieve memory (returns meta for reuse)
const { memoryPrompt, context, meta } = await memoryService.retrieveAndCompose(userId, conversationId);
const systemPrompt = BASE_SYSTEM_PROMPT + '\n\n' + memoryPrompt;

// 2. Call LLM with memory-augmented prompt
const response = await callLLM(systemPrompt, messages);

// 3. After LLM call — extract and store memories (fire-and-forget)
//    Pass meta and context to avoid redundant DB queries
memoryService.processExchange(userId, conversationId, messageId, userMsg, response, meta, context)
  .catch(err => console.error('Memory processing failed:', err));
```

The write path runs fire-and-forget so it doesn't slow down the response.

---

## Memory Promotion & Project Assignment

A conversation can start standalone (no project) and later be assigned to one.

**`assignConversationToProject(conversationId, projectId)`:**
1. Sets `project_id` on the conversation
2. Finds all active conversation memories with importance >= 7
3. For each, calls `promote_memory_to_project()` — a DB function that:
   - Copies the memory with `scope: project` and the new `project_id`
   - Marks the original as `superseded` pointing to the copy

**`promoteMemory(memoryId, projectId)`:**
Manual promotion of a single memory. Useful for a future "share to project" UI action.

---

## Memory Decay & Cleanup

**`archive_stale_memories(age_days, min_importance)`** — a DB function that archives old, low-value conversation memories:
- Targets `scope: conversation`, `importance <= min_importance`, `updated_at` older than `age_days`
- Sets `status = 'archived'`
- Does NOT touch global or project memories (those are long-lived by design)
- Run via `pg_cron` weekly, or call manually

This prevents unbounded growth of conversation-scoped noise while preserving all project-level and global memories.

---

## Implementation Notes for Supabase

1. **Run the migration** to create all tables, indexes, RLS policies, and the `get_memory_context` function
2. **Deploy the edge function**: `supabase functions deploy memory`
3. **Set secrets**: `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) for the extraction LLM
4. **Extraction model**: Uses Claude Haiku for cheap, fast memory extraction — this is NOT the main chat model
5. **RLS**: All tables are secured — users can only access their own data
6. **Indexes**: Partial indexes on `status='active'` ensure fast retrieval without scanning archived memories
7. **The `get_memory_context` function**: Single DB call fetches all three scopes, sorted by importance
8. **Project summary race condition**: When two project chats write concurrently, the upsert on `project_id` unique constraint means last-write-wins. The summary is regenerated from scratch each time (not appended), so the "losing" write just gets a slightly stale summary that will be corrected on the next exchange.
9. **`updated_at` triggers**: All tables have `BEFORE UPDATE` triggers that auto-set `updated_at = now()`. No need to set this in application code.
10. **Memory decay**: Run `select archive_stale_memories(90, 3)` weekly via `pg_cron` to clean up old low-value conversation memories.
11. **Memory promotion**: Use `promote_memory_to_project()` to share important conversation-scoped memories to the project level — either automatically during project assignment or manually via the `/promote` endpoint.
