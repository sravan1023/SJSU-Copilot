import { useState, useRef, useEffect } from 'react';
import './App.css';

export default function App() {
  const [messages, setMessages] = useState([
    { 
      id: 1, 
      text: "Hello! I'm SJSU Copilot. I can help you with course details, office hours, dining options on campus, and more. How can I assist you today?", 
      sender: 'bot' 
    }
  ]);
  const [input, setInput] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    return false;
  });
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim()) return;

    const userMessage = { id: Date.now(), text: input, sender: 'user' };
    setMessages(prev => [...prev, userMessage]);
    setInput("");

    // Simulate bot response
    setTimeout(() => {
      const botResponse = { 
        id: Date.now() + 1, 
        text: "I'm currently a demo interface. Once connected to the backend, I'll use RAG to fetch real data from SJSU sources for you!", 
        sender: 'bot' 
      };
      setMessages(prev => [...prev, botResponse]);
    }, 1000);
  };

  return (
    <div className="flex h-screen bg-gray-100 dark:bg-gray-900 font-sans text-gray-900 dark:text-gray-100 transition-colors duration-200">
      {/* Sidebar - Desktop only for now */}
      <aside className="hidden md:flex flex-col w-64 bg-[#0055A2] dark:bg-blue-900 text-white transition-colors duration-200">
        <div className="p-6 border-b border-blue-800 dark:border-blue-800">
          <h1 className="text-2xl font-bold tracking-tight">SJSU Copilot</h1>
          <p className="text-xs text-blue-200 mt-1">Student Assistant</p>
        </div>
        <nav className="flex-1 overflow-y-auto py-4">
          <ul className="space-y-1">
            <li><a href="#" className="flex items-center px-6 py-3 bg-blue-800/50 border-r-4 border-[#E5A823]">Chat</a></li>
            <li><a href="#" className="flex items-center px-6 py-3 hover:bg-blue-800 transition-colors">Degree Progress</a></li>
            <li><a href="#" className="flex items-center px-6 py-3 hover:bg-blue-800 transition-colors">Class Schedule</a></li>
            <li><a href="#" className="flex items-center px-6 py-3 hover:bg-blue-800 transition-colors">Campus Map</a></li>
          </ul>
        </nav>
        <div className="p-4 border-t border-blue-800">
            <button 
                onClick={() => setIsDarkMode(!isDarkMode)}
                className="w-full mb-4 flex items-center justify-center gap-2 bg-blue-800 hover:bg-blue-700 py-2 rounded-lg text-sm transition-colors"
            >
                {isDarkMode ? '☀️ Light Mode' : '🌙 Dark Mode'}
            </button>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-blue-700 flex items-center justify-center">S</div>
            <div className="text-sm">
              <p className="font-medium">Student</p>
              <p className="text-xs text-blue-300">Logout</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col h-full relative">
        {/* Header (Mobile) */}
        <header className="md:hidden bg-[#0055A2] dark:bg-blue-900 text-white p-4 flex items-center justify-between shadow-sm z-10 transition-colors duration-200">
          <span className="font-bold text-lg">SJSU Copilot</span>
          <div className="flex gap-3">
             <button onClick={() => setIsDarkMode(!isDarkMode)} className="text-xl">
               {isDarkMode ? '☀️' : '🌙'}
             </button>
             <button className="text-white">☰</button>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-6 bg-white dark:bg-gray-800 transition-colors duration-200">
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div 
                className={`max-w-[85%] md:max-w-[70%] rounded-2xl px-5 py-3 shadow-sm ${
                  msg.sender === 'user' 
                    ? 'bg-[#0055A2] text-white rounded-br-none' 
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100 rounded-bl-none border border-gray-200 dark:border-gray-600 transition-colors duration-200'
                }`}
              >
                <p className="leading-relaxed">{msg.text}</p>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-4 md:p-6 bg-white dark:bg-gray-800 border-t border-gray-100 dark:border-gray-700 transition-colors duration-200">
            <form onSubmit={handleSend} className="max-w-4xl mx-auto relative flex gap-3">
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Ask about office hours, deadlines, etc..."
                  className="w-full pl-5 pr-12 py-3 rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-300 focus:border-[#0055A2] dark:focus:border-blue-400 focus:ring-2 focus:ring-blue-100 dark:focus:ring-blue-900 focus:outline-none transition-all shadow-sm"
                />
                <button 
                  type="submit"
                  disabled={!input.trim()}
                  className="absolute right-2 top-1.5 bottom-1.5 bg-[#E5A823] text-[#0055A2] px-4 rounded-lg font-semibold hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  ➤
                </button>
            </form>
            <p className="text-center text-xs text-gray-400 mt-2">
              SJSU Copilot can make mistakes. Check official university sources.
            </p>
        </div>
      </main>
    </div>
  )
}
