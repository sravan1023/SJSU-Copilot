import { 
  Plus, 
  Search, 
  GraduationCap,
  Calendar,
  Bell,
  User2Icon, 
  Sun, 
  Moon, 
  LogOut 
} from 'lucide-react';
import { SidebarToolItem } from './Common';

export default function Sidebar({ 
  startNewChat, 
  isDarkMode, 
  setIsDarkMode,
  onProfileClick,
  onInternAlertsClick,
  onLogout,
  user,
  currentPage,
}) {
  return (
    <aside className="w-64 bg-bg-sidebar text-sidebar-text-main flex flex-col shrink-0 transition-colors duration-300">
      <div className="px-5 py-6">
        <h1 className="text-white text-xl font-display font-bold tracking-wide flex items-center gap-2">
          SJSU <span className="font-normal">COPILOT</span>
        </h1>
      </div>

      <div className="px-4 mb-3">
        <button 
          onClick={startNewChat}
          className="w-full flex items-center gap-2.5 bg-white/10 hover:bg-white/20 text-white py-2.5 px-4 rounded-lg transition-colors border border-sidebar-border"
        >
          <Plus size={16} />
          <span className="text-sm font-medium">New Chat</span>
        </button>
      </div>

      {/* Search Bar */}
      <div className="px-4 mb-4">
        <div className="relative group">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-sidebar-text-muted transition-colors group-focus-within:text-white" />
          <input 
            type="text" 
            placeholder="Search chats..." 
            className="w-full bg-white/5 border border-sidebar-border rounded-lg pl-9 pr-3 py-2.5 text-xs text-white placeholder-sidebar-text-muted focus:outline-none focus:bg-white/10 focus:border-white/30 transition-all"
          />
        </div>
      </div>

      {/* Tools / Shortcuts */}
      <div className="px-4 mb-5">
        <h3 className="text-[11px] font-semibold text-sidebar-text-muted px-2 mb-2 uppercase tracking-wider">Services</h3>
        <div className="space-y-1">
          <SidebarToolItem icon={<GraduationCap size={16} />} label="Degree Progress" />
          <SidebarToolItem icon={<Calendar size={16} />} label="Registration Info" />
          <SidebarToolItem
            icon={<Bell size={16} />}
            label="Intern Alerts"
            active={currentPage === 'intern-alerts'}
            onClick={onInternAlertsClick}
          />
        </div>
      </div>

      {/* Chat History */}
      <div className="flex-1 overflow-y-auto px-4 scrollbar-hide">
        <div>
          <h3 className="text-[11px] font-semibold text-sidebar-text-muted mb-2 uppercase tracking-wider">Recent</h3>
          <ul className="space-y-0.5 text-sm">
            {/* History items will appear here */}
          </ul>
        </div>
      </div>

      {/* Bottom User Profile & Settings */}
      <div className="p-4 border-t border-sidebar-border">
        <div 
          onClick={onProfileClick}
          className="flex items-center gap-3 px-2 py-2.5 rounded-lg cursor-pointer group hover:bg-white/10 transition-colors"
        >
          <div className="w-8 h-8 rounded-full overflow-hidden bg-linear-to-br from-sjsu-gold to-orange-400 shrink-0 flex items-center justify-center">
            <User2Icon size={16} className="text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-sidebar-text-main truncate">{user?.user_metadata?.full_name || user?.user_metadata?.name || 'Student'}</p>
          </div>
          <div className="flex items-center gap-0.5 transition-opacity">
            <button 
              onClick={(e) => {
                  e.stopPropagation();
                  setIsDarkMode(!isDarkMode);
              }}
              className="p-1.5 rounded-md hover:bg-white/10 transition-colors text-sidebar-text-muted hover:text-white"
              title={isDarkMode ? "Light Mode" : "Dark Mode"}
            >
              {isDarkMode ? <Sun size={14} className="text-sjsu-gold" /> : <Moon size={14} />}
            </button>
            <button 
              onClick={(e) => {
                  e.stopPropagation();
                  onLogout();
              }}
              className="p-1.5 rounded-md hover:bg-red-500/20 transition-colors text-sidebar-text-muted hover:text-red-400"
              title="Logout"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
