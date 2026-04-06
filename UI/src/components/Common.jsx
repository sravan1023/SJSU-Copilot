import { useState, useRef, useEffect } from 'react';
import { MessageSquare, Link as LinkIcon, Pencil, Trash2, Check, X } from 'lucide-react';

export function SuggestionCard({ title, subtitle, onClick }) {
    return (
        <button onClick={onClick} className="bg-bg-surface hover:bg-bg-hover border border-border-color px-5 py-5 rounded-xl text-left transition-colors h-24 flex flex-col justify-center shadow-sm w-full">
            <h4 className="font-semibold text-text-primary text-sm mb-1">{title}</h4>
            <p className="text-xs text-text-secondary leading-snug">{subtitle}</p>
        </button>
    );
}

export function FollowUpChip({ text }) {
    return (
        <button className="bg-transparent hover:bg-bg-hover text-text-primary border border-border-color px-4 py-2 rounded-full text-xs font-medium transition-colors">
            {text}
        </button>
    );
}

export function LinkItem({ label, isFirst = false }) {
    return (
        <div className={`flex items-center justify-between px-4 py-3.5 bg-bg-surface hover:bg-bg-hover transition-colors cursor-pointer group ${!isFirst ? 'border-t border-border-color' : ''}`}>
            <span className="text-sm underline text-text-primary font-medium group-hover:text-black dark:group-hover:text-white">{label}</span>
            <LinkIcon size={14} className="text-sjsu-gold group-hover:text-black dark:group-hover:text-white" />
        </div>
    );
}

export function SidebarToolItem({ icon, label, active = false, onClick }) {
    return (
        <button
            onClick={onClick}
            className={`w-full flex items-center gap-3 px-2 py-2 rounded-lg cursor-pointer transition-colors group ${
                active ? 'bg-white/15' : 'hover:bg-white/10'
            }`}
        >
            <div className={`transition-colors ${active ? 'text-white' : 'text-sidebar-text-muted group-hover:text-white'}`}>
                {icon}
            </div>
            <span className="text-sm text-sidebar-text-main font-medium">{label}</span>
        </button>
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

export function HistoryItem({ id, title, preview, updatedAt, active, onClick, onRename, onDelete }) {
    const [renaming, setRenaming] = useState(false);
    const [renameValue, setRenameValue] = useState(title || '');
    const inputRef = useRef(null);

    useEffect(() => {
        if (renaming) inputRef.current?.focus();
    }, [renaming]);

    const submitRename = () => {
        const trimmed = renameValue.trim();
        if (trimmed && trimmed !== title) onRename?.(id, trimmed);
        setRenaming(false);
    };

    const displayTitle = title || preview || 'New Chat';

    return (
        <li
            onClick={() => !renaming && onClick?.(id)}
            className={`flex items-center gap-2 px-2 py-2 rounded-lg cursor-pointer transition-all group relative overflow-hidden ${
                active ? 'bg-white/15' : 'hover:bg-white/10'
            }`}
        >
            <MessageSquare size={14} className="text-sidebar-text-muted shrink-0" />

            {renaming ? (
                /* ── Rename input ── */
                <div className="flex items-center gap-1 min-w-0 flex-1" onClick={(e) => e.stopPropagation()}>
                    <input
                        ref={inputRef}
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') submitRename();
                            if (e.key === 'Escape') setRenaming(false);
                        }}
                        className="bg-white/10 text-white text-sm rounded px-1.5 py-0.5 w-full focus:outline-none focus:ring-1 focus:ring-sjsu-gold/50"
                    />
                    <button
                        onClick={submitRename}
                        className="shrink-0 p-1 rounded hover:bg-white/10 text-green-400 hover:text-green-300 transition-colors"
                        title="Save"
                    >
                        <Check size={13} />
                    </button>
                    <button
                        onClick={() => setRenaming(false)}
                        className="shrink-0 p-1 rounded hover:bg-white/10 text-sidebar-text-muted hover:text-white transition-colors"
                        title="Cancel"
                    >
                        <X size={13} />
                    </button>
                </div>
            ) : (
                <>
                    {/* Title + timestamp */}
                    <div className="min-w-0 flex-1">
                        <span className="truncate block text-sm">{displayTitle}</span>
                        {updatedAt && <span className="text-[10px] text-sidebar-text-muted">{timeAgo(updatedAt)}</span>}
                    </div>

                    {/* Action icons — visible on hover */}
                    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                        <button
                            onClick={(e) => { e.stopPropagation(); setRenameValue(title || ''); setRenaming(true); }}
                            className="p-1.5 rounded-md hover:bg-white/15 text-sidebar-text-muted hover:text-white transition-colors"
                            title="Rename"
                        >
                            <Pencil size={13} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); onDelete?.(id); }}
                            className="p-1.5 rounded-md hover:bg-red-500/20 text-sidebar-text-muted hover:text-red-400 transition-colors"
                            title="Delete"
                        >
                            <Trash2 size={13} />
                        </button>
                    </div>

                    {/* Gradient fade — hides on hover when icons appear */}
                    <div className="absolute right-0 top-0 bottom-0 w-12 bg-linear-to-l from-bg-sidebar via-bg-sidebar/80 to-transparent pointer-events-none group-hover:opacity-0 transition-opacity" />
                </>
            )}
        </li>
    );
}
