import { useState, useRef, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import MainChat from './components/MainChat';
import RightPanel from './components/RightPanel';
import UserProfile from './components/UserProfile';
import Login from './components/Login';
import Signup from './components/Signup';
import './App.css';

export default function App() {
  // Auth state
  const [authPage, setAuthPage] = useState('login'); // 'login' | 'signup' | null
  const [user, setUser] = useState(null);

  // State
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [rightPanelContent, setRightPanelContent] = useState('empty');
  const [currentPage, setCurrentPage] = useState('chat');
  const messagesEndRef = useRef(null);

  // Apply dark mode class to html element
  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  // Scroll to bottom when messages change
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };
  
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Handle sending a message
  const handleSend = () => {
    if (!input.trim()) return;

    // Add user message
    const userMsg = { id: Date.now(), text: input, sender: 'user' };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setIsTyping(true);
    
    // Switch to active state immediately
    setRightPanelContent('links');

    // Simulate AI response
    setTimeout(() => {
      const aiMsg = { 
        id: Date.now() + 1, 
        text: `San Jose State University (SJSU) is the founding campus of the California State University (CSU) system and the oldest public university on the West Coast.

Located in downtown San Jose, the university offers more than 145 areas of study with an additional 108 concentrations. SJSU is known for its strong programs in engineering, business, and computer science, and its proximity to Silicon Valley tech giants.

**Key Highlights:**
• **Rankings:** Often ranked as one of the top public universities in the West.
• **innovation:** A hub for student startups and research.
• **diversity:** A vibrant campus with a diverse student body.

Let me know if you would like to know more about specific departments or campus life!`,
        sender: 'bot' 
      };
      setMessages(prev => [...prev, aiMsg]);
      setIsTyping(false);
    }, 2000);
  };

  const startNewChat = () => {
    setMessages([]);
    setRightPanelContent('empty');
  };

  const handleLogin = (credentials) => {
    // TODO: Replace with real auth
    console.log('Login:', credentials);
    setUser({ email: credentials.email });
    setAuthPage(null);
  };

  const handleSignup = (data) => {
    // TODO: Replace with real auth
    console.log('Signup:', data);
    setUser({ email: data.email, name: data.fullName });
    setAuthPage(null);
  };

  const handleLogout = () => {
    setUser(null);
    setAuthPage('login');
    setMessages([]);
    setRightPanelContent('empty');
    setCurrentPage('chat');
  };

  // Show auth pages if not logged in
  if (authPage === 'login') {
    return <Login onLogin={handleLogin} onSwitchToSignup={() => setAuthPage('signup')} />;
  }
  if (authPage === 'signup') {
    return <Signup onSignup={handleSignup} onSwitchToLogin={() => setAuthPage('login')} />;
  }

  return (
    <div className="flex h-screen text-text-primary font-sans overflow-hidden transition-colors duration-300 bg-bg-main">
      <Sidebar 
        startNewChat={startNewChat} 
        isDarkMode={isDarkMode} 
        setIsDarkMode={setIsDarkMode}
        onProfileClick={() => setCurrentPage('profile')}
        onLogout={handleLogout} 
      />
      
      {currentPage === 'profile' ? (
        <UserProfile onBack={() => setCurrentPage('chat')} />
      ) : (
        <>
          <MainChat 
            messages={messages}
            input={input}
            setInput={setInput}
            handleSend={handleSend}
            isTyping={isTyping}
            messagesEndRef={messagesEndRef}
          />
          <RightPanel 
            rightPanelContent={rightPanelContent} 
          />
        </>
      )}
    </div>
  );
}
