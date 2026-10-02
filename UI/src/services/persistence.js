/**
 * Where a conversation lives, decided once instead of everywhere.
 *
 * App.jsx used to call `chatService`, `behaviorService`, `projectService`,
 * `feedbackLogService` and `memoryService` directly, threading `user.id`
 * through nine call sites and guarding each with `!user?.id`. A guest has no
 * `user.id`, so supporting one that way would have meant `if (isGuest)`
 * scattered through 1,100 lines.
 *
 * Instead there are two implementations of one surface:
 *
 *   supabasePersistence   delegates to the existing services, unchanged
 *   ephemeralPersistence  in-memory Maps; nothing leaves the tab
 *
 * `createPersistence(principal)` picks one. Two properties fall out of that
 * and are worth stating, because the rest of the design leans on them:
 *
 * 1. **The principal's id is closed over, not passed per call.** That is why
 *    `userId` disappears from the call sites, and why the ephemeral branch
 *    cannot accidentally send `user_id: null` to PostgREST.
 * 2. **`capabilities` is a data answer to "what can this principal do".** The
 *    three surfaces a guest never sees -- projects, the profile page, the
 *    intern-alerts console -- read one boolean each instead of testing
 *    `principal.kind` inline.
 *
 * Deliberately NOT behind this adapter, because a guest cannot reach them and
 * an ephemeral stand-in would be worse than their absence:
 *   - UserProfile.jsx / MemoryManagement.jsx  account-only pages
 *   - internAlerts/lib/api.ts                 capability-gated operator console
 *   - supabaseHelpers.ensureProfile           account-only by definition
 */
import {
  fetchConversations,
  createConversation,
  renameConversation,
  deleteConversation,
  fetchMessages,
  insertMessage,
  deleteMessage,
  deleteMessagesAfter,
  autoTitleIfNeeded,
} from './chatService';
import { resolveEffectiveBehavior, detectActiveScope, fetchScopedBehavior } from './behaviorService';
import { insertFeedbackLog, updateFeedbackVote } from './feedbackLogService';
import { retrieveMemoryContext, processMemoryExtraction } from './memoryService';

/** What the UI may offer this principal. Read by JSX, not by logic. */
const ACCOUNT_CAPABILITIES = {
  history: true,
  projects: true,
  profile: true,
  memory: true,
  internAlerts: true,
};

const GUEST_CAPABILITIES = {
  history: false,
  projects: false,
  profile: false,
  memory: false,
  internAlerts: false,
};

// ── Supabase-backed ───────────────────────────────────────────────────────────

function supabasePersistence(principal) {
  const userId = principal.id;

  return {
    kind: 'supabase',
    capabilities: ACCOUNT_CAPABILITIES,

    // Conversations
    listConversations: (opts) => fetchConversations(opts),
    createConversation: (title = null, projectId = null, audience = null) =>
      createConversation(userId, title, projectId, audience),
    renameConversation: (conversationId, title) => renameConversation(conversationId, title),
    deleteConversation: (conversationId) => deleteConversation(conversationId),
    autoTitle: (conversationId, firstMessage, generator) =>
      autoTitleIfNeeded(conversationId, firstMessage, generator),

    // Messages
    listMessages: (opts) => fetchMessages(opts),
    insertMessage: (opts) => insertMessage(opts),
    deleteMessage: (messageId) => deleteMessage(messageId),
    deleteMessagesAfter: (conversationId, createdAt) =>
      deleteMessagesAfter(conversationId, createdAt),

    // Behavior
    resolveBehavior: (projectId, conversationId) =>
      resolveEffectiveBehavior(userId, projectId, conversationId),
    detectActiveScope: (opts) => detectActiveScope(userId, opts),
    fetchScopedBehavior: (opts) => fetchScopedBehavior(userId, opts),

    // Feedback
    logFeedback: (payload) => insertFeedbackLog({ ...payload, userId }),
    voteFeedback: (responseId, type) => updateFeedbackVote(responseId, userId, type),

    // Memory
    retrieveMemory: (conversationId) => retrieveMemoryContext(conversationId),
    extractMemory: (conversationId, messageId, userText, answer) =>
      processMemoryExtraction(conversationId, messageId, userText, answer),
  };
}

// ── In-memory ─────────────────────────────────────────────────────────────────

/**
 * Module-level, not component state, so a re-render does not wipe a guest's
 * conversation. Nothing persists it, so a refresh ends the session -- which is
 * the requirement rather than an accident.
 */
const guestStore = {
  conversations: new Map(),
  messages: new Map(), // conversationId -> row[]
  seq: 0,
};

/** Reset the in-memory store. Called on sign-out and when a guest session ends. */
export function clearEphemeralStore() {
  guestStore.conversations.clear();
  guestStore.messages.clear();
  guestStore.seq = 0;
}

function nextId(prefix) {
  guestStore.seq += 1;
  return `${prefix}-${guestStore.seq}-${Date.now().toString(36)}`;
}

function ephemeralPersistence() {
  return {
    kind: 'ephemeral',
    capabilities: GUEST_CAPABILITIES,

    // A guest's sidebar is always empty: nothing is listed because nothing is
    // kept. The conversation they are in lives in React state like anyone
    // else's, so the chat itself works normally.
    listConversations: async () => [],

    createConversation: async (title = null, projectId = null, audience = null) => {
      const now = new Date().toISOString();
      const row = {
        id: nextId('guest-convo'),
        title,
        last_message_preview: null,
        created_at: now,
        updated_at: now,
        // Always null in practice -- a guest has no projects to file it under
        // -- but kept in the shape so the two implementations return the same
        // row and nothing downstream has to know which one it came from.
        project_id: projectId,
        audience,
      };
      guestStore.conversations.set(row.id, row);
      guestStore.messages.set(row.id, []);
      return row;
    },

    renameConversation: async (conversationId, title) => {
      const row = guestStore.conversations.get(conversationId);
      if (row) row.title = title;
      return row ?? null;
    },

    deleteConversation: async (conversationId) => {
      guestStore.conversations.delete(conversationId);
      guestStore.messages.delete(conversationId);
    },

    // Titles are generated by the backend and then written to the row. There
    // is no row worth writing to, and no sidebar entry to label.
    autoTitle: async () => null,

    listMessages: async ({ conversationId }) => [...(guestStore.messages.get(conversationId) || [])],

    // The returned shape is load-bearing: runAssistantTurn reads `.id` and
    // `.created_at` off it to reconcile the optimistic row.
    insertMessage: async ({ conversationId, role, content }) => {
      const row = {
        id: nextId('guest-msg'),
        conversation_id: conversationId,
        role,
        content,
        created_at: new Date().toISOString(),
      };
      const thread = guestStore.messages.get(conversationId) || [];
      thread.push(row);
      guestStore.messages.set(conversationId, thread);
      return row;
    },

    deleteMessage: async (messageId) => {
      for (const [convoId, thread] of guestStore.messages) {
        const next = thread.filter(m => m.id !== messageId);
        if (next.length !== thread.length) guestStore.messages.set(convoId, next);
      }
    },

    deleteMessagesAfter: async (conversationId, createdAt) => {
      const thread = guestStore.messages.get(conversationId) || [];
      guestStore.messages.set(
        conversationId,
        thread.filter(m => m.created_at < createdAt)
      );
    },

    // A guest has no stored overrides. Empty means "use the backend's
    // auto-detected baseline", which is exactly right.
    resolveBehavior: async () => ({}),
    detectActiveScope: async () => 'user',
    fetchScopedBehavior: async () => null,

    // Both need a user id to attribute a row to. There is none.
    logFeedback: async () => {},
    voteFeedback: async () => {},

    // No memory for guests -- the stated requirement. Note this is also the
    // current behaviour for *everyone*: the `memory` edge function has never
    // been deployed, so memoryService is already a no-op that logs a warning.
    retrieveMemory: async () => '',
    extractMemory: async () => {},
  };
}

// ── Selection ─────────────────────────────────────────────────────────────────

/**
 * The store for a principal.
 *
 * @param {{kind: 'user'|'guest', id: string}|null} principal
 * @returns {object|null} null before sign-in, so callers can guard on the store
 *   itself rather than on a user id.
 */
export function createPersistence(principal) {
  if (!principal?.id) return null;
  return principal.kind === 'guest' ? ephemeralPersistence() : supabasePersistence(principal);
}
