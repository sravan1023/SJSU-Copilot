import { useState, useRef, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import MainChat from './components/MainChat';
import RightPanel from './components/RightPanel';
import UserProfile from './components/UserProfile';
import Login from './components/Login';
import Signup from './components/Signup';
import VerifyEmail from './components/VerifyEmail';
import { supabase } from './supabaseClient';
import { ensureProfile } from './supabaseHelpers';
import './App.css';

export default function App() {
  // Authentication state
  const [authPage, setAuthPage] = useState('login'); // 'login' | 'signup' | 'verify-email' | null
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [unverifiedEmail, setUnverifiedEmail] = useState('');

  // Check for existing session and listen for auth changes
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session?.user) {
        // Verify @sjsu.edu domain even for cached sessions
        if (!session.user.email?.endsWith('@sjsu.edu')) {
          await supabase.auth.signOut();
          setAuthLoading(false);
          return;
        }
        // Check email verification (skip for OAuth users — they're already verified by Google)
        const isOAuth = session.user.app_metadata?.provider !== 'email';
        if (!isOAuth && !session.user.email_confirmed_at) {
          setUnverifiedEmail(session.user.email);
          setAuthPage('verify-email');
          setAuthLoading(false);
          return;
        }
        await ensureProfile(session.user);
        setUser(session.user);
        setAuthPage(null);
      }
      setAuthLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      if (session?.user) {
        // Verify @sjsu.edu domain for OAuth logins
        if (!session.user.email?.endsWith('@sjsu.edu')) {
          await supabase.auth.signOut();
          setUser(null);
          setAuthPage('login');
          return;
        }
        // Check email verification (skip for OAuth users)
        const isOAuth = session.user.app_metadata?.provider !== 'email';
        if (!isOAuth && !session.user.email_confirmed_at) {
          setUnverifiedEmail(session.user.email);
          setAuthPage('verify-email');
          return;
        }
        await ensureProfile(session.user);
        setUser(session.user);
        setAuthPage(null);
      } else {
        setUser(null);
        setAuthPage('login');
      }
    });

    return () => subscription.unsubscribe();
  }, []);

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

  const handleLogin = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  const handleSignup = (user) => {
    setUser(user);
    setAuthPage(null);
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    setUser(null);
    setAuthPage('login');
    setMessages([]);
    setRightPanelContent('empty');
    setCurrentPage('chat');
  };

  // Show loading while checking session
  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0F172A]">
        <div className="text-white text-lg">Loading...</div>
      </div>
    );
  }

  // Show auth pages if not logged in
  if (authPage === 'login') {
    return <Login onLogin={handleLogin} onSwitchToSignup={() => setAuthPage('signup')} />;
  }
  if (authPage === 'signup') {
    return <Signup onSignup={handleSignup} onSwitchToLogin={() => setAuthPage('login')} />;
  }
  if (authPage === 'verify-email') {
    return <VerifyEmail email={unverifiedEmail} onBackToLogin={() => setAuthPage('login')} />;
  }

  return (
    <div className="flex h-screen text-text-primary font-sans overflow-hidden transition-colors duration-300 bg-bg-main">
      <Sidebar 
        startNewChat={startNewChat} 
        isDarkMode={isDarkMode} 
        setIsDarkMode={setIsDarkMode}
        onProfileClick={() => setCurrentPage('profile')}
        onLogout={handleLogout}
        user={user}
      />
      
      {currentPage === 'profile' ? (
        <UserProfile onBack={() => setCurrentPage('chat')} user={user} />
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
