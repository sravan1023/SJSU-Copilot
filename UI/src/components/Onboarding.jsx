import { useState } from 'react';
import { AUDIENCES, ONBOARDING_ORDER } from '../config/audiences';
import { completeOnboarding } from '../services/profileService';

/**
 * Shown once, after sign-in, when `profiles.onboarded_at` is null.
 *
 * Two things worth knowing about how this is wired:
 *
 * 1. **The gate that shows this is derived from profile state, not set
 *    imperatively.** App.jsx's `onAuthStateChange` calls `setAuthPage(null)`
 *    on every session event supabase-js produces -- sign-in, sign-out and the
 *    hourly TOKEN_REFRESHED alike -- so adding an `'onboarding'` value to that
 *    state machine would have it cleared out from under the user at an
 *    arbitrary moment. The gate reads `profile.onboarded_at` instead.
 *
 * 2. **Skipping still writes `onboarded_at`.** Otherwise "skip" would mean
 *    "ask me again on every single load", which is not what the button says.
 *
 * Rendered over the app shell after sign-in, so it uses the semantic theme
 * tokens the in-app components use, rather than the hardcoded hex of
 * Login/Signup/VerifyEmail.
 */
export default function Onboarding({ user, profile, onDone }) {
  const [audience, setAudience] = useState(profile?.active_audience || 'student');
  const [declare, setDeclare] = useState(true);
  const [details, setDetails] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const selected = AUDIENCES[audience];
  // Fields with no `column` have no home in `profiles` and go to
  // profile_audience_details.details as jsonb.
  const extraFields = (selected?.profileFields || []).filter(f => !f.column);

  const finish = async ({ skip = false } = {}) => {
    setSaving(true);
    setError('');
    try {
      const updated = await completeOnboarding(user.id, {
        audience: skip ? (profile?.active_audience || 'student') : audience,
        affiliation: skip || !declare ? null : selected?.declaresAffiliation,
        details: skip ? null : details,
      });
      onDone(updated);
    } catch (err) {
      setError(err?.message || 'Could not save your choices.');
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-main p-4">
      <div className="w-full max-w-2xl bg-bg-surface border border-border-color rounded-2xl shadow-2xl p-8 md:p-10">
        <div className="flex items-center gap-3 mb-8">
          <img src="/spartan.svg" alt="SJSU" className="w-9 h-9" />
          <span className="text-text-primary text-lg font-bold tracking-wide">SJSU COPILOT</span>
        </div>

        <h1 className="text-2xl font-bold text-text-primary mb-1">Which best describes you?</h1>
        <p className="text-text-secondary text-sm mb-6">
          This changes the suggestions you see and how answers are written. You can change it later
          in your profile.
        </p>

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm mb-5">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
          {ONBOARDING_ORDER.map((id) => {
            const option = AUDIENCES[id];
            if (!option) return null;
            const active = id === audience;
            return (
              <button
                key={id}
                type="button"
                onClick={() => { setAudience(id); setDetails({}); }}
                className={`text-left px-4 py-3 rounded-lg border transition-colors ${
                  active
                    ? 'border-sjsu-gold bg-sjsu-gold/10'
                    : 'border-border-color hover:border-sjsu-gold/50 hover:bg-white/5'
                }`}
              >
                <span className="block text-sm font-semibold text-text-primary">{option.label}</span>
                <span className="block text-xs text-text-secondary mt-0.5">{option.blurb}</span>
              </button>
            );
          })}
        </div>

        {/* A declaration, never a verification. Staff verify affiliations; the
            database will not let a user set their own status above 'declared'. */}
        {selected?.declaresAffiliation && (
          <label className="flex items-start gap-2.5 mb-6 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={declare}
              onChange={(e) => setDeclare(e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded border-border-color bg-bg-main accent-sjsu-gold"
            />
            <span className="text-text-secondary text-sm">
              Record that I said I am {selected.label.toLowerCase()}.
              <span className="block text-xs text-text-secondary/70 mt-0.5">
                Saved as something you told us, not as something we checked.
              </span>
            </span>
          </label>
        )}

        {extraFields.length > 0 && (
          <div className="mb-8">
            <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-3">
              Optional
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {extraFields.map((field) => (
                <div key={field.name} className={field.span === 2 ? 'md:col-span-2' : ''}>
                  <label className="block text-xs font-medium text-text-secondary mb-1.5">
                    {field.label}
                  </label>
                  <input
                    type={field.type === 'month' ? 'month' : 'text'}
                    value={details[field.name] || ''}
                    placeholder={field.placeholder || ''}
                    onChange={(e) => setDetails(prev => ({ ...prev, [field.name]: e.target.value }))}
                    className="w-full bg-bg-main border border-border-color rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder-text-secondary focus:outline-none focus:ring-2 focus:ring-sjsu-gold/40 focus:border-sjsu-gold transition-all"
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={saving}
            onClick={() => finish()}
            className="flex-1 bg-sjsu-gold hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-lg transition-all"
          >
            {saving ? 'Saving...' : 'Continue'}
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => finish({ skip: true })}
            className="px-5 py-3 rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-50 transition-colors"
          >
            Skip
          </button>
        </div>
      </div>
    </div>
  );
}
