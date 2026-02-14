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
  Copy
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
  return (
    <main className="flex-1 flex flex-col relative bg-bg-main transition-colors duration-300 min-w-0">
      {/* Top Navigation */}
      <header className="flex items-center justify-between px-8 py-4 z-10 transition-colors duration-300">
          <div className="flex items-center gap-4 ml-auto">
          </div>
      </header>

      {/* Chat Content */}
      <div className="flex-1 overflow-y-auto px-8 md:px-16 py-6 pb-40 scrollbar-hide">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center">
            <div className="relative text-center mb-16">
               <h1 className="text-8xl font-bold text-text-primary/10 select-none pointer-events-none tracking-tighter leading-none transition-colors duration-300">
                  SJSU <br /> CHATBOT
               </h1>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full max-w-4xl">
               <SuggestionCard title="Degree Requirements" subtitle="What classes do I need to graduate?" />
               <SuggestionCard title="Registration Dates" subtitle="When is the deadline for Spring 2026?" />
               <SuggestionCard title="Professor Office Hours" subtitle="Where can I find my professors?" />
               <SuggestionCard title="Campus Dining" subtitle="What are the best places to eat near the SU?" />
               <SuggestionCard title="Internship Opportunities" subtitle="Show me roles for Software Engineering" />
               <SuggestionCard title="Library Resources" subtitle="How do I book a private study room?" />
            </div>
          </div>
        ) : (
           <div className="max-w-4xl mx-auto space-y-8">
              <div className="flex items-center justify-between px-2 py-4">
                  <h2 className="text-xl font-semibold text-text-primary">SJSU Academic Assistant</h2>
                  <button className="p-2 hover:bg-bg-hover rounded-full transition-colors">
                      <Edit2 size={16} className="text-text-secondary" />
                  </button>
              </div>
              
              {messages.map((msg) => (
                  <div key={msg.id} className={`flex flex-col mb-6 ${msg.sender === 'user' ? 'items-end' : 'items-start'}`}>
                      {msg.sender === 'user' ? (
                          <div className="bg-[#f4f4f4] dark:bg-[#2f2f2f] text-text-primary px-5 py-3.5 rounded-[26px] max-w-[85%] text-[1rem] leading-relaxed">
                            {msg.text}
                          </div>
                      ) : (
                          <div className="w-full max-w-4xl pr-4">
                              <div className="prose max-w-none text-text-primary leading-7 text-[1rem] markdown-body">
                                  {msg.text.split('\n').map((line, i) => (
                                      <p key={i} className={`mb-3 last:mb-0 ${line.trim().startsWith('•') || line.trim().match(/^\d+\./) ? 'ml-4' : ''}`}>
                                          {line}
                                      </p>
                                  ))}
                              </div>
                              <div className="flex items-center gap-2 mt-2">
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><Copy size={16} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><RotateCw size={16} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><ThumbsUp size={16} /></button>
                                   <button className="hover:bg-bg-hover p-1.5 rounded-md transition-colors text-text-secondary hover:text-text-primary"><ThumbsDown size={16} /></button>
                              </div>
                          </div>
                      )}
                  </div>
              ))}

              {isTyping && (
                  <div className="flex flex-col mb-6 items-start animate-fade-in">
                      <div className="flex items-center space-x-2 h-8 px-4 text-text-secondary select-none">
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
                <div className="flex flex-wrap gap-2 mt-4 mb-8">
                    {['Make response shorter', 'Explain like I\'m 5', 'Tell me more'].map((text, i) => (
                        <button key={i} className="px-5 py-2.5 bg-bg-surface hover:bg-bg-hover border border-border-color rounded-2xl text-sm font-medium text-text-primary transition-all shadow-sm hover:shadow-md hover:-translate-y-0.5">
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
      <div className="absolute bottom-6 left-0 right-0 px-8 py-4 flex flex-col items-center bg-transparent z-20">
         <div className="w-full max-w-4xl relative">
            <div className="w-full bg-[#FAF9F6] dark:bg-bg-input border border-[#E5A823]/30 dark:border-border-color text-text-primary rounded-lg flex items-center shadow-sm transition-colors duration-300 focus-within:ring-2 focus-within:ring-[#E5A823]/20 dark:focus-within:ring-primary/20">
                <button className="pl-4 py-4 text-text-secondary hover:text-text-primary transition-colors">
                    <Paperclip size={20} />
                </button>
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                  className="flex-1 bg-transparent border-none text-text-primary pl-4 pr-14 py-4 focus:outline-none placeholder-text-secondary"
                  placeholder="Send a message..."
                />
                <button 
                  className="absolute right-14 top-1/2 -translate-y-1/2 text-text-secondary hover:text-text-primary p-2 mr-1 transition-colors"
                >
                  <Mic size={20} />
                </button>
                <button 
                  onClick={handleSend}
                  className="absolute right-3 top-1/2 -translate-y-1/2 bg-[#E5A823] hover:bg-[#c9921f] text-white p-2 rounded transition-colors"
                  disabled={!input.trim()}
                >
                  <Send size={18} />
                </button>
            </div>
         </div>
      </div>
    </main>
  );
}
