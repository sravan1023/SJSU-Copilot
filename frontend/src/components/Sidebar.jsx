import { 
  Plus, 
  Search, 
  GraduationCap, 
  Calendar, 
  Briefcase, 
  Clock, 
  User2Icon, 
  Sun, 
  Moon, 
  LogOut 
} from 'lucide-react';
import { SidebarToolItem, HistoryItem } from './Common';

export default function Sidebar({ 
  startNewChat, 
  isDarkMode, 
  setIsDarkMode 
}) {
  return (
    <aside className="w-64 bg-bg-sidebar text-sidebar-text-main flex flex-col shrink-0 transition-colors duration-300">
      <div className="p-6">
        <h1 className="text-white text-xl font-semibold tracking-wide flex items-center gap-2">
          <span className="font-bold">SJSU</span> CHATBOT
        </h1>
      </div>

      <div className="px-4 mb-2">
        <button 
          onClick={startNewChat}
          className="w-full flex items-center gap-2 bg-white/10 hover:bg-white/20 text-white py-3 px-4 rounded-lg transition-colors border border-sidebar-border"
        >
          <Plus size={18} />
          <span className="text-sm font-medium">New Chat</span>
        </button>
      </div>

      {/* Search Bar */}
      <div className="px-4 mb-4">
        <div className="relative group">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-sidebar-text-muted transition-colors group-focus-within:text-white" />
          <input 
            type="text" 
            placeholder="Search chats..." 
            className="w-full bg-white/5 border border-sidebar-border rounded-lg pl-10 pr-4 py-2 text-xs text-white placeholder-sidebar-text-muted focus:outline-none focus:bg-white/10 focus:border-white/30 transition-all font-medium"
          />
        </div>
      </div>

      {/* Tools / Shortcuts */}
      <div className="px-4 mb-6">
        <h3 className="text-xs font-semibold text-sidebar-text-muted px-2 mb-2 uppercase tracking-wider">Services</h3>
        <div className="space-y-1">
          <SidebarToolItem icon={<GraduationCap size={16} />} label="Degree Progress" />
          <SidebarToolItem icon={<Calendar size={16} />} label="Registration Info" />
          <SidebarToolItem icon={<Briefcase size={16} />} label="Internship Alerts" />
        </div>
      </div>

      {/* Chat History */}
      <div className="flex-1 overflow-y-auto px-4 space-y-6 scrollbar-hide">
        <div>
          <h3 className="text-xs font-semibold text-sidebar-text-muted mb-3 uppercase tracking-wider">Recent History</h3>
          <ul className="space-y-1 text-sm">
            <HistoryItem text="Spring 2026 Registration" />
            <HistoryItem text="Degree Progress Audit" />
            <HistoryItem text="Dining Near Student Union" />
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold text-sidebar-text-muted mb-3 uppercase tracking-wider">Previous Days</h3>
          <ul className="space-y-1 text-sm">
            <HistoryItem text="Software Internship Roles" />
            <HistoryItem text="Library Room Booking" />
          </ul>
        </div>
      </div>

      {/* Bottom User Profile & Settings */}
      <div className="p-4 border-t border-sidebar-border">
        <div className="flex items-center gap-3 px-2 py-2 rounded-lg cursor-pointer group">
          <div className="w-8 h-8 rounded-full overflow-hidden bg-linear-to-br from-sjsu-gold to-orange-400 shrink-0 flex items-center justify-center">
            <User2Icon size={18} className="text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-sidebar-text-main truncate">Student Name</p>
            <p className="text-xs text-sidebar-text-muted truncate">student@sjsu.edu</p>
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
              onClick={(e) => e.stopPropagation()}
              className="p-1.5 rounded-md hover:bg-white/10 transition-colors text-sidebar-text-muted hover:text-white"
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
