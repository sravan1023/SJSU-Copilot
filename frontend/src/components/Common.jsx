import { MessageSquare, MoreHorizontal, Link as LinkIcon } from 'lucide-react';

export function SuggestionCard({ title, subtitle }) {
    return (
        <button className="bg-bg-surface hover:bg-bg-hover border border-border-color px-6 py-5 rounded-lg text-left transition-colors h-24 flex flex-col justify-center shadow-sm w-full">
            <h4 className="font-semibold text-text-primary text-sm mb-1">{title}</h4>
            <p className="text-xs text-text-secondary">{subtitle}</p>
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
        <div className={`flex items-center justify-between px-4 py-4 bg-bg-surface hover:bg-bg-hover transition-colors cursor-pointer group ${!isFirst ? 'border-t border-border-color' : ''}`}>
            <span className="text-sm underline text-text-primary font-medium group-hover:text-black dark:group-hover:text-white">{label}</span>
            <LinkIcon size={14} className="text-sjsu-gold group-hover:text-black dark:group-hover:text-white" />
        </div>
    );
}

export function SidebarToolItem({ icon, label }) {
    return (
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-white/10 cursor-pointer transition-colors group">
            <div className="text-sidebar-text-muted group-hover:text-white transition-colors">
                {icon}
            </div>
            <span className="text-sm text-sidebar-text-main font-medium">{label}</span>
        </div>
    );
}

export function HistoryItem({ text }) {
    return (
        <li className="flex items-center justify-between gap-3 hover:bg-white/10 px-3 py-2 rounded-lg cursor-pointer transition-all group relative overflow-hidden">
            <div className="flex items-center gap-3 min-w-0">
                <MessageSquare size={16} className="text-sidebar-text-muted shrink-0" />
                <span className="truncate pr-4">{text}</span>
            </div>
            <button className="opacity-0 group-hover:opacity-100 p-1 hover:bg-white/10 rounded-md transition-all">
                <MoreHorizontal size={14} className="text-sidebar-text-muted hover:text-white" />
            </button>
            {/* Gradient shadow for text truncation like ChatGPT */}
            <div className={`absolute right-0 top-0 bottom-0 w-16 bg-linear-to-l from-bg-sidebar via-bg-sidebar/80 to-transparent pointer-events-none transition-opacity group-hover:opacity-0`}></div>
        </li>
    );
}
