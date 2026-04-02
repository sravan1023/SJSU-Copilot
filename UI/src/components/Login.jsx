import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { supabase } from '../supabaseClient';

export default function Login({ onLogin, onSwitchToSignup, authError = '' }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!agreeTerms) return;
    if (!email.endsWith('@sjsu.edu')) {
      setError('Only @sjsu.edu email addresses are allowed.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const timeoutMs = 12000;
      const signInRes = await Promise.race([
        supabase.auth.signInWithPassword({
          email,
          password,
        }),
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error('Login timed out. Check your Supabase connection and try again.')), timeoutMs);
        }),
      ]);

      const { data, error: signInError } = signInRes;
      if (signInError) {
        setError(signInError.message);
        return;
      }
      onLogin(data.user);
    } catch (submitError) {
      setError(submitError?.message || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    });
    if (oauthError) setError(oauthError.message);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0F172A] p-4">
      <div className="w-full max-w-5xl bg-[#1E293B] rounded-2xl shadow-2xl flex overflow-hidden">
        
        {/* Left - Form */}
        <div className="w-full md:w-1/2 p-10 md:p-12 flex flex-col justify-center">
          {/* Logo */}
          <div className="flex items-center gap-3 mb-10">
            <img src="/spartan.svg" alt="SJSU" className="w-10 h-10" />
            <span className="text-white text-xl font-bold tracking-wide">SJSU COPILOT</span>
          </div>

          {/* Heading */}
          <h1 className="text-3xl font-bold text-white mb-2">Login</h1>
          <p className="text-[#94A3B8] text-sm mb-8">Add your credentials to log in</p>

          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm mb-4">
              {error}
            </div>
          )}

          {!error && authError && (
            <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3 text-amber-300 text-sm mb-4">
              {authError}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div>
              <label className="block text-[#94A3B8] text-xs font-medium mb-1.5">Your email*</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Enter your email"
                className="w-full bg-[#0F172A] border border-[#334155] rounded-lg px-4 py-3 text-sm text-white placeholder-[#64748B] focus:outline-none focus:border-[#E5A823] focus:ring-1 focus:ring-[#E5A823]/30 transition-all"
                required
              />
            </div>

            {/* Password */}
            <div>
              <label className="block text-[#94A3B8] text-xs font-medium mb-1.5">Password*</label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                  className="w-full bg-[#0F172A] border border-[#334155] rounded-lg px-4 py-3 pr-11 text-sm text-white placeholder-[#64748B] focus:outline-none focus:border-[#E5A823] focus:ring-1 focus:ring-[#E5A823]/30 transition-all"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[#64748B] hover:text-white transition-colors"
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {/* Terms */}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={agreeTerms}
                onChange={(e) => setAgreeTerms(e.target.checked)}
                className="w-4 h-4 rounded border-[#334155] bg-[#0F172A] accent-[#E5A823]"
              />
              <span className="text-[#94A3B8] text-sm">I agree to terms & conditions</span>
            </label>

            {/* Login Button */}
            <button
              type="submit"
              disabled={!agreeTerms || loading}
              className="w-full bg-[#E5A823] hover:bg-[#D49612] disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-lg transition-colors shadow-lg shadow-[#E5A823]/20"
            >
              {loading ? 'Logging in...' : 'Login'}
            </button>
          </form>

          {/* Divider */}
          <div className="flex items-center gap-4 my-6">
            <div className="flex-1 h-px bg-[#334155]"></div>
            <span className="text-[#64748B] text-xs">Or</span>
            <div className="flex-1 h-px bg-[#334155]"></div>
          </div>

          {/* Google */}
          <button
            type="button"
            onClick={handleGoogleLogin}
            className="w-full flex items-center justify-center gap-3 bg-[#0F172A] hover:bg-[#1a2744] border border-[#334155] text-white font-medium py-3 rounded-lg transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 48 48">
              <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
              <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
              <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
              <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
            </svg>
            Sign in with Google
          </button>

          {/* Switch to Signup */}
          <p className="text-center text-[#94A3B8] text-sm mt-6">
            Don't have an Account?{' '}
            <button onClick={onSwitchToSignup} className="text-[#E5A823] hover:text-[#D49612] font-medium transition-colors">
              Sign up
            </button>
          </p>
        </div>

        {/* Right - Image */}
        <div className="hidden md:block w-1/2 p-4">
          <div className="w-full h-full rounded-xl overflow-hidden bg-gradient-to-br from-[#0055A2] via-[#0055A2]/80 to-[#E5A823]/60 flex items-center justify-center">
            <div className="text-center p-8">
              <img src="/spartan.svg" alt="SJSU Spartan" className="w-40 h-40 mx-auto mb-6 drop-shadow-2xl" />
              <h2 className="text-white text-3xl font-bold mb-3">Welcome Back</h2>
              <p className="text-white/70 text-sm max-w-xs mx-auto">Your AI academic assistant at San Jose State University</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
