import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import Sidebar from './components/Sidebar';
import MainChat from './components/MainChat';
import RightPanel from './components/RightPanel';
import UserProfile from './components/UserProfile';
import InternJobsAlertsPage from './internAlerts/InternJobsAlertsPage.tsx';
import Login from './components/Login';
import Signup from './components/Signup';
import VerifyEmail from './components/VerifyEmail';
import Onboarding from './components/Onboarding';
import { supabase } from './supabaseClient';
import { ensureProfile } from './supabaseHelpers';
import { sendMessage, generateTitle, fetchAutoBehavior, DEFAULT_MODEL_KEY } from './services/llamaService';
import { startTurn } from './services/telemetryService';
import { primeAuthToken, clearAuthToken, getAuthToken } from './services/authToken';
// Conversations, messages, behaviour overrides, feedback and memory all go
// through one store, so where they are kept is decided once (see
// services/persistence.js) rather than at every call site.
import { createPersistence, clearEphemeralStore } from './services/persistence';
import { startGuestSession, endGuestSession } from './services/guestSession';
import { resolveAudience } from './config/audiences';
// Account-only surfaces stay direct: a guest never reaches the personalization
// panel or the projects tree, and an ephemeral stand-in for either would be
// worse than not offering them.
import { fetchBehaviorSettings, updateBehaviorSettings, upsertScopedBehavior, deleteScopedBehavior, DEFAULT_BEHAVIOR } from './services/behaviorService';
import ScopedBehaviorPanel from './components/ScopedBehaviorPanel';
import {
  fetchProjects,
  createProject,
  renameProject,
  deleteProject,
  fetchProjectConversations,
  assignConversationToProject,
  unassignConversationFromProject,
} from './services/projectService';
import './App.css';

export default function App() {
  const [authPage, setAuthPage] = useState('login');
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState('');
  const [unverifiedEmail, setUnverifiedEmail] = useState('');
  // A visitor with no account. Held separately from `user` because the two
  // are produced by completely different machinery: one by supabase-js, one
  // by POST /api/guest/session.
  const [guest, setGuest] = useState(null);
  // The caller's `profiles` row. `null` means 'not loaded yet', which is
  // deliberately different from 'loaded and not onboarded' -- see the gate
  // near the bottom of this file.
  const [profile, setProfile] = useState(null);

  useEffect(() => {
    let alive = true;

    const withTimeout = (promise, label, timeoutMs = 12000) =>
      Promise.race([
        promise,
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs);
        }),
      ]);

    const bootstrapAuth = async () => {
      try {
        const sessionRes = await withTimeout(supabase.auth.getSession(), 'Auth session check');
        const session = sessionRes?.data?.session;
        // The only getSession() call in the app. It used to read .user and drop
        // the token; the backend needs it on every request now.
        primeAuthToken(session ?? null);

        if (session?.user) {
          if (!session.user.email?.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }

          const isOAuth = session.user.app_metadata?.provider !== 'email';
          if (!isOAuth && !session.user.email_confirmed_at) {
            if (alive) {
              setUnverifiedEmail(session.user.email);
              setAuthPage('verify-email');
            }
            return;
          }

          if (alive) {
            setUser(session.user);
            setAuthPage(null);
            setAuthError('');
          }

          // The return value used to be discarded. ensureProfile already does
          // select('*'), so active_audience and onboarded_at arrive here with
          // no extra round trip -- they were simply being thrown away.
          withTimeout(ensureProfile(session.user), 'Profile check')
            .then((row) => { if (alive && row) setProfile(row); })
            .catch((profileError) => {
              if (alive) {
                console.warn('Profile sync warning:', profileError?.message || profileError);
              }
            });
        } else if (alive) {
          setUser(null);
          setProfile(null);
          setAuthPage('login');
        }
      } catch (error) {
        if (alive) {
          setUser(null);
          setAuthPage('login');
          setAuthError(error?.message || 'Failed to initialize authentication.');
        }
      } finally {
        if (alive) setAuthLoading(false);
      }
    };

    bootstrapAuth();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(async (_event, session) => {
      // First, before any early return below. This callback sees every session
      // supabase-js produces -- sign-in, sign-out and TOKEN_REFRESHED alike --
      // which is what keeps the cached token fresh without polling. A sign-out
      // arrives here with a null session and clears it.
      primeAuthToken(session ?? null);

      try {
        if (session?.user) {
          if (session.user.email && !session.user.email.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }

          const isOAuth = session.user.app_metadata?.provider !== 'email';
          if (!isOAuth && !session.user.email_confirmed_at) {
            if (alive) {
              setUnverifiedEmail(session.user.email);
              setAuthPage('verify-email');
            }
            return;
          }

          if (alive) {
            setUser(session.user);
            setAuthPage(null);
            setAuthError('');
          }

          withTimeout(ensureProfile(session.user), 'Profile sync')
            .then((row) => { if (alive && row) setProfile(row); })
            .catch((profileError) => {
              if (alive) {
                console.warn('Profile sync warning:', profileError?.message || profileError);
              }
            });
        } else if (alive) {
          setUser(null);
          setProfile(null);
          setAuthPage('login');
        }
      } catch (error) {
        if (alive) {
          setAuthError(error?.message || 'Authentication state update failed.');
        }
      }
    });

    return () => {
      alive = false;
      subscription.unsubscribe();
    };
  }, []);

  // ── Who is acting, and where their data goes ────────────────────────────────

  // One object instead of `user?.id` threaded through every call site, so
  // nothing below has to ask whether it is looking at a Supabase session or a
  // guest. Phase 2.4 adds the guest branch; today every principal is a
  // signed-in user, which is what makes this step a pure refactor.
  const principal = useMemo(() => {
    if (user?.id) return { kind: 'user', id: user.id, email: user.email };
    if (guest?.id) return { kind: 'guest', id: guest.id };
    return null;
  }, [user?.id, user?.email, guest?.id]);

  // Swapping this swaps where every conversation, message, override, feedback
  // row and memory call goes. Null until sign-in, so callers guard on the store
  // rather than on a user id.
  const store = useMemo(() => createPersistence(principal), [principal]);

  // What the UI may offer. Read by JSX; never branched on `principal.kind`.
  const can = store?.capabilities ?? {};

  // Which experience this person gets: their saved choice, or 'guest' for a
  // visitor, who has no profile row to have chosen in. resolveAudience falls
  // back rather than throwing, so an unrecognised stored value degrades to the
  // default instead of blanking the screen.
  const audience = useMemo(
    () => resolveAudience(principal?.kind === 'guest' ? 'guest' : profile?.active_audience),
    [principal?.kind, profile?.active_audience]
  );

  const [isDarkMode, setIsDarkMode] = useState(false);
  const [currentPage, setCurrentPage] = useState('chat');
  const [rightPanelContent, setRightPanelContent] = useState('empty');
  const [rightPanelLinks, setRightPanelLinks] = useState([]);
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL_KEY);

  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  // Backend progress key ('received' | 'searching' | 'generating') shown while
  // waiting for the first answer token. MainChat maps it to display text.
  const [streamStatus, setStreamStatus] = useState(null);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [hasMoreConversations, setHasMoreConversations] = useState(false);
  const messagesEndRef = useRef(null);
  const abortRef = useRef(null);

  const [projects, setProjects] = useState([]);
  const [projectConversations, setProjectConversations] = useState({});
  const [expandedProjects, setExpandedProjects] = useState({});
  const [activeProjectId, setActiveProjectId] = useState(null); // project context for new chats

  // Behavior settings
  const [behaviorSettings, setBehaviorSettings] = useState(null);

  // Scoped behavior panel state
  const [scopedPanel, setScopedPanel] = useState({
    open: false,
    scope: null,          // 'project' | 'conversation'
    scopeId: null,        // project_id or conversation_id
    scopeLabel: '',       // display name
  });
  const [scopedBehavior, setScopedBehavior] = useState(null);        // the override row (null = no override)
  const [activeBehaviorScope, setActiveBehaviorScope] = useState('user'); // 'user' | 'project' | 'conversation'
  const [autoBehavior, setAutoBehavior] = useState(null);            // auto-detected behavior from backend

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  useEffect(() => {
    // /api/jobs/fetch is gated on a 'run_jobs' grant in admin_grants, which
    // only a real account can hold.
    if (principal?.kind !== 'user') return undefined;

    const schedulerEnabled = import.meta.env.VITE_ENABLE_JOB_FETCHER_SCHEDULER === 'true';
    if (!schedulerEnabled) return undefined;

    const schedulerMs = Number(import.meta.env.VITE_JOB_FETCHER_SCHEDULER_MS || 300000);
    let running = false;

    const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
    const runCycle = async () => {
      if (running) return;
      running = true;
      try {
        const token = await getAuthToken();
        const res = await fetch(`${API_BASE}/api/jobs/fetch`, {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        // This endpoint now needs a 'run_jobs' grant in admin_grants, which is
        // seeded by hand. Without the check a 403 (no grant) or 503 (grants
        // unreadable) would be indistinguishable from a working scheduler.
        if (!res.ok) {
          console.error('Job scheduler cycle rejected:', res.status);
        }
      } catch (error) {
        console.error('Job scheduler cycle failed:', error?.message || error);
      } finally {
        running = false;
      }
    };

    runCycle();
    const intervalId = setInterval(runCycle, schedulerMs);
    return () => clearInterval(intervalId);
  }, [principal?.kind]);

  const loadConversations = useCallback(async (cursor = null) => {
    if (!store) return;
    try {
      const data = await store.listConversations({ limit: 20, cursor });
      if (cursor) {
        setConversations(prev => [...prev, ...data]);
      } else {
        setConversations(data);
      }
      setHasMoreConversations(data.length === 20);
    } catch (err) {
      console.error('Failed to load conversations:', err.message);
    }
  }, [store]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  /**
   * Patch one conversation in the local lists and move it to the top.
   *
   * Refetching the entire sidebar after every message — which is what this
   * replaces — was a round trip to restate what we already knew locally.
   */
  const patchConversation = useCallback((convoId, patch) => {
    const apply = (list) => {
      const idx = list.findIndex(c => c.id === convoId);
      if (idx === -1) return list;
      const updated = { ...list[idx], ...patch };
      return [updated, ...list.slice(0, idx), ...list.slice(idx + 1)];
    };

    setConversations(apply);
    setProjectConversations(prev => {
      let changed = false;
      const next = {};
      for (const [projectId, list] of Object.entries(prev)) {
        const applied = apply(list);
        if (applied !== list) changed = true;
        next[projectId] = applied;
      }
      return changed ? next : prev;
    });
  }, []);

  /** Mirror what chatService.insertMessage writes to last_message_preview. */
  const previewFor = (text) => (text.length > 80 ? text.slice(0, 80) + '...' : text);

  const loadMoreConversations = useCallback(() => {
    if (!hasMoreConversations || !conversations.length) return;
    const oldest = conversations[conversations.length - 1];
    loadConversations(oldest.updated_at);
  }, [hasMoreConversations, conversations, loadConversations]);

  const loadProjects = useCallback(async () => {
    if (!can.projects) return;
    try {
      const data = await fetchProjects();
      setProjects(data);
    } catch (err) {
      console.error('Failed to load projects:', err.message);
    }
  }, [can.projects]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  // ── Load behavior settings when user logs in ────────────
  useEffect(() => {
    if (!can.profile || !user?.id) return;
    fetchBehaviorSettings(user.id)
      .then(setBehaviorSettings)
      .catch(err => {
        console.warn('Failed to load behavior settings, using empty overrides:', err.message);
        setBehaviorSettings({});
      });
  }, [can.profile, user?.id]);

  const handleUpdateBehavior = async (updates) => {
    if (!can.profile || !user?.id) return;
    try {
      const updated = await updateBehaviorSettings(user.id, updates);
      setBehaviorSettings(updated);
    } catch (err) {
      console.error('Failed to update behavior settings:', err.message);
    }
  };

  // ── Scoped behavior panel helpers ──────────────────────────────────────────

  // Detect which scope is active for the current conversation
  useEffect(() => {
    if (!store || !currentConversationId) {
      setActiveBehaviorScope('user');
      return;
    }
    store
      .detectActiveScope({ projectId: activeProjectId, conversationId: currentConversationId })
      .then(setActiveBehaviorScope);
  }, [store, currentConversationId, activeProjectId, scopedPanel.open]);

  const openScopedPanel = async (scope, scopeId, scopeLabel) => {
    if (!store) return;
    setScopedBehavior(await store.fetchScopedBehavior({ scope, scopeId }));

    setScopedPanel({ open: true, scope, scopeId, scopeLabel });

    // Fetch auto-detected behavior for this conversation's context
    const context = messages.slice(-20).map(m => ({
      role: m.sender === 'user' ? 'user' : 'assistant',
      content: m.text,
    }));
    if (context.length > 0) {
      fetchAutoBehavior(context).then(result => {
        if (result?.behavior) setAutoBehavior(result.behavior);
      }).catch(() => {});
    }
  };

  const handleSaveScopedBehavior = async (updates) => {
    if (!can.profile || !user?.id || !scopedPanel.scopeId) return;
    try {
      const result = await upsertScopedBehavior(user.id, updates, {
        projectId: scopedPanel.scope === 'project' ? scopedPanel.scopeId : null,
        conversationId: scopedPanel.scope === 'conversation' ? scopedPanel.scopeId : null,
      });
      setScopedBehavior(result);
    } catch (err) {
      console.error('Failed to save scoped behavior:', err.message);
    }
  };

  const handleDeleteScopedBehavior = async () => {
    if (!can.profile || !user?.id || !scopedPanel.scopeId) return;
    try {
      await deleteScopedBehavior(user.id, {
        projectId: scopedPanel.scope === 'project' ? scopedPanel.scopeId : null,
        conversationId: scopedPanel.scope === 'conversation' ? scopedPanel.scopeId : null,
      });
      setScopedBehavior(null);
    } catch (err) {
      console.error('Failed to delete scoped behavior:', err.message);
    }
  };

  const handleOpenProjectBehavior = (projectId, projectName) => {
    openScopedPanel('project', projectId, projectName);
  };

  const handleOpenConversationBehavior = () => {
    if (!currentConversationId) return;
    const convo = conversations.find(c => c.id === currentConversationId);
    // Also check project conversations
    let title = convo?.title;
    if (!title) {
      for (const convos of Object.values(projectConversations)) {
        const found = convos.find(c => c.id === currentConversationId);
        if (found) { title = found.title; break; }
      }
    }
    openScopedPanel('conversation', currentConversationId, title || 'Current Chat');
  };

  const loadProjectConvos = useCallback(async (projectId) => {
    try {
      const convos = await fetchProjectConversations(projectId);
      setProjectConversations(prev => ({ ...prev, [projectId]: convos }));
    } catch (err) {
      console.error('Failed to load project conversations:', err.message);
    }
  }, []);

  const handleCreateProject = async (name) => {
    if (!can.projects || !user?.id) return;
    try {
      const project = await createProject(user.id, name);
      setProjects(prev => [project, ...prev]);
      setExpandedProjects(prev => ({ ...prev, [project.id]: true }));
      setProjectConversations(prev => ({ ...prev, [project.id]: [] }));
    } catch (err) {
      console.error('Create project failed:', err.message);
    }
  };

  const handleRenameProject = async (projectId, newName) => {
    try {
      await renameProject(projectId, newName);
      setProjects(prev => prev.map(p => p.id === projectId ? { ...p, name: newName } : p));
    } catch (err) {
      console.error('Rename project failed:', err.message);
    }
  };

  const handleDeleteProject = async (projectId) => {
    try {
      await deleteProject(projectId);
      setProjects(prev => prev.filter(p => p.id !== projectId));
      setProjectConversations(prev => {
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
      if (activeProjectId === projectId) setActiveProjectId(null);
      loadConversations();
    } catch (err) {
      console.error('Delete project failed:', err.message);
    }
  };

  const handleToggleProject = useCallback((projectId) => {
    setExpandedProjects(prev => {
      const nowExpanded = !prev[projectId];
      if (nowExpanded) loadProjectConvos(projectId);
      return { ...prev, [projectId]: nowExpanded };
    });
  }, [loadProjectConvos]);

  const handleNewChatInProject = (projectId) => {
    setActiveProjectId(projectId);
    setCurrentConversationId(null);
    setMessages([]);
    setRightPanelContent('empty');
    setRightPanelLinks([]);
    setCurrentPage('chat');
  };

  const handleAssignToProject = async (conversationId, projectId) => {
    try {
      await assignConversationToProject(conversationId, projectId);
      setConversations(prev => prev.filter(c => c.id !== conversationId));
      loadProjectConvos(projectId);
      setExpandedProjects(prev => ({ ...prev, [projectId]: true }));
    } catch (err) {
      console.error('Assign to project failed:', err.message);
    }
  };

  const handleRemoveFromProject = async (conversationId) => {
    try {
      await unassignConversationFromProject(conversationId);
      setProjectConversations(prev => {
        const next = {};
        for (const [pid, convos] of Object.entries(prev)) {
          next[pid] = convos.filter(c => c.id !== conversationId);
        }
        return next;
      });
      loadConversations();
    } catch (err) {
      console.error('Remove from project failed:', err.message);
    }
  };

  const openConversation = useCallback(async (conversationId) => {
    if (!store) return;
    setCurrentConversationId(conversationId);
    setMessages([]);
    setCurrentPage('chat');
    setLoadingMessages(true);
    setRightPanelContent('empty');
    setRightPanelLinks([]);

    try {
      const msgs = await store.listMessages({ conversationId, limit: 30 });
      const mapped = msgs.map(m => ({
        id: m.id,
        text: m.content,
        sender: m.role === 'user' ? 'user' : 'bot',
        created_at: m.created_at,
      }));
      setMessages(mapped);
      setHasMoreMessages(msgs.length === 30);
      if (mapped.length > 0) setRightPanelContent('empty');
    } catch (err) {
      console.error('Failed to load messages:', err.message);
    } finally {
      setLoadingMessages(false);
    }
  }, [store]);

  const loadOlderMessages = useCallback(async () => {
    if (!store || !currentConversationId || !hasMoreMessages || loadingMessages) return;
    setLoadingMessages(true);

    try {
      const oldest = messages[0];
      const cursor = oldest?.created_at || null;
      const older = await store.listMessages({ conversationId: currentConversationId, limit: 30, cursor });
      const mapped = older.map(m => ({
        id: m.id,
        text: m.content,
        sender: m.role === 'user' ? 'user' : 'bot',
        created_at: m.created_at,
      }));
      setMessages(prev => [...mapped, ...prev]);
      setHasMoreMessages(older.length === 30);
    } catch (err) {
      console.error('Failed to load older messages:', err.message);
    } finally {
      setLoadingMessages(false);
    }
  }, [store, currentConversationId, hasMoreMessages, loadingMessages, messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (!loadingMessages) scrollToBottom();
  }, [messages, loadingMessages]);

  // The shared tail of every assistant turn. Send, regenerate and
  // edit-and-resubmit differ only in how they prepare the thread, so the
  // request, the persistence and the error handling live here once instead of
  // three times.
  //
  // `prepare` runs inside the try, so a failure while creating the conversation
  // or truncating the thread lands in the same catch it always did. It returns
  // everything the turn needs, including the placeholder id it just pushed --
  // minting that here instead would reorder handleRegenerate's 'ack' mark.
  //
  // These were three near-identical copies that had already drifted: only two
  // of them recovered when the placeholder row was gone, and they marked
  // 'assistant_saved' at different points. Both are reconciled below.
  const runAssistantTurn = async ({ turn, prepare }) => {
    try {
      const {
        conversationId,
        context,
        tempId,
        sourceText,
        userSave = null,
      } = await prepare();

      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Resolve manual overrides + memory in parallel
      const [manualBehavior, memoryPrompt] = await Promise.all([
        store.resolveBehavior(activeProjectId, conversationId).catch(() => null),
        store.retrieveMemory(conversationId).catch(() => ''),
      ]);
      turn.mark('context_ready');
      let fullResponse = '';
      const assistantMeta = await sendMessage({
        messages: context,
        model: selectedModel,
        signal: controller.signal,
        behavior: manualBehavior,
        memoryPrompt,
        audience: audience.id,
        onStatus: setStreamStatus,
        onChunk: (chunk) => {
          fullResponse += chunk;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text: m.text + chunk } : m));
        },
        onReplace: (text) => {
          fullResponse = text;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text } : m));
        },
      });

      const sources = assistantMeta?.sources || [];
      setRightPanelLinks(sources);
      setRightPanelContent(sources.length > 0 ? 'links' : 'empty');

      // Persist the assistant message after the user's (created_at orders the
      // thread). Regenerate has no new user row to wait for.
      if (userSave) await userSave;
      const assistantRow = await store.insertMessage({
        conversationId,
        role: 'assistant',
        content: fullResponse,
      });
      turn.mark('assistant_saved');

      // Fire-and-forget: feedback log + memory extraction. Both are no-ops for
      // a guest -- neither has a user id to attribute a row to.
      store.logFeedback({
        responseId:       assistantRow.id,
        conversationId,
        behaviorSnapshot: manualBehavior,
        validatorsRun:    assistantMeta?.validatorsRun    ?? [],
        validatorsPassed: assistantMeta?.validatorsPassed ?? true,
        repairsApplied:   assistantMeta?.repairsApplied   ?? [],
        modelUsed:        selectedModel,
      }).catch(() => {});

      store.extractMemory(conversationId, assistantRow.id, sourceText, fullResponse).catch(() => {});

      // Replace temp message with persisted one
      setMessages(prev => prev.map(m => m.id === tempId ? { ...m, id: assistantRow.id, created_at: assistantRow.created_at } : m));

      patchConversation(conversationId, {
        last_message_preview: previewFor(fullResponse),
        updated_at: assistantRow.created_at,
      });
      turn.finish({
        outcome: 'ok',
        requestId: assistantMeta?.requestId,
        model: selectedModel,
        stream: assistantMeta?.timings,
      });
    } catch (err) {
      turn.finish({
        outcome: err.name === 'AbortError' ? 'aborted' : 'error',
        requestId: err.requestId,
        model: selectedModel,
        stream: err.timings,
      });
      if (err.name === 'AbortError') return;

      // Patch the placeholder while it is still the last message; append a
      // fresh row if it never got pushed or has already been replaced.
      // Regenerate used to only patch by id, so an error that arrived after the
      // placeholder went away was silent.
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.sender === 'bot') {
          return prev.map(m =>
            m.id === last.id ? { ...m, text: `**Error:** ${err.message}` } : m
          );
        }
        return [
          ...prev,
          {
            id: `err-${Date.now()}`,
            text: `**Error:** ${err.message}`,
            sender: 'bot',
          },
        ];
      });
    } finally {
      setIsTyping(false);
      setStreamStatus(null);
      abortRef.current = null;
    }
  };

  const handleSend = async () => {
    if (!input.trim() || !store) return;
    const turn = startTurn('send');

    const userText = input.trim();

    setInput('');
    setIsTyping(true);
    setStreamStatus('received');
    setRightPanelContent('empty');
    setRightPanelLinks([]);

    await runAssistantTurn({
      turn,
      prepare: async () => {
        // Echo the user's own message before touching the network. This used to
        // sit behind two awaited Supabase round trips (create conversation, then
        // insert message + preview update), so the message the user just typed
        // took 2-3 network hops to appear.
        const tempUserId = `temp-user-${Date.now()}`;
        const userMsg = {
          id: tempUserId,
          text: userText,
          sender: 'user',
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, userMsg]);
        turn.mark('ack');

        let convoId = currentConversationId;

        if (!convoId) {
          const convo = await store.createConversation(null, activeProjectId, audience.id);
          convoId = convo.id;
          setCurrentConversationId(convoId);

          if (activeProjectId) {
            setProjectConversations(prev => ({
              ...prev,
              [activeProjectId]: [convo, ...(prev[activeProjectId] || [])],
            }));
          } else {
            setConversations(prev => [convo, ...prev]);
          }
        }

        // Save the user's message alongside the chat request rather than before
        // it: the model doesn't need the saved row, and awaiting it here added
        // 200-600 ms to every answer. It is awaited before the assistant's row
        // is inserted, so the two still land in order.
        const userSave = store.insertMessage({
          conversationId: convoId,
          role: 'user',
          content: userText,
        }).then(userRow => {
          turn.mark('user_saved');

          // Reconcile the optimistic row with the persisted one.
          setMessages(prev =>
            prev.map(m =>
              m.id === tempUserId
                ? { ...m, id: userRow.id, created_at: userRow.created_at }
                : m
            )
          );
          patchConversation(convoId, {
            last_message_preview: previewFor(userText),
            updated_at: userRow.created_at,
          });

          store.autoTitle(convoId, userText, generateTitle)
            .then(title => {
              if (title) patchConversation(convoId, { title });
            })
            .catch(() => {});

          return userRow;
        });
        // Awaited in runAssistantTurn; this only stops an early failure from
        // being reported as unhandled while the answer streams.
        userSave.catch(() => {});

        const currentMessages = [...messages, userMsg];
        const context = currentMessages.slice(-20).map(m => ({
          role: m.sender === 'user' ? 'user' : 'assistant',
          content: m.text,
        }));

        const tempId = `temp-${Date.now()}`;
        setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);

        return { conversationId: convoId, context, tempId, sourceText: userText, userSave };
      },
    });
  };

  const handleRegenerate = async () => {
    if (!currentConversationId || !store || isTyping) return;

    const lastBotIdx = [...messages].reverse().findIndex(m => m.sender === 'bot');
    if (lastBotIdx === -1) return;

    const botIdx = messages.length - 1 - lastBotIdx;
    const botMsg = messages[botIdx];

    let userMsg = null;
    for (let i = botIdx - 1; i >= 0; i--) {
      if (messages[i].sender === 'user') {
        userMsg = messages[i];
        break;
      }
    }
    if (!userMsg) return;
    const turn = startTurn('regenerate');

    if (
      botMsg.id &&
      !String(botMsg.id).startsWith('temp-') &&
      !String(botMsg.id).startsWith('err-')
    ) {
      try {
        await store.deleteMessage(botMsg.id);
      } catch {
        // best-effort delete; continue regenerating even if the row is gone
      }
    }

    setMessages(prev => prev.filter(m => m.id !== botMsg.id));
    setIsTyping(true);
    setStreamStatus('received');

    await runAssistantTurn({
      turn,
      prepare: async () => {
        const context = messages
          .slice(0, botIdx)
          .slice(-20)
          .map(m => ({
            role: m.sender === 'user' ? 'user' : 'assistant',
            content: m.text,
          }));

        const tempId = `temp-${Date.now()}`;
        setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);
        turn.mark('ack');

        // No userSave: regenerating reuses the user's existing message, so
        // there is no new row to insert or wait for.
        return {
          conversationId: currentConversationId,
          context,
          tempId,
          sourceText: userMsg.text,
        };
      },
    });
  };

  const handleEditAndResubmit = async (msgId, newText) => {
    if (!currentConversationId || !store || isTyping) return;

    const msgIdx = messages.findIndex(m => m.id === msgId);
    if (msgIdx === -1) return;
    const originalMsg = messages[msgIdx];
    const turn = startTurn('edit');

    // Show the edited message at once, as send does; the database catches up.
    const preceding = messages.slice(0, msgIdx);
    const tempUserId = `temp-user-${Date.now()}`;
    const userMsg = {
      id: tempUserId,
      text: newText,
      sender: 'user',
      created_at: new Date().toISOString(),
    };
    setMessages([...preceding, userMsg]);
    turn.mark('ack');
    setIsTyping(true);
    setStreamStatus('received');

    await runAssistantTurn({
      turn,
      prepare: async () => {
        // The old message and everything after it must be gone before the
        // edited one is inserted: the delete matches created_at >= the
        // original's.
        if (originalMsg.created_at) {
          try {
            await store.deleteMessagesAfter(currentConversationId, originalMsg.created_at);
          } catch {
            // best-effort cleanup; local state is the source of truth here
          }
        }

        // As in handleSend: saved alongside the chat request, awaited before
        // the assistant's row.
        const userSave = store.insertMessage({
          conversationId: currentConversationId,
          role: 'user',
          content: newText,
        }).then(userRow => {
          turn.mark('user_saved');
          setMessages(prev =>
            prev.map(m =>
              m.id === tempUserId
                ? { ...m, id: userRow.id, created_at: userRow.created_at }
                : m
            )
          );
          return userRow;
        });
        userSave.catch(() => {});

        const context = [...preceding, userMsg].slice(-20).map(m => ({
          role: m.sender === 'user' ? 'user' : 'assistant',
          content: m.text,
        }));

        const tempId = `temp-${Date.now()}`;
        setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);

        return {
          conversationId: currentConversationId,
          context,
          tempId,
          sourceText: newText,
          userSave,
        };
      },
    });
  };

  const handleSuggestionClick = (text) => {
    setInput(text);
  };

  // ── Feedback vote (thumbs-up / thumbs-down on bot messages) ──────────────
  const handleFeedback = useCallback((msgId, type) => {
    if (!store || !type) return;
    store.voteFeedback(msgId, type).catch(() => {});
  }, [store]);

  // ── New chat ──────────────────────────────────────────────
  const startNewChat = () => {
    setCurrentConversationId(null);
    setActiveProjectId(null);
    setMessages([]);
    setRightPanelContent('empty');
    setRightPanelLinks([]);
    setCurrentPage('chat');
  };

  const handleRenameConversation = async (convoId, newTitle) => {
    try {
      await store.renameConversation(convoId, newTitle);
      setConversations(prev =>
        prev.map(c => (c.id === convoId ? { ...c, title: newTitle } : c))
      );
    } catch (err) {
      console.error('Rename failed:', err.message);
    }
  };

  const handleDeleteConversation = async (convoId) => {
    try {
      await store.deleteConversation(convoId);
      setConversations(prev => prev.filter(c => c.id !== convoId));
      setProjectConversations(prev => {
        const next = {};
        for (const [pid, convos] of Object.entries(prev)) {
          next[pid] = convos.filter(c => c.id !== convoId);
        }
        return next;
      });
      if (currentConversationId === convoId) {
        setCurrentConversationId(null);
        setMessages([]);
        setRightPanelContent('empty');
        setRightPanelLinks([]);
      }
    } catch (err) {
      console.error('Delete failed:', err.message);
    }
  };

  const handleLogin = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  const handleSignup = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  /**
   * Start a guest session.
   *
   * Throws are surfaced through `authError` on the login screen rather than
   * swallowed: a "Continue as guest" button that silently does nothing is
   * worse than a message saying why.
   */
  const handleContinueAsGuest = async () => {
    setAuthError('');
    const session = await startGuestSession();
    clearEphemeralStore();
    setGuest({ id: session.guestId });
    setAuthPage(null);
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    // signOut() fires SIGNED_OUT with a null session, which clears this too --
    // but it can fail on a network error while the local state is cleared
    // regardless, and a stale token must not outlive the session.
    clearAuthToken();
    // A guest's conversation lives in a module-level Map, so it outlives this
    // component and would otherwise be visible to whoever signs in next.
    clearEphemeralStore();
    endGuestSession();
    setGuest(null);
    setUser(null);
    setProfile(null);
    setAuthPage('login');
    setMessages([]);
    setConversations([]);
    setCurrentConversationId(null);
    setRightPanelContent('empty');
    setRightPanelLinks([]);
    setCurrentPage('chat');
    setProjects([]);
    setProjectConversations({});
    setExpandedProjects({});
    setActiveProjectId(null);
  };

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0F172A]">
        <div className="text-white text-lg">Loading...</div>
      </div>
    );
  }

  // Derived from `principal`, not from authPage alone. onAuthStateChange
  // (:110) calls setAuthPage('login') on every session event supabase-js
  // produces -- including the null session a guest's tab reports -- so a gate
  // that only read authPage would throw a guest back to the login screen
  // mid-conversation. A guest has a principal, so these three are skipped.
  if (!principal && authPage === 'login') {
    return (
      <Login
        onLogin={handleLogin}
        onContinueAsGuest={handleContinueAsGuest}
        onSwitchToSignup={() => setAuthPage('signup')}
        authError={authError}
      />
    );
  }

  if (!principal && authPage === 'signup') {
    return (
      <Signup
        onSignup={handleSignup}
        onSwitchToLogin={() => setAuthPage('login')}
      />
    );
  }

  if (!principal && authPage === 'verify-email') {
    return (
      <VerifyEmail
        email={unverifiedEmail}
        onBackToLogin={() => setAuthPage('login')}
      />
    );
  }

  // Onboarding, gated on profile state rather than on authPage.
  //
  // Three properties this shape buys, all of which the authPage machine would
  // have cost:
  //   - It cannot be stomped. onAuthStateChange sets authPage(null) on every
  //     session event; this only ever *reads* authPage.
  //   - `profile === null` means "not loaded yet" and falls through to the app,
  //     so a slow or failed profile fetch degrades to today's behaviour instead
  //     of trapping someone on a blank gate. ensureProfile is fire-and-forget
  //     behind a 12s timeout whose catch only warns, so that path is real.
  //   - A guest has no `user`, so the gate is invisible to them by
  //     construction, with no extra condition.
  if (user && profile && !profile.onboarded_at) {
    return <Onboarding user={user} profile={profile} onDone={setProfile} />;
  }

  return (
    <div className="flex h-screen text-text-primary font-sans overflow-hidden transition-colors duration-300 bg-bg-main">
      <Sidebar
        startNewChat={startNewChat}
        isDarkMode={isDarkMode}
        setIsDarkMode={setIsDarkMode}
        onProfileClick={() => setCurrentPage('profile')}
        onInternAlertsClick={() => setCurrentPage('intern-alerts')}
        onLogout={handleLogout}
        user={user}
        capabilities={can}
        isGuest={principal?.kind === 'guest'}
        currentPage={currentPage}
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={openConversation}
        onRenameConversation={handleRenameConversation}
        onDeleteConversation={handleDeleteConversation}
        hasMoreConversations={hasMoreConversations}
        onLoadMoreConversations={loadMoreConversations}
        projects={projects}
        projectConversations={projectConversations}
        onCreateProject={handleCreateProject}
        onRenameProject={handleRenameProject}
        onDeleteProject={handleDeleteProject}
        onNewChatInProject={handleNewChatInProject}
        onToggleProject={handleToggleProject}
        expandedProjects={expandedProjects}
        onAssignToProject={handleAssignToProject}
        onRemoveFromProject={handleRemoveFromProject}
        onProjectBehaviorSettings={handleOpenProjectBehavior}
      />

      {/* Capability-checked as well as hidden in the sidebar: the entry points
          are gone for a guest, but currentPage is state that survives a
          principal change, so the page itself must refuse too. */}
      {currentPage === 'profile' && can.profile ? (
        <UserProfile
          onBack={() => setCurrentPage('chat')}
          user={user}
          audience={audience}
          onProfileChange={setProfile}
          behaviorSettings={behaviorSettings}
          onUpdateBehavior={handleUpdateBehavior}
          autoBehavior={autoBehavior}
        />
      ) : currentPage === 'intern-alerts' && can.internAlerts ? (
        <InternJobsAlertsPage onBack={() => setCurrentPage('chat')} />
      ) : (
        <>
          <MainChat
            audience={audience}
            messages={messages}
            input={input}
            setInput={setInput}
            handleSend={handleSend}
            isTyping={isTyping}
            streamStatus={streamStatus}
            messagesEndRef={messagesEndRef}
            selectedModel={selectedModel}
            setSelectedModel={setSelectedModel}
            hasMoreMessages={hasMoreMessages}
            loadingMessages={loadingMessages}
            onLoadOlderMessages={loadOlderMessages}
            onRegenerate={handleRegenerate}
            onEditAndResubmit={handleEditAndResubmit}
            onSuggestionClick={handleSuggestionClick}
            onFeedback={handleFeedback}
            onBehaviorSettings={handleOpenConversationBehavior}
            hasConversation={!!currentConversationId}
            activeBehaviorScope={activeBehaviorScope}
          />
          <RightPanel rightPanelContent={rightPanelContent} links={rightPanelLinks} />
        </>
      )}

      {/* Scoped Behavior Panel (project or conversation override) */}
      <ScopedBehaviorPanel
        open={scopedPanel.open}
        onClose={() => setScopedPanel(prev => ({ ...prev, open: false }))}
        scope={scopedPanel.scope}
        scopeLabel={scopedPanel.scopeLabel}
        scopeId={scopedPanel.scopeId}
        autoBehavior={autoBehavior || { ...DEFAULT_BEHAVIOR, ...(behaviorSettings || {}) }}
        scopedBehavior={scopedBehavior}
        onSave={handleSaveScopedBehavior}
        onDelete={handleDeleteScopedBehavior}
      />
    </div>
  );
}