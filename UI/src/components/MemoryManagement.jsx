import { useState, useEffect, useCallback } from 'react';
import {
  Trash2, Archive, RotateCcw, Search, X, Edit3, Check,
  RefreshCw, ChevronDown, ChevronUp, Database, Sparkles,
} from 'lucide-react';
import { supabase } from '../supabaseClient';

// ── Helpers ───────────────────────────────────────────────
const SCOPE_LABELS = { global: 'Global', project: 'Project', conversation: 'Chat' };
const CATEGORY_LABELS = {
  preference: 'Preference', decision: 'Decision', constraint: 'Constraint',
  fact: 'Fact', task: 'Task', context: 'Context',
};

const SCOPE_COLORS = {
  global:       'bg-blue-500/10 text-blue-600 dark:text-blue-400 ring-blue-500/20',
  project:      'bg-purple-500/10 text-purple-600 dark:text-purple-400 ring-purple-500/20',
  conversation: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 ring-emerald-500/20',
};

const CATEGORY_COLORS = {
  preference: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  decision:   'bg-sky-500/10 text-sky-600 dark:text-sky-400',
  constraint: 'bg-red-500/10 text-red-600 dark:text-red-400',
  fact:       'bg-slate-500/10 text-slate-600 dark:text-slate-400',
  task:       'bg-orange-500/10 text-orange-600 dark:text-orange-400',
  context:    'bg-teal-500/10 text-teal-600 dark:text-teal-400',
};

function Tag({ text, className }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${className}`}>
      {text}
    </span>
  );
}

function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

// ── Memory row ────────────────────────────────────────────
function MemoryRow({ memory, projectName, onArchive, onRestore, onDelete, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(memory.content);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const isLong = memory.content.length > 140;
  const isArchived = memory.status === 'archived';

  const handleSave = async () => {
    if (!editContent.trim() || editContent.trim() === memory.content) {
      setEditing(false);
      return;
    }
    setSaving(true);
    await onEdit(memory.id, editContent.trim());
    setSaving(false);
    setEditing(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSave();
    if (e.key === 'Escape') { setEditing(false); setEditContent(memory.content); }
  };

  return (
    <div className={`group relative transition-opacity ${isArchived ? 'opacity-50' : ''}`}>
      <div className="flex gap-3 py-3">
        {/* Importance bar */}
        <div className="flex flex-col items-center pt-1 shrink-0" title={`Importance: ${memory.importance}/10`}>
          <div className="w-1 rounded-full bg-border-color overflow-hidden" style={{ height: '40px' }}>
            <div
              className="w-full rounded-full bg-sjsu-gold transition-all"
              style={{ height: `${memory.importance * 10}%` , marginTop: `${100 - memory.importance * 10}%` }}
            />
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="space-y-2">
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                onKeyDown={handleKeyDown}
                className="w-full bg-bg-main border border-border-color rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-sjsu-gold/40 focus:border-sjsu-gold resize-none transition-all"
                rows={2}
                autoFocus
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-sjsu-gold hover:bg-sjsu-gold-hover text-white text-xs font-semibold transition-colors disabled:opacity-50"
                >
                  <Check size={10} />
                  {saving ? 'Saving...' : 'Save'}
                </button>
                <button
                  onClick={() => { setEditing(false); setEditContent(memory.content); }}
                  className="px-2.5 py-1 rounded-md text-xs text-text-secondary hover:bg-bg-hover transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-text-primary leading-relaxed">
              {isLong && !expanded ? `${memory.content.slice(0, 140)}...` : memory.content}
              {isLong && (
                <button
                  onClick={() => setExpanded(v => !v)}
                  className="inline-flex items-center gap-0.5 ml-1 text-xs text-text-secondary hover:text-text-primary transition-colors"
                >
                  {expanded ? <><ChevronUp size={10} /> less</> : <><ChevronDown size={10} /> more</>}
                </button>
              )}
            </p>
          )}

          {/* Meta row */}
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <Tag text={SCOPE_LABELS[memory.scope] || memory.scope} className={SCOPE_COLORS[memory.scope]} />
            <Tag text={CATEGORY_LABELS[memory.category] || memory.category} className={CATEGORY_COLORS[memory.category]} />
            {memory.scope === 'project' && projectName && (
              <span className="text-[10px] text-text-secondary">{projectName}</span>
            )}
            <span className="text-[10px] text-text-secondary ml-auto">{timeAgo(memory.created_at)}</span>
          </div>
        </div>

        {/* Actions — show on hover */}
        <div className="flex items-start gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity pt-0.5">
          {!editing && !isArchived && (
            <button
              onClick={() => { setEditing(true); setEditContent(memory.content); }}
              className="p-1 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
              title="Edit"
            >
              <Edit3 size={13} />
            </button>
          )}
          {!isArchived ? (
            <button
              onClick={() => onArchive(memory.id)}
              className="p-1 rounded-md text-text-secondary hover:text-amber-600 hover:bg-amber-500/10 transition-colors"
              title="Archive"
            >
              <Archive size={13} />
            </button>
          ) : (
            <button
              onClick={() => onRestore(memory.id)}
              className="p-1 rounded-md text-text-secondary hover:text-emerald-600 hover:bg-emerald-500/10 transition-colors"
              title="Restore"
            >
              <RotateCcw size={13} />
            </button>
          )}
          {confirmDelete ? (
            <div className="flex items-center gap-0.5">
              <button
                onClick={() => onDelete(memory.id)}
                className="p-1 rounded-md text-red-600 hover:bg-red-500/10 transition-colors"
                title="Confirm"
              >
                <Check size={13} />
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="p-1 rounded-md text-text-secondary hover:bg-bg-hover transition-colors"
                title="Cancel"
              >
                <X size={13} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="p-1 rounded-md text-text-secondary hover:text-red-600 hover:bg-red-500/10 transition-colors"
              title="Delete"
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────
export default function MemoryManagement({ user }) {
  const [memories, setMemories] = useState([]);
  const [projectNames, setProjectNames] = useState({});
  const [loading, setLoading] = useState(true);
  const [scopeFilter, setScopeFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('active');
  const [searchQuery, setSearchQuery] = useState('');
  const [confirmClearAll, setConfirmClearAll] = useState(false);

  const loadMemories = useCallback(async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const { data, error } = await supabase
        .from('memories')
        .select('*')
        .eq('user_id', user.id)
        .order('importance', { ascending: false })
        .order('created_at', { ascending: false });
      if (error) throw error;
      setMemories(data || []);

      const projectIds = [...new Set((data || []).filter(m => m.project_id).map(m => m.project_id))];
      if (projectIds.length > 0) {
        const { data: pData } = await supabase.from('projects').select('id, name').in('id', projectIds);
        const map = {};
        (pData || []).forEach(p => { map[p.id] = p.name; });
        setProjectNames(map);
      }
    } catch (err) {
      console.error('Failed to load memories:', err.message);
    } finally {
      setLoading(false);
    }
  }, [user?.id]);

  useEffect(() => { loadMemories(); }, [loadMemories]);

  const handleArchive = async (id) => {
    const { error } = await supabase.from('memories').update({ status: 'archived' }).eq('id', id);
    if (!error) setMemories(prev => prev.map(m => m.id === id ? { ...m, status: 'archived' } : m));
  };
  const handleRestore = async (id) => {
    const { error } = await supabase.from('memories').update({ status: 'active' }).eq('id', id);
    if (!error) setMemories(prev => prev.map(m => m.id === id ? { ...m, status: 'active' } : m));
  };
  const handleDelete = async (id) => {
    const { error } = await supabase.from('memories').delete().eq('id', id);
    if (!error) setMemories(prev => prev.filter(m => m.id !== id));
  };
  const handleEdit = async (id, newContent) => {
    const { error } = await supabase
      .from('memories')
      .update({ content: newContent, updated_at: new Date().toISOString() })
      .eq('id', id);
    if (!error) setMemories(prev => prev.map(m => m.id === id ? { ...m, content: newContent } : m));
  };
  const handleClearAll = async () => {
    const ids = memories.filter(m => m.status === 'active').map(m => m.id);
    if (!ids.length) return;
    const { error } = await supabase.from('memories').delete().in('id', ids);
    if (!error) {
      setMemories(prev => prev.filter(m => m.status !== 'active'));
      setConfirmClearAll(false);
    }
  };

  const filtered = memories.filter(m => {
    if (scopeFilter !== 'all' && m.scope !== scopeFilter) return false;
    if (statusFilter !== 'all' && m.status !== statusFilter) return false;
    if (searchQuery && !m.content.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    return true;
  });

  const activeCount = memories.filter(m => m.status === 'active').length;

  const pillClass = (active) =>
    `px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
      active
        ? 'bg-sjsu-gold/10 text-sjsu-gold ring-1 ring-sjsu-gold/30'
        : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
    }`;

  // ── Render ──────────────────────────────────────────────
  return (
    <div className="space-y-5">
      {/* Toolbar */}
      <div className="space-y-3">
        {/* Search */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary pointer-events-none" />
            <input
              type="text"
              placeholder="Search memories..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-bg-main border border-border-color rounded-lg pl-9 pr-8 py-2 text-sm text-text-primary placeholder-text-secondary focus:outline-none focus:ring-2 focus:ring-sjsu-gold/40 focus:border-sjsu-gold transition-all"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-secondary hover:text-text-primary">
                <X size={14} />
              </button>
            )}
          </div>
          <button
            onClick={loadMemories}
            className="p-2 rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
            title="Refresh"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* Filters row */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1 bg-bg-main rounded-lg p-0.5">
            {['all', 'global', 'project', 'conversation'].map(s => (
              <button key={s} onClick={() => setScopeFilter(s)} className={pillClass(scopeFilter === s)}>
                {s === 'all' ? 'All' : s === 'conversation' ? 'Chat' : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
          <div className="w-px h-5 bg-border-color" />
          <div className="flex items-center gap-1 bg-bg-main rounded-lg p-0.5">
            {[
              { key: 'active', label: 'Active' },
              { key: 'archived', label: 'Archived' },
              { key: 'all', label: 'All' },
            ].map(({ key, label }) => (
              <button key={key} onClick={() => setStatusFilter(key)} className={pillClass(statusFilter === key)}>
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1" />
          {activeCount > 0 && (
            confirmClearAll ? (
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-red-500 font-medium">Delete all?</span>
                <button onClick={handleClearAll} className="px-2 py-1 rounded-md bg-red-600 text-white text-xs font-semibold hover:bg-red-700 transition-colors">Yes</button>
                <button onClick={() => setConfirmClearAll(false)} className="px-2 py-1 rounded-md text-xs text-text-secondary hover:bg-bg-hover transition-colors">No</button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmClearAll(true)}
                className="text-xs text-text-secondary hover:text-red-500 transition-colors"
              >
                Clear all
              </button>
            )
          )}
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="space-y-3 animate-pulse">
          {[1, 2, 3].map(i => (
            <div key={i} className="flex gap-3 py-3">
              <div className="w-1 h-10 bg-border-color rounded-full" />
              <div className="flex-1 space-y-2">
                <div className="h-3.5 bg-border-color rounded w-3/4" />
                <div className="h-3 bg-border-color/60 rounded w-1/2" />
              </div>
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center py-16 text-center">
          <div className="w-14 h-14 rounded-2xl bg-bg-surface border border-border-color flex items-center justify-center mb-4">
            {memories.length === 0 ? (
              <Sparkles size={24} className="text-text-secondary/40" />
            ) : (
              <Database size={24} className="text-text-secondary/40" />
            )}
          </div>
          <p className="text-sm font-semibold text-text-primary">
            {memories.length === 0 ? 'No memories yet' : 'Nothing matches'}
          </p>
          <p className="text-xs text-text-secondary mt-1 max-w-xs">
            {memories.length === 0
              ? 'As you chat, Copilot will automatically remember important details about your preferences and context.'
              : 'Try adjusting your scope or status filters above.'}
          </p>
        </div>
      ) : (
        <>
          <div className="text-[11px] text-text-secondary">
            {filtered.length} {filtered.length === 1 ? 'memory' : 'memories'}
          </div>
          <div className="divide-y divide-border-color/50">
            {filtered.map(memory => (
              <MemoryRow
                key={memory.id}
                memory={memory}
                projectName={memory.project_id ? projectNames[memory.project_id] : null}
                onArchive={handleArchive}
                onRestore={handleRestore}
                onDelete={handleDelete}
                onEdit={handleEdit}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
