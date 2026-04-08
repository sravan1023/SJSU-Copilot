import { useState } from 'react';
import { ChevronUp, ChevronDown, GripVertical, Shield, Target, CheckCircle2, Eye, Zap, Heart, Lightbulb, User } from 'lucide-react';

const PRIORITY_META = {
  safety:          { label: 'Safety',          desc: 'Protect user wellbeing, avoid harmful content',  icon: Shield,       color: 'text-red-500' },
  accuracy:        { label: 'Accuracy',        desc: 'Factually correct, cite sources when possible',  icon: Target,        color: 'text-blue-500' },
  task_completion: { label: 'Task Completion',  desc: 'Get the job done, finish what was asked',        icon: CheckCircle2,  color: 'text-emerald-500' },
  clarity:         { label: 'Clarity',          desc: 'Easy to understand, well-structured answers',    icon: Eye,           color: 'text-violet-500' },
  speed:           { label: 'Speed',            desc: 'Be concise, get to the point quickly',           icon: Zap,           color: 'text-amber-500' },
  warmth:          { label: 'Warmth',           desc: 'Empathetic, supportive, emotionally aware',      icon: Heart,         color: 'text-pink-500' },
  creativity:      { label: 'Creativity',       desc: 'Original ideas, novel approaches',               icon: Lightbulb,     color: 'text-orange-500' },
  personalization: { label: 'Personalization',  desc: 'Adapt to your context and preferences',          icon: User,          color: 'text-teal-500' },
};

const ALL_PRIORITIES = Object.keys(PRIORITY_META);

export default function PrioritySettings({ stack, onUpdate }) {
  const [saving, setSaving] = useState(false);

  // Ensure stack has all priorities (in case new ones were added)
  const current = stack && stack.length > 0 ? stack : ALL_PRIORITIES.slice(0, 6);
  const inactive = ALL_PRIORITIES.filter(k => !current.includes(k));

  const move = async (index, direction) => {
    const target = index + direction;
    if (target < 0 || target >= current.length) return;
    const next = [...current];
    [next[index], next[target]] = [next[target], next[index]];
    setSaving(true);
    await onUpdate({ priority_stack: next });
    setSaving(false);
  };

  const addPriority = async (key) => {
    const next = [...current, key];
    setSaving(true);
    await onUpdate({ priority_stack: next });
    setSaving(false);
  };

  const removePriority = async (key) => {
    if (current.length <= 2) return; // keep at least 2
    const next = current.filter(k => k !== key);
    setSaving(true);
    await onUpdate({ priority_stack: next });
    setSaving(false);
  };

  if (!stack) {
    return (
      <div className="space-y-3 animate-pulse">
        {[1, 2, 3, 4, 5, 6].map(i => (
          <div key={i} className="flex items-center gap-3 py-3">
            <div className="w-5 h-4 bg-border-color rounded" />
            <div className="w-8 h-8 bg-border-color rounded-lg" />
            <div className="flex-1 h-4 bg-border-color/60 rounded" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={`space-y-6 transition-opacity ${saving ? 'opacity-60 pointer-events-none' : ''}`}>
      {/* Active stack */}
      <div>
        <div className="space-y-0.5">
          {current.map((key, index) => {
            const meta = PRIORITY_META[key];
            if (!meta) return null;
            const Icon = meta.icon;
            const isFirst = index === 0;
            const isLast = index === current.length - 1;

            return (
              <div
                key={key}
                className="group flex items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-bg-surface transition-colors"
              >
                {/* Rank number */}
                <span className={`w-5 text-center text-xs font-bold shrink-0 ${
                  index === 0 ? 'text-sjsu-gold' : index <= 2 ? 'text-text-primary' : 'text-text-secondary'
                }`}>
                  {index + 1}
                </span>

                {/* Grip indicator */}
                <GripVertical size={14} className="text-border-color shrink-0" />

                {/* Icon */}
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 bg-bg-surface border border-border-color/60">
                  <Icon size={16} className={meta.color} />
                </div>

                {/* Label + desc */}
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-text-primary">{meta.label}</span>
                  <span className="text-xs text-text-secondary ml-2 hidden sm:inline">{meta.desc}</span>
                </div>

                {/* Move / remove buttons */}
                <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => move(index, -1)}
                    disabled={isFirst}
                    className="p-1 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                    title="Move up"
                  >
                    <ChevronUp size={14} />
                  </button>
                  <button
                    onClick={() => move(index, 1)}
                    disabled={isLast}
                    className="p-1 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                    title="Move down"
                  >
                    <ChevronDown size={14} />
                  </button>
                  <button
                    onClick={() => removePriority(key)}
                    disabled={current.length <= 2}
                    className="p-1 rounded-md text-text-secondary hover:text-red-500 hover:bg-red-500/10 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-text-secondary transition-colors ml-0.5"
                    title="Remove"
                  >
                    <span className="text-xs font-medium">Remove</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Inactive — available to add */}
      {inactive.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Available</span>
            <div className="flex-1 h-px bg-border-color/50" />
          </div>
          <div className="flex flex-wrap gap-2">
            {inactive.map(key => {
              const meta = PRIORITY_META[key];
              if (!meta) return null;
              const Icon = meta.icon;
              return (
                <button
                  key={key}
                  onClick={() => addPriority(key)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-text-secondary hover:text-text-primary bg-bg-surface hover:bg-bg-hover border border-border-color hover:border-text-secondary/30 transition-all"
                >
                  <Icon size={14} className={meta.color} />
                  {meta.label}
                  <span className="text-xs opacity-50">+</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Hint */}
      <p className="text-[11px] text-text-secondary leading-relaxed">
        Higher-ranked priorities win when goals conflict. For example, if Accuracy is above Speed,
        Copilot will give a thorough answer even when a shorter one might suffice.
      </p>
    </div>
  );
}
