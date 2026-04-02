import { useState, useRef, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import MainChat from './components/MainChat';
import RightPanel from './components/RightPanel';
import UserProfile from './components/UserProfile';
import InternJobsAlertsPage from './internAlerts/InternJobsAlertsPage.tsx';
import Login from './components/Login';
import Signup from './components/Signup';
import VerifyEmail from './components/VerifyEmail';
import { supabase } from './supabaseClient';
import { ensureProfile } from './supabaseHelpers';
import { runDueJobFetchCycle } from './services/jobFetcher';
import './App.css';

export default function App() {
  // Authentication state
  const [authPage, setAuthPage] = useState('login'); // 'login' | 'signup' | 'verify-email' | null
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState('');
  const [unverifiedEmail, setUnverifiedEmail] = useState('');

  // Check for existing session and listen for auth changes
  useEffect(() => {
    let alive = true;

    const withTimeout = (promise, label, timeoutMs = 12000) =>
      Promise.race([
        promise,
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs);
        }),
      ]);

    const bootstrapAuth = async () => {
      try {
        const sessionRes = await withTimeout(supabase.auth.getSession(), 'Auth session check');
        const session = sessionRes?.data?.session;

        if (session?.user) {
          // Verify @sjsu.edu domain even for cached sessions
          if (!session.user.email?.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }

          // Check email verification (skip for OAuth users — they're already verified by Google)
          const isOAuth = session.user.app_metadata?.provider !== 'email';
          if (!isOAuth && !session.user.email_confirmed_at) {
            if (alive) {
              setUnverifiedEmail(session.user.email);
              setAuthPage('verify-email');
            }
            return;
          }

          if (alive) {
            setUser(session.user);
            setAuthPage(null);
            setAuthError('');
          }

          // Do not block initial app render on profile sync.
          withTimeout(ensureProfile(session.user), 'Profile check').catch((profileError) => {
            if (alive) {
              console.warn('Profile sync warning:', profileError?.message || profileError);
            }
          });
        } else if (alive) {
          setUser(null);
          setAuthPage('login');
        }
      } catch (error) {
        if (alive) {
          setUser(null);
          setAuthPage('login');
          setAuthError(error?.message || 'Failed to initialize authentication.');
        }
      } finally {
        if (alive) setAuthLoading(false);
      }
    };

    bootstrapAuth();

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      try {
        if (session?.user) {
          // Verify @sjsu.edu domain for OAuth logins
          if (session.user.email && !session.user.email.endsWith('@sjsu.edu')) {
            await supabase.auth.signOut();
            if (alive) {
              setUser(null);
              setAuthPage('login');
            }
            return;
          }
          // Check email verification (skip for OAuth users)
          const isOAuth = session.user.app_metadata?.provider !== 'email';
          if (!isOAuth && !session.user.email_confirmed_at) {
            if (alive) {
              setUnverifiedEmail(session.user.email);
              setAuthPage('verify-email');
            }
            return;
          }

          if (alive) {
            setUser(session.user);
            setAuthPage(null);
            setAuthError('');
          }

          withTimeout(ensureProfile(session.user), 'Profile sync').catch((profileError) => {
            if (alive) {
              console.warn('Profile sync warning:', profileError?.message || profileError);
            }
          });
        } else if (alive) {
          setUser(null);
          setAuthPage('login');
        }
      } catch (error) {
        if (alive) {
          setAuthError(error?.message || 'Authentication state update failed.');
        }
      }
    });

    return () => {
      alive = false;
      subscription.unsubscribe();
    };
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

  useEffect(() => {
    if (!user?.id) return undefined;

    const schedulerEnabled = import.meta.env.VITE_ENABLE_JOB_FETCHER_SCHEDULER === 'true';
    if (!schedulerEnabled) return undefined;

    const schedulerMs = Number(import.meta.env.VITE_JOB_FETCHER_SCHEDULER_MS || 300000);
    let running = false;

    const runCycle = async () => {
      if (running) return;
      running = true;
      try {
        await runDueJobFetchCycle({ userId: user.id });
      } catch (error) {
        console.error('Job scheduler cycle failed:', error?.message || error);
      } finally {
        running = false;
      }
    };

    runCycle();
    const intervalId = setInterval(runCycle, schedulerMs);
    return () => clearInterval(intervalId);
  }, [user?.id]);

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
    setCurrentPage('chat');
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
    return <Login onLogin={handleLogin} onSwitchToSignup={() => setAuthPage('signup')} authError={authError} />;
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
        onInternAlertsClick={() => setCurrentPage('intern-alerts')}
        onLogout={handleLogout}
        user={user}
        currentPage={currentPage}
      />
      
      {currentPage === 'profile' ? (
        <UserProfile onBack={() => setCurrentPage('chat')} user={user} />
      ) : currentPage === 'intern-alerts' ? (
        <InternJobsAlertsPage onBack={() => setCurrentPage('chat')} />
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
