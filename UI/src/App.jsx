import { useState, useRef, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import MainChat from './components/MainChat';
import RightPanel from './components/RightPanel';
import UserProfile from './components/UserProfile';
import InternJobsAlertsPage from './internAlerts/InternJobsAlertsPage.tsx';
import Login from './components/Login';
import Signup from './components/Signup';
import VerifyEmail from './components/VerifyEmail';
import { supabase } from './supabaseClient';
import { ensureProfile } from './supabaseHelpers';
import { runDueJobFetchCycle } from './services/jobFetcher';
import { sendMessage, generateTitle } from './services/llamaService';
import {
  fetchConversations,
  createConversation,
  updateConversation,
  renameConversation,
  deleteConversation,
  fetchMessages,
  insertMessage,
  deleteMessage,
  deleteMessagesAfter,
  autoTitleIfNeeded,
} from './services/chatService';
import { fetchBehaviorSettings, updateBehaviorSettings, resolveEffectiveBehavior, DEFAULT_BEHAVIOR } from './services/behaviorService';
import { insertFeedbackLog, updateFeedbackVote } from './services/feedbackLogService';
import { analyzeConversationState, adaptBehavior } from './services/conversationStateService';
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
  // Authentication state
  const [authPage, setAuthPage] = useState('login'); // 'login' | 'signup' | 'verify-email' | null
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState('');
  const [unverifiedEmail, setUnverifiedEmail] = useState('');

  // Check for existing session and listen for auth changes
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

        if (session?.user) {
          // Verify @sjsu.edu domain even for cached sessions
          if (!session.user.email?.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }

          // Check email verification (skip for OAuth users — they're already verified by Google)
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

          // Do not block initial app render on profile sync.
          withTimeout(ensureProfile(session.user), 'Profile check').catch((profileError) => {
            if (alive) {
              console.warn('Profile sync warning:', profileError?.message || profileError);
            }
          });
        } else if (alive) {
          setUser(null);
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

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      try {
        if (session?.user) {
          // Verify @sjsu.edu domain for OAuth logins
          if (session.user.email && !session.user.email.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }
          // Check email verification (skip for OAuth users)
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

          withTimeout(ensureProfile(session.user), 'Profile sync').catch((profileError) => {
            if (alive) {
              console.warn('Profile sync warning:', profileError?.message || profileError);
            }
          });
        } else if (alive) {
          setUser(null);
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

  // UI state
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [currentPage, setCurrentPage] = useState('chat');
  const [rightPanelContent, setRightPanelContent] = useState('empty');
  const [selectedModel, setSelectedModel] = useState('8b');

  // Chat / conversation state
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [hasMoreConversations, setHasMoreConversations] = useState(false);
  const messagesEndRef = useRef(null);
  const abortRef = useRef(null);

  // Project state
  const [projects, setProjects] = useState([]);
  const [projectConversations, setProjectConversations] = useState({}); // { projectId: [convos] }
  const [expandedProjects, setExpandedProjects] = useState({});
  const [activeProjectId, setActiveProjectId] = useState(null); // project context for new chats

  // Behavior settings
  const [behaviorSettings, setBehaviorSettings] = useState(null);

  // Apply dark mode class to html element
  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  // Job fetcher scheduler
  useEffect(() => {
    if (!user?.id) return undefined;

    const schedulerEnabled = import.meta.env.VITE_ENABLE_JOB_FETCHER_SCHEDULER === 'true';
    if (!schedulerEnabled) return undefined;

    const schedulerMs = Number(import.meta.env.VITE_JOB_FETCHER_SCHEDULER_MS || 300000);
    let running = false;

    const runCycle = async () => {
      if (running) return;
      running = true;
      try {
        await runDueJobFetchCycle({ userId: user.id });
      } catch (error) {
        console.error('Job scheduler cycle failed:', error?.message || error);
      } finally {
        running = false;
      }
    };

    runCycle();
    const intervalId = setInterval(runCycle, schedulerMs);
    return () => clearInterval(intervalId);
  }, [user?.id]);

  // ── Load conversations when user logs in ──────────────────
  const loadConversations = useCallback(async (cursor = null) => {
    if (!user?.id) return;
    try {
      const data = await fetchConversations({ limit: 20, cursor });
      if (cursor) {
        setConversations(prev => [...prev, ...data]);
      } else {
        setConversations(data);
      }
      setHasMoreConversations(data.length === 20);
    } catch (err) {
      console.error('Failed to load conversations:', err.message);
    }
  }, [user?.id]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const loadMoreConversations = useCallback(() => {
    if (!hasMoreConversations || !conversations.length) return;
    const oldest = conversations[conversations.length - 1];
    loadConversations(oldest.updated_at);
  }, [hasMoreConversations, conversations, loadConversations]);

  // ── Load projects when user logs in ──────────────────────
  const loadProjects = useCallback(async () => {
    if (!user?.id) return;
    try {
      const data = await fetchProjects();
      setProjects(data);
    } catch (err) {
      console.error('Failed to load projects:', err.message);
    }
  }, [user?.id]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  // ── Load behavior settings when user logs in ────────────
  useEffect(() => {
    if (!user?.id) return;
    fetchBehaviorSettings(user.id)
      .then(setBehaviorSettings)
      .catch(err => {
        console.warn('Failed to load behavior settings, using defaults:', err.message);
        setBehaviorSettings({ ...DEFAULT_BEHAVIOR });
      });
  }, [user?.id]);

  const handleUpdateBehavior = async (updates) => {
    if (!user?.id) return;
    try {
      const updated = await updateBehaviorSettings(user.id, updates);
      setBehaviorSettings(updated);
    } catch (err) {
      console.error('Failed to update behavior settings:', err.message);
    }
  };

  const loadProjectConvos = useCallback(async (projectId) => {
    try {
      const convos = await fetchProjectConversations(projectId);
      setProjectConversations(prev => ({ ...prev, [projectId]: convos }));
    } catch (err) {
      console.error('Failed to load project conversations:', err.message);
    }
  }, []);

  // ── Project handlers ──────────────────────────────────────
  const handleCreateProject = async (name) => {
    if (!user?.id) return;
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
      // Reload standalone conversations — unlinked convos will appear there
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
    setCurrentPage('chat');
  };

  const handleAssignToProject = async (conversationId, projectId) => {
    try {
      await assignConversationToProject(conversationId, projectId);
      // Remove from standalone list
      setConversations(prev => prev.filter(c => c.id !== conversationId));
      // Refresh project's conversation list
      loadProjectConvos(projectId);
      // Expand the target project
      setExpandedProjects(prev => ({ ...prev, [projectId]: true }));
    } catch (err) {
      console.error('Assign to project failed:', err.message);
    }
  };

  const handleRemoveFromProject = async (conversationId) => {
    try {
      await unassignConversationFromProject(conversationId);
      // Remove from all project conversation lists
      setProjectConversations(prev => {
        const next = {};
        for (const [pid, convos] of Object.entries(prev)) {
          next[pid] = convos.filter(c => c.id !== conversationId);
        }
        return next;
      });
      // Reload standalone conversations
      loadConversations();
    } catch (err) {
      console.error('Remove from project failed:', err.message);
    }
  };

  // ── Open a conversation ───────────────────────────────────
  const openConversation = useCallback(async (conversationId) => {
    setCurrentConversationId(conversationId);
    setMessages([]);
    setCurrentPage('chat');
    setLoadingMessages(true);

    try {
      const msgs = await fetchMessages({ conversationId, limit: 30 });
      const mapped = msgs.map(m => ({
        id: m.id,
        text: m.content,
        sender: m.role === 'user' ? 'user' : 'bot',
        created_at: m.created_at,
      }));
      setMessages(mapped);
      setHasMoreMessages(msgs.length === 30);
      if (mapped.length > 0) setRightPanelContent('links');
    } catch (err) {
      console.error('Failed to load messages:', err.message);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  // ── Load older messages (scroll up) ───────────────────────
  const loadOlderMessages = useCallback(async () => {
    if (!currentConversationId || !hasMoreMessages || loadingMessages) return;
    setLoadingMessages(true);

    try {
      const oldest = messages[0];
      const cursor = oldest?.created_at || null;
      const older = await fetchMessages({ conversationId: currentConversationId, limit: 30, cursor });
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
  }, [currentConversationId, hasMoreMessages, loadingMessages, messages]);

  // Scroll to bottom when new messages arrive (not when loading older)
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (!loadingMessages) scrollToBottom();
  }, [messages, loadingMessages]);

  // ── Send a message ────────────────────────────────────────
  const handleSend = async () => {
    if (!input.trim() || !user?.id) return;

    const userText = input.trim();
    setInput('');
    setIsTyping(true);
    setRightPanelContent('links');

    try {
      // Create conversation on first message if needed
      let convoId = currentConversationId;
      if (!convoId) {
        const convo = await createConversation(user.id, null, activeProjectId);
        convoId = convo.id;
        setCurrentConversationId(convoId);
        // Add to sidebar — either in project or standalone
        if (activeProjectId) {
          setProjectConversations(prev => ({
            ...prev,
            [activeProjectId]: [convo, ...(prev[activeProjectId] || [])],
          }));
        } else {
          setConversations(prev => [convo, ...prev]);
        }
      }

      // Persist user message
      const userRow = await insertMessage({ conversationId: convoId, role: 'user', content: userText });
      const userMsg = { id: userRow.id, text: userText, sender: 'user', created_at: userRow.created_at };
      setMessages(prev => [...prev, userMsg]);

      // Auto-title from first user message
      autoTitleIfNeeded(convoId, userText, generateTitle).then(() => loadConversations()).catch(() => {});

      // Build context for LLM (last 20 messages)
      const currentMessages = [...messages, userMsg];
      const context = currentMessages.slice(-20).map(m => ({
        role: m.sender === 'user' ? 'user' : 'assistant',
        content: m.text,
      }));

      // Placeholder for streaming bot response
      const tempId = `temp-${Date.now()}`;
      setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);

      // Abort any in-flight request
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Resolve scoped behavior (conversation > project > user > defaults)
      const effectiveBehavior = await resolveEffectiveBehavior(
        user.id,
        activeProjectId,
        convoId,
      ).catch(() => behaviorSettings);
      const adaptedBehavior = adaptBehavior(effectiveBehavior, analyzeConversationState(context));

      let fullResponse = '';
      const validatorMeta = await sendMessage({
        messages: context,
        model: selectedModel,
        signal: controller.signal,
        behavior: adaptedBehavior,
        onChunk: (chunk) => {
          fullResponse += chunk;
          setMessages(prev =>
            prev.map(m => m.id === tempId ? { ...m, text: m.text + chunk } : m)
          );
        },
        onReplace: (text) => {
          fullResponse = text;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text } : m));
        },
      });

      // Persist assistant message
      const assistantRow = await insertMessage({ conversationId: convoId, role: 'assistant', content: fullResponse });

      // Fire-and-forget feedback log entry for this response
      insertFeedbackLog({
        responseId:       assistantRow.id,
        userId:           user.id,
        conversationId:   convoId,
        behaviorSnapshot: adaptedBehavior,
        validatorsRun:    validatorMeta?.validatorsRun    ?? [],
        validatorsPassed: validatorMeta?.validatorsPassed ?? true,
        repairsApplied:   validatorMeta?.repairsApplied   ?? [],
        modelUsed:        selectedModel,
      }).catch(() => {});

      // Replace temp message with persisted one
      setMessages(prev =>
        prev.map(m => m.id === tempId ? { ...m, id: assistantRow.id, created_at: assistantRow.created_at } : m)
      );

      // Refresh sidebar to reflect updated preview/time
      loadConversations();
    } catch (err) {
      if (err.name === 'AbortError') return;
      // Show error in the last bot message
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.sender === 'bot') {
          return prev.map(m => m.id === last.id ? { ...m, text: `**Error:** ${err.message}` } : m);
        }
        return [...prev, { id: `err-${Date.now()}`, text: `**Error:** ${err.message}`, sender: 'bot' }];
      });
    } finally {
      setIsTyping(false);
      abortRef.current = null;
    }
  };

  // ── Regenerate last assistant response ─────────────────────
  const handleRegenerate = async () => {
    if (!currentConversationId || !user?.id || isTyping) return;

    // Find the last bot message and the user message before it
    const lastBotIdx = [...messages].reverse().findIndex(m => m.sender === 'bot');
    if (lastBotIdx === -1) return;
    const botIdx = messages.length - 1 - lastBotIdx;
    const botMsg = messages[botIdx];

    // Find the last user message before this bot message
    let userMsg = null;
    for (let i = botIdx - 1; i >= 0; i--) {
      if (messages[i].sender === 'user') { userMsg = messages[i]; break; }
    }
    if (!userMsg) return;

    // Delete the bot message from DB (if it's persisted, not temp)
    if (botMsg.id && !String(botMsg.id).startsWith('temp-') && !String(botMsg.id).startsWith('err-')) {
      try { await deleteMessage(botMsg.id); } catch {}
    }

    // Remove bot message from local state
    setMessages(prev => prev.filter(m => m.id !== botMsg.id));
    setIsTyping(true);

    // Build context
    const context = messages
      .slice(0, botIdx)
      .slice(-20)
      .map(m => ({ role: m.sender === 'user' ? 'user' : 'assistant', content: m.text }));

    const tempId = `temp-${Date.now()}`;
    setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // Resolve scoped behavior for the current conversation/project
      const effectiveBehavior = await resolveEffectiveBehavior(
        user.id,
        activeProjectId,
        currentConversationId,
      ).catch(() => behaviorSettings);
      const adaptedBehavior = adaptBehavior(effectiveBehavior, analyzeConversationState(context));

      let fullResponse = '';
      const validatorMeta = await sendMessage({
        messages: context,
        model: selectedModel,
        signal: controller.signal,
        behavior: adaptedBehavior,
        onChunk: (chunk) => {
          fullResponse += chunk;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text: m.text + chunk } : m));
        },
        onReplace: (text) => {
          fullResponse = text;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text } : m));
        },
      });

      const assistantRow = await insertMessage({ conversationId: currentConversationId, role: 'assistant', content: fullResponse });

      insertFeedbackLog({
        responseId:       assistantRow.id,
        userId:           user.id,
        conversationId:   currentConversationId,
        behaviorSnapshot: adaptedBehavior,
        validatorsRun:    validatorMeta?.validatorsRun    ?? [],
        validatorsPassed: validatorMeta?.validatorsPassed ?? true,
        repairsApplied:   validatorMeta?.repairsApplied   ?? [],
        modelUsed:        selectedModel,
      }).catch(() => {});

      setMessages(prev => prev.map(m => m.id === tempId ? { ...m, id: assistantRow.id, created_at: assistantRow.created_at } : m));
      loadConversations();
    } catch (err) {
      if (err.name === 'AbortError') return;
      setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text: `**Error:** ${err.message}` } : m));
    } finally {
      setIsTyping(false);
      abortRef.current = null;
    }
  };

  // ── Edit a previous user message and resubmit ────────────
  const handleEditAndResubmit = async (msgId, newText) => {
    if (!currentConversationId || !user?.id || isTyping) return;

    const msgIdx = messages.findIndex(m => m.id === msgId);
    if (msgIdx === -1) return;
    const originalMsg = messages[msgIdx];

    // Delete all messages from this point onward in DB
    if (originalMsg.created_at) {
      try { await deleteMessagesAfter(currentConversationId, originalMsg.created_at); } catch {}
    }

    // Truncate local messages up to (not including) the edited message
    const preceding = messages.slice(0, msgIdx);
    setMessages(preceding);
    setIsTyping(true);

    try {
      // Insert the edited user message
      const userRow = await insertMessage({ conversationId: currentConversationId, role: 'user', content: newText });
      const userMsg = { id: userRow.id, text: newText, sender: 'user', created_at: userRow.created_at };
      setMessages(prev => [...prev, userMsg]);

      // Build context
      const context = [...preceding, userMsg].slice(-20).map(m => ({
        role: m.sender === 'user' ? 'user' : 'assistant',
        content: m.text,
      }));

      const tempId = `temp-${Date.now()}`;
      setMessages(prev => [...prev, { id: tempId, text: '', sender: 'bot' }]);

      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Resolve scoped behavior for the current conversation/project
      const effectiveBehavior = await resolveEffectiveBehavior(
        user.id,
        activeProjectId,
        currentConversationId,
      ).catch(() => behaviorSettings);
      const adaptedBehavior = adaptBehavior(effectiveBehavior, analyzeConversationState(context));

      let fullResponse = '';
      const validatorMeta = await sendMessage({
        messages: context,
        model: selectedModel,
        signal: controller.signal,
        behavior: adaptedBehavior,
        onChunk: (chunk) => {
          fullResponse += chunk;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text: m.text + chunk } : m));
        },
        onReplace: (text) => {
          fullResponse = text;
          setMessages(prev => prev.map(m => m.id === tempId ? { ...m, text } : m));
        },
      });

      const assistantRow = await insertMessage({ conversationId: currentConversationId, role: 'assistant', content: fullResponse });

      insertFeedbackLog({
        responseId:       assistantRow.id,
        userId:           user.id,
        conversationId:   currentConversationId,
        behaviorSnapshot: adaptedBehavior,
        validatorsRun:    validatorMeta?.validatorsRun    ?? [],
        validatorsPassed: validatorMeta?.validatorsPassed ?? true,
        repairsApplied:   validatorMeta?.repairsApplied   ?? [],
        modelUsed:        selectedModel,
      }).catch(() => {});

      setMessages(prev => prev.map(m => m.id === tempId ? { ...m, id: assistantRow.id, created_at: assistantRow.created_at } : m));
      loadConversations();
    } catch (err) {
      if (err.name === 'AbortError') return;
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.sender === 'bot') {
          return prev.map(m => m.id === last.id ? { ...m, text: `**Error:** ${err.message}` } : m);
        }
        return [...prev, { id: `err-${Date.now()}`, text: `**Error:** ${err.message}`, sender: 'bot' }];
      });
    } finally {
      setIsTyping(false);
      abortRef.current = null;
    }
  };

  // ── Suggestion click (from empty state or follow-up chips)─
  const handleSuggestionClick = (text) => {
    setInput(text);
  };

  // ── Feedback vote (thumbs-up / thumbs-down on bot messages) ──────────────
  const handleFeedback = useCallback((msgId, type) => {
    if (!user?.id || !type) return;
    updateFeedbackVote(msgId, user.id, type).catch(() => {});
  }, [user?.id]);

  // ── New chat ──────────────────────────────────────────────
  const startNewChat = () => {
    setCurrentConversationId(null);
    setActiveProjectId(null);
    setMessages([]);
    setRightPanelContent('empty');
    setCurrentPage('chat');
  };

  // ── Rename / Delete ───────────────────────────────────────
  const handleRenameConversation = async (convoId, newTitle) => {
    try {
      await renameConversation(convoId, newTitle);
      setConversations(prev =>
        prev.map(c => c.id === convoId ? { ...c, title: newTitle } : c)
      );
    } catch (err) {
      console.error('Rename failed:', err.message);
    }
  };

  const handleDeleteConversation = async (convoId) => {
    try {
      await deleteConversation(convoId);
      setConversations(prev => prev.filter(c => c.id !== convoId));
      // Also remove from project conversation lists
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
      }
    } catch (err) {
      console.error('Delete failed:', err.message);
    }
  };

  // ── Auth handlers ─────────────────────────────────────────
  const handleLogin = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  const handleSignup = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    setUser(null);
    setAuthPage('login');
    setMessages([]);
    setConversations([]);
    setCurrentConversationId(null);
    setRightPanelContent('empty');
    setCurrentPage('chat');
    setProjects([]);
    setProjectConversations({});
    setExpandedProjects({});
    setActiveProjectId(null);
  };

  // Show loading while checking session
  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0F172A]">
        <div className="text-white text-lg">Loading...</div>
      </div>
    );
  }

  // Show auth pages if not logged in
  if (authPage === 'login') {
    return <Login onLogin={handleLogin} onSwitchToSignup={() => setAuthPage('signup')} authError={authError} />;
  }
  if (authPage === 'signup') {
    return <Signup onSignup={handleSignup} onSwitchToLogin={() => setAuthPage('login')} />;
  }
  if (authPage === 'verify-email') {
    return <VerifyEmail email={unverifiedEmail} onBackToLogin={() => setAuthPage('login')} />;
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
      />
      
      {currentPage === 'profile' ? (
        <UserProfile
          onBack={() => setCurrentPage('chat')}
          user={user}
          behaviorSettings={behaviorSettings}
          onUpdateBehavior={handleUpdateBehavior}
        />
      ) : currentPage === 'intern-alerts' ? (
        <InternJobsAlertsPage onBack={() => setCurrentPage('chat')} />
      ) : (
        <>
          <MainChat
            messages={messages}
            input={input}
            setInput={setInput}
            handleSend={handleSend}
            isTyping={isTyping}
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
          />
          <RightPanel 
            rightPanelContent={rightPanelContent} 
          />
        </>
      )}
    </div>
  );
}
