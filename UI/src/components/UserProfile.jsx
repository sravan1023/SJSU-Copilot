import { useState, useEffect, useMemo } from 'react';
import { ArrowLeft, Save, User2Icon, Mail, Phone, BookOpen, GraduationCap, Calendar, Hash, Brain, Sliders, ListOrdered } from 'lucide-react';
import { supabase } from '../supabaseClient';
import MemoryManagement from './MemoryManagement';
import BehaviorSettings from './BehaviorSettings';
import PrioritySettings from './PrioritySettings';
import { DEFAULT_BEHAVIOR } from '../services/behaviorService';
import {
  updateProfile,
  saveAudienceDetails,
  fetchAudienceDetails,
} from '../services/profileService';

/**
 * Fields whose stored shape differs from their input shape.
 *
 * Kept here rather than in audiences.json because this is a property of the
 * form's input types, not of the audience: `graduation_year` is an integer
 * column shown by a <input type="month">, and `gpa` is numeric shown by a text
 * input. Everything else round-trips as a string.
 */
const CONVERTERS = {
  graduation_year: {
    fromDb: (v) => (v ? `${v}-05` : ''),
    toDb: (v) => (v ? parseInt(String(v).split('-')[0], 10) : null),
  },
  gpa: {
    fromDb: (v) => (v ?? ''),
    toDb: (v) => (v === '' || v == null ? null : parseFloat(v)),
  },
};

const ICONS = { Hash, Phone, Mail, BookOpen, GraduationCap, Calendar, User2Icon };

/**
 * One field of the audience-driven section.
 *
 * Two shapes, which is all the original hand-written form used: plain, and
 * icon-prefixed with the icon absolutely positioned inside a relative wrapper.
 */
function Field({ field, value, onChange, inputClass, labelClass }) {
  const Icon = field.icon ? ICONS[field.icon] : null;
  const common = {
    name: field.name,
    value: value ?? '',
    onChange: (e) => onChange(field.name, e.target.value),
    placeholder: field.placeholder || '',
  };

  let control;
  if (field.type === 'select') {
    control = (
      <select {...common} className={inputClass}>
        <option value="">Select...</option>
        {(field.options || []).map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    );
  } else {
    const input = (
      <input
        {...common}
        type={field.type === 'number' ? 'number' : field.type}
        className={Icon ? `${inputClass} pl-10` : inputClass}
        maxLength={field.maxLength}
        min={field.min}
        max={field.max}
        step={field.step}
      />
    );
    control = Icon ? (
      <div className="relative">
        <Icon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
        {input}
      </div>
    ) : input;
  }

  return (
    <div className={field.span === 2 ? 'md:col-span-2' : ''}>
      <label className={labelClass}>{field.label}</label>
      {control}
    </div>
  );
}

export default function UserProfile({ onBack, user, audience, onProfileChange, behaviorSettings, onUpdateBehavior, autoBehavior }) {
  // At the global-profile scope there is no live conversation to auto-adapt to,
  // so the "auto" baseline falls back to DEFAULT_BEHAVIOR. Manual overrides
  // (behaviorSettings) are merged on top for display.
  const autoBaseline = useMemo(() => autoBehavior || DEFAULT_BEHAVIOR, [autoBehavior]);
  const manualOverrides = useMemo(() => behaviorSettings || {}, [behaviorSettings]);
  const effectiveSettings = useMemo(
    () => ({ ...autoBaseline, ...manualOverrides }),
    [autoBaseline, manualOverrides]
  );
  // Universal identity fields, the same for every audience.
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    email: '',
    phone: '',
  });

  // The audience-driven section. Keyed by field name; a field with a `column`
  // lands in `profiles`, one without lands in profile_audience_details.details.
  const [audienceData, setAudienceData] = useState({});

  const fields = useMemo(() => audience?.profileFields ?? [], [audience]);

  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [_loadingProfile, setLoadingProfile] = useState(true);
  const [activeTab, setActiveTab] = useState('profile'); // 'profile' | 'personalization'
  const [personalSection, setPersonalSection] = useState('style'); // 'style' | 'priorities' | 'memory'

  // Load profile from Supabase on mount
  useEffect(() => {
    let alive = true;
    async function fetchProfile() {
      if (!user?.id) { setLoadingProfile(false); return; }

      const { data } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', user.id)
        .single();

      // Fields with no `profiles` column live in a jsonb blob keyed by
      // audience, so an alum's employer and a faculty member's department do
      // not become two more mostly-null columns on a shared table.
      const details = await fetchAudienceDetails(user.id, audience?.id || 'student')
        .catch(() => ({}));

      if (!alive) return;

      if (data) {
        const nameParts = (data.full_name || '').split(' ');
        setFormData({
          firstName: nameParts[0] || '',
          lastName: nameParts.slice(1).join(' ') || '',
          email: data.email || '',
          phone: data.phone || '',
        });

        const next = {};
        for (const field of fields) {
          const raw = field.column ? data[field.column] : details[field.name];
          const convert = field.column && CONVERTERS[field.column]?.fromDb;
          next[field.name] = convert ? convert(raw) : (raw ?? '');
        }
        setAudienceData(next);
      }
      setLoadingProfile(false);
    }
    fetchProfile();
    return () => { alive = false; };
  }, [user, audience, fields]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
    setSaved(false);
  };

  const handleAudienceChange = (name, value) => {
    setAudienceData(prev => ({ ...prev, [name]: value }));
    setSaved(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaveError('');

    // Split the form by where each field is stored. Everything named in the
    // `profiles` patch must be in the UPDATE allowlist -- profileService
    // enforces that, because PostgREST rejects the whole statement if any one
    // column is ungranted, which would break saving every other field with it.
    const patch = {
      full_name: `${formData.firstName} ${formData.lastName}`.trim(),
      phone: formData.phone || null,
    };
    const details = {};

    for (const field of fields) {
      const value = audienceData[field.name];
      if (field.column) {
        const convert = CONVERTERS[field.column]?.toDb;
        patch[field.column] = convert ? convert(value) : (value || null);
      } else if (value !== undefined && value !== '') {
        details[field.name] = value;
      }
    }

    try {
      const updated = await updateProfile(user.id, patch);
      if (Object.keys(details).length > 0) {
        await saveAudienceDetails(user.id, audience?.id || 'student', details);
      }
      onProfileChange?.(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setSaveError(err?.message || 'Could not save your profile.');
    }
  };

  const inputClass =
    "w-full bg-bg-surface border border-border-color rounded-lg px-4 py-3 text-base text-text-primary placeholder-text-secondary focus:outline-none focus:ring-2 focus:ring-sjsu-gold/40 focus:border-sjsu-gold transition-all";

  const labelClass = "block text-sm font-semibold text-text-secondary mb-1.5 uppercase tracking-wide";

  return (
    <div className="flex-1 flex flex-col bg-bg-main overflow-y-auto transition-colors duration-300">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-bg-main/80 backdrop-blur-md border-b border-border-color px-8 py-5 flex items-center gap-4">
        <button
          onClick={onBack}
          className="p-2 rounded-lg hover:bg-bg-hover transition-colors text-text-secondary hover:text-text-primary"
          title="Back to Chat"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-text-primary">Settings</h1>
          <p className="text-sm text-text-secondary">Manage your profile, behavior, and memory</p>
        </div>
        {/* Tab switcher */}
        <div className="flex items-center bg-bg-surface border border-border-color rounded-lg p-1 gap-1">
          <button
            onClick={() => setActiveTab('profile')}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'profile'
                ? 'bg-white dark:bg-bg-hover text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <User2Icon size={15} />
            Profile
          </button>
          <button
            onClick={() => setActiveTab('personalization')}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'personalization'
                ? 'bg-white dark:bg-bg-hover text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <Sliders size={15} />
            Personalization
          </button>
        </div>
      </div>

      {/* Body */}
      {activeTab === 'personalization' ? (
        <div className="flex-1 flex overflow-hidden">
          {/* Left nav */}
          <nav className="w-56 shrink-0 border-r border-border-color bg-bg-surface/50 py-6 px-3 space-y-1 overflow-y-auto">
            <button
              onClick={() => setPersonalSection('style')}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                personalSection === 'style'
                  ? 'bg-sjsu-gold/10 text-sjsu-gold'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`}
            >
              <Sliders size={16} />
              Response Style
            </button>
            <button
              onClick={() => setPersonalSection('priorities')}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                personalSection === 'priorities'
                  ? 'bg-sjsu-gold/10 text-sjsu-gold'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`}
            >
              <ListOrdered size={16} />
              Priorities
            </button>
            <button
              onClick={() => setPersonalSection('memory')}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                personalSection === 'memory'
                  ? 'bg-sjsu-gold/10 text-sjsu-gold'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`}
            >
              <Brain size={16} />
              Memory
            </button>
          </nav>

          {/* Right content */}
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-2xl mx-auto px-8 py-8">
              {personalSection === 'style' ? (
                <>
                  <div className="mb-6">
                    <h2 className="text-lg font-bold text-text-primary">Response Style</h2>
                    <p className="text-sm text-text-secondary mt-0.5">Control how SJSU Copilot writes its responses</p>
                  </div>
                  <BehaviorSettings
                    settings={effectiveSettings}
                    autoBehavior={autoBaseline}
                    manualOverrides={manualOverrides}
                    onUpdate={onUpdateBehavior}
                  />
                </>
              ) : personalSection === 'priorities' ? (
                <>
                  <div className="mb-6">
                    <h2 className="text-lg font-bold text-text-primary">Priorities</h2>
                    <p className="text-sm text-text-secondary mt-0.5">What Copilot optimizes for when tradeoffs appear</p>
                  </div>
                  <PrioritySettings
                    stack={effectiveSettings.priority_stack}
                    onUpdate={onUpdateBehavior}
                  />
                </>
              ) : (
                <>
                  <div className="mb-6">
                    <h2 className="text-lg font-bold text-text-primary">Memory</h2>
                    <p className="text-sm text-text-secondary mt-0.5">Review and manage what Copilot remembers about you</p>
                  </div>
                  <MemoryManagement user={user} />
                </>
              )}
            </div>
          </div>
        </div>
      ) : (
      /* Form */
      <div className="flex-1 flex justify-center px-8 py-10">
        <form onSubmit={handleSubmit} className="w-full max-w-2xl space-y-8">
          
          {/* Avatar Section */}
          <div className="flex items-center gap-6 pb-6 border-b border-border-color">
            <div className="w-20 h-20 rounded-full bg-gradient-to-br from-sjsu-gold to-orange-400 flex items-center justify-center shrink-0 shadow-lg">
              <User2Icon size={36} className="text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-text-primary">
                {formData.firstName || formData.lastName
                  ? `${formData.firstName} ${formData.lastName}`.trim()
                  : 'Your Name'}
              </h2>
              <p className="text-sm text-text-secondary">
                {formData.email || 'your.email@sjsu.edu'}
              </p>
            </div>
          </div>

          {/* Personal Information -- the same for every audience */}
          <div>
            <h3 className="text-lg font-bold text-text-primary mb-4 flex items-center gap-2">
              <User2Icon size={18} className="text-sjsu-gold" />
              Personal Information
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div>
                <label className={labelClass}>First Name</label>
                <input
                  type="text"
                  name="firstName"
                  value={formData.firstName}
                  onChange={handleChange}
                  placeholder="John"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Last Name</label>
                <input
                  type="text"
                  name="lastName"
                  value={formData.lastName}
                  onChange={handleChange}
                  placeholder="Doe"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Phone Number</label>
                <div className="relative">
                  <Phone size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
                  <input
                    type="tel"
                    name="phone"
                    value={formData.phone}
                    onChange={handleChange}
                    placeholder="(408) 555-1234"
                    className={`${inputClass} pl-10`}
                  />
                </div>
              </div>
              <div>
                <label className={labelClass}>Email</label>
                <div className="relative">
                  <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
                  {/* Read-only: `email` is deliberately outside the UPDATE
                      allowlist, since it is the subject of the domain gate.
                      Showing it as editable would promise a save that the
                      database refuses. */}
                  <input
                    type="email"
                    value={formData.email}
                    readOnly
                    className={`${inputClass} pl-10 opacity-70 cursor-not-allowed`}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Audience-specific -- driven by UI/src/config/audiences.json, so a
              visitor is not asked for a GPA and an alum is not offered a class
              standing of 'Freshman'. */}
          {fields.length > 0 && (
            <div>
              <h3 className="text-lg font-bold text-text-primary mb-4 flex items-center gap-2">
                <GraduationCap size={18} className="text-sjsu-gold" />
                {audience?.label || 'Details'}
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {fields.map((field) => (
                  <Field
                    key={field.name}
                    field={field}
                    value={audienceData[field.name]}
                    onChange={handleAudienceChange}
                    inputClass={inputClass}
                    labelClass={labelClass}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Save Button */}
          <div className="flex items-center gap-4 pt-4 pb-8">
            <button
              type="submit"
              className="flex items-center gap-2 bg-sjsu-gold hover:bg-sjsu-gold-hover text-white font-semibold px-8 py-3 rounded-lg transition-colors shadow-md hover:shadow-lg"
            >
              <Save size={18} />
              Save Profile
            </button>
            {saved && (
              <span className="text-green-600 dark:text-green-400 text-sm font-medium animate-fade-in">
                Profile saved successfully!
              </span>
            )}
            {saveError && (
              <span className="text-red-500 dark:text-red-400 text-sm font-medium">{saveError}</span>
            )}
          </div>
        </form>
      </div>
      )}
    </div>
  );
}
