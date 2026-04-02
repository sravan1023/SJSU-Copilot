import { useState } from 'react';
import { 
  Send, 
  ThumbsUp, 
  ThumbsDown, 
  Edit2,
  Sparkles,
  User,
  Paperclip,
  Mic,
  ArrowUp,
  RotateCw,
  Copy,
  Check,
  X
} from 'lucide-react';
import { SuggestionCard, FollowUpChip } from './Common';

export default function MainChat({ 
  messages, 
  input, 
  setInput, 
  handleSend, 
  isTyping,
  messagesEndRef 
}) {
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");

  const startEditing = (id, text) => {
    setEditingId(id);
    setEditText(text);
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditText("");
  };

  const saveEdit = () => {
    // In a real app, you'd call a function passed from parent to update state
    // For now, let's assume the UI should just reflect the change if possible
    setEditingId(null);
  };

  return (
    <main className="flex-1 flex flex-col relative bg-bg-main transition-colors duration-300 min-w-0">
      {/* Top Navigation */}
      <header className="flex items-center justify-between px-10 py-5 z-10 transition-colors duration-300">
          <div className="flex items-center gap-4 ml-auto">
          </div>
      </header>

      {/* Chat Content */}
      <div className="flex-1 overflow-y-auto px-8 md:px-16 py-6 pb-40 scrollbar-hide">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center">
            <div className="relative text-center mb-12">
               <h1 className="text-7xl font-display font-bold text-text-primary/10 select-none pointer-events-none tracking-tighter leading-none transition-colors duration-300">
                  SJSU <br /> COPILOT
               </h1>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full max-w-3xl">
               <SuggestionCard title="Degree Requirements" subtitle="What classes do I need to graduate?" />
               <SuggestionCard title="Registration Dates" subtitle="When is the deadline for Spring 2026?" />
               <SuggestionCard title="Professor Office Hours" subtitle="Where can I find my professors?" />
               <SuggestionCard title="Campus Dining" subtitle="What are the best places to eat near the SU?" />
               <SuggestionCard title="Internship Opportunities" subtitle="Show me roles for Software Engineering" />
               <SuggestionCard title="Library Resources" subtitle="How do I book a private study room?" />
            </div>
          </div>
        ) : (
           <div className="max-w-3xl mx-auto space-y-8">
              <div className="flex items-center justify-between px-1 py-4">
                  <h2 className="text-lg font-semibold text-text-primary">SJSU Academic Assistant</h2>
                  <button className="p-1.5 hover:bg-bg-hover rounded-full transition-colors">
                      <Edit2 size={14} className="text-text-secondary" />
                  </button>
              </div>
              
              {messages.map((msg) => (
                  <div key={msg.id} className={`flex flex-col mb-5 group ${msg.sender === 'user' ? 'items-end' : 'items-start'}`}>
                      {msg.sender === 'user' ? (
                          <div className="flex flex-col items-end max-w-[80%]">
                            {editingId === msg.id ? (
                                <div className="w-full bg-[#f4f4f4] dark:bg-[#2f2f2f] rounded-[22px] p-2 border border-sjsu-gold/30">
                                    <textarea
                                        value={editText}
                                        onChange={(e) => setEditText(e.target.value)}
                                        className="w-full bg-transparent text-text-primary px-3 py-2 focus:outline-none resize-none min-h-[60px]"
                                        autoFocus
                                    />
                                    <div className="flex justify-end gap-2 pt-1 pr-1">
                                        <button 
                                            onClick={cancelEditing}
                                            className="p-1.5 rounded-full hover:bg-white/10 text-text-secondary transition-colors"
                                        >
                                            <X size={16} />
                                        </button>
                                        <button 
                                            onClick={() => saveEdit()}
                                            className="p-1.5 rounded-full bg-sjsu-gold text-white hover:bg-sjsu-gold-hover transition-colors"
                                        >
                                            <Check size={16} />
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <>
                                    <div className="bg-[#f4f4f4] dark:bg-[#2f2f2f] text-text-primary px-5 py-3.5 rounded-[20px] text-sm leading-relaxed relative group">
                                        {msg.text}
                                        <button 
                                            onClick={() => startEditing(msg.id, msg.text)}
                                            className="absolute -left-10 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 p-2 hover:bg-bg-hover rounded-full transition-all text-text-secondary"
                                            title="Edit message"
                                        >
                                            <Edit2 size={14} />
                                        </button>
                                    </div>
                                </>
                            )}
                          </div>
                      ) : (
                          <div className="w-full max-w-3xl pr-4">
                              <div className="prose max-w-none text-text-primary leading-7 text-sm markdown-body">
                                  {msg.text.split('\n').map((line, i) => (
                                      <p key={i} className={`mb-3 last:mb-0 ${line.trim().startsWith('•') || line.trim().match(/^\d+\./) ? 'ml-4' : ''}`}>
                                          {line}
                                      </p>
                                  ))}
                              </div>
                              <div className="flex items-center gap-1.5 mt-1.5">
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><Copy size={15} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><RotateCw size={15} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><ThumbsUp size={15} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><ThumbsDown size={15} /></button>
                              </div>
                          </div>
                      )}
                  </div>
              ))}

              {isTyping && (
                  <div className="flex flex-col mb-4 items-start animate-fade-in">
                      <div className="flex items-center space-x-2 h-7 px-3 text-text-secondary select-none">
                          <span className="text-sm font-medium animate-pulse italic">Thinking</span>
                          <div className="flex space-x-1 mt-1">
                              <div className="w-1.5 h-1.5 bg-[#E5A823] rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                              <div className="w-1.5 h-1.5 bg-[#E5A823] rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                              <div className="w-1.5 h-1.5 bg-[#E5A823] rounded-full animate-bounce"></div>
                          </div>
                      </div>
                  </div>
              )}

              {!isTyping && (
                <div className="flex flex-wrap gap-2.5 mt-4 mb-8">
                    {['Make response shorter', 'Explain like I\'m 5', 'Tell me more'].map((text, i) => (
                        <button key={i} className="px-4 py-2 bg-bg-surface hover:bg-bg-hover border border-border-color rounded-2xl text-xs font-medium text-text-primary transition-all shadow-sm hover:shadow-md hover:-translate-y-0.5">
                          {text}
                        </button>
                    ))}
                </div>
              )}
           </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="absolute bottom-4 left-0 right-0 px-8 py-3 flex flex-col items-center bg-transparent z-20">
         <div className="w-full max-w-3xl relative">
            <div className="w-full bg-white dark:bg-bg-input border border-[#E5A823]/30 dark:border-border-color text-text-primary rounded-xl flex items-center shadow-sm transition-colors duration-300 focus-within:ring-2 focus-within:ring-[#E5A823]/20 dark:focus-within:ring-primary/20">
                <button className="pl-4 py-3.5 text-text-secondary hover:text-text-primary transition-colors">
                    <Paperclip size={18} />
                </button>
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                  className="flex-1 bg-transparent border-none text-sm text-text-primary pl-3 pr-14 py-3.5 focus:outline-none placeholder-text-secondary"
                  placeholder="Send a message..."
                />
                <button 
                  className="absolute right-12 top-1/2 -translate-y-1/2 text-text-secondary hover:text-text-primary p-1.5 transition-colors"
                >
                  <Mic size={18} />
                </button>
                <button 
                  onClick={handleSend}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 bg-[#E5A823] hover:bg-[#c9921f] text-white p-1.5 rounded transition-colors"
                  disabled={!input.trim()}
                >
                  <Send size={16} />
                </button>
            </div>
         </div>
      </div>
    </main>
  );
}
