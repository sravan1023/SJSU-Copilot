import { useState, useEffect } from 'react';
import { X, Globe, FolderOpen, MessageSquare, Check, ChevronDown, Trash2 } from 'lucide-react';
import BehaviorSettings from './BehaviorSettings';
import PrioritySettings from './PrioritySettings';

const SCOPE_META = {
  user:         { label: 'Global (Default)', icon: Globe,         color: 'text-blue-500',    bg: 'bg-blue-500/10',  border: 'border-blue-500/30',  desc: 'Applies to all conversations unless overridden' },
  project:      { label: 'Project',          icon: FolderOpen,    color: 'text-sjsu-gold',   bg: 'bg-sjsu-gold/10', border: 'border-sjsu-gold/30', desc: 'Overrides global for all chats in this project' },
  conversation: { label: 'This Chat',        icon: MessageSquare, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', desc: 'Overrides everything for this conversation only' },
};

/**
 * ScopedBehaviorPanel — slide-over panel for configuring behavior at
 * project or conversation level, with a clear scope indicator.
 *
 * Props:
 *   open           - boolean, whether the panel is visible
 *   onClose        - () => void
 *   scope          - 'project' | 'conversation'
 *   scopeLabel     - display name, e.g. project name or conversation title
 *   scopeId        - the project_id or conversation_id
 *   userId         - current user ID
 *   globalBehavior - user-level behavior settings (fallback display)
 *   scopedBehavior - the override for this scope (null if none set)
 *   onSave         - (updates) => Promise<void>  — upsert scoped behavior
 *   onDelete       - () => Promise<void>          — remove the override
 */
export default function ScopedBehaviorPanel({
  open,
  onClose,
  scope,
  scopeLabel,
  scopeId,
  globalBehavior,
  scopedBehavior,
  onSave,
  onDelete,
}) {
  const [tab, setTab] = useState('style'); // 'style' | 'priorities'
  const [localSettings, setLocalSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const meta = SCOPE_META[scope] || SCOPE_META.conversation;
  const ScopeIcon = meta.icon;
  const hasOverride = scopedBehavior != null;

  // Sync local state when panel opens or scoped data changes
  useEffect(() => {
    if (open) {
      setLocalSettings(scopedBehavior ? { ...scopedBehavior } : { ...globalBehavior });
      setTab('style');
    }
  }, [open, scopeId, scopedBehavior, globalBehavior]);

  if (!open || !localSettings) return null;

  const handleUpdate = async (updates) => {
    const next = { ...localSettings, ...updates };
    setLocalSettings(next);
    setSaving(true);
    try {
      await onSave(updates);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await onDelete();
      onClose();
    } finally {
      setDeleting(false);
    }
  };

  // Determine which scope is actually active for display
  const activeScope = hasOverride ? scope : 'user';
  const activeMeta = SCOPE_META[activeScope];
  const ActiveIcon = activeMeta.icon;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 z-40 transition-opacity"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-lg bg-bg-main border-l border-border-color z-50 flex flex-col shadow-2xl animate-slide-in-right">
        {/* Header */}
        <div className="px-6 py-5 border-b border-border-color">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-bold text-text-primary">Behavior Override</h2>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scope badge */}
          <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium ${meta.bg} ${meta.border} border`}>
            <ScopeIcon size={14} className={meta.color} />
            <span className={meta.color}>{meta.label}</span>
            <span className="text-text-secondary">—</span>
            <span className="text-text-primary truncate max-w-[200px]">{scopeLabel || 'Untitled'}</span>
          </div>

          {/* Active scope indicator */}
          <div className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
            <span>Currently using:</span>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${activeMeta.bg} ${activeMeta.color} font-medium`}>
              <ActiveIcon size={10} />
              {activeMeta.label}
            </span>
            {!hasOverride && (
              <span className="italic">(no override set — using global defaults)</span>
            )}
          </div>
        </div>

        {/* Tab switcher */}
        <div className="px-6 pt-4 pb-0">
          <div className="flex bg-bg-surface border border-border-color rounded-lg p-0.5 gap-0.5">
            <button
              onClick={() => setTab('style')}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors ${
                tab === 'style'
                  ? 'bg-white dark:bg-bg-hover text-text-primary shadow-sm'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              Response Style
            </button>
            <button
              onClick={() => setTab('priorities')}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors ${
                tab === 'priorities'
                  ? 'bg-white dark:bg-bg-hover text-text-primary shadow-sm'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              Priorities
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {/* Inheritance hint */}
          {!hasOverride && (
            <div className="mb-6 px-4 py-3 rounded-lg bg-blue-500/5 border border-blue-500/20 text-sm text-text-secondary">
              No override exists for this {scope === 'project' ? 'project' : 'conversation'} yet.
              Changing any setting below will create one. Until then, global defaults apply.
            </div>
          )}

          {tab === 'style' ? (
            <BehaviorSettings
              settings={localSettings}
              onUpdate={handleUpdate}
            />
          ) : (
            <PrioritySettings
              stack={localSettings?.priority_stack}
              onUpdate={handleUpdate}
            />
          )}
        </div>

        {/* Footer */}
        {hasOverride && (
          <div className="px-6 py-4 border-t border-border-color">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-red-500 hover:bg-red-500/10 border border-red-500/20 hover:border-red-500/40 transition-colors disabled:opacity-50"
            >
              <Trash2 size={14} />
              {deleting ? 'Removing...' : 'Remove Override'}
              <span className="text-xs text-text-secondary font-normal ml-1">(revert to {scope === 'conversation' ? 'project or ' : ''}global defaults)</span>
            </button>
          </div>
        )}
      </div>
    </>
  );
}
