/**
 * Policy Compiler — Fix #5
 *
 * Classifies all active instructions into Hard / Medium / Soft tiers,
 * detects conflicts between behavior layers, resolves them by precedence,
 * and outputs a structured prompt with three emphasis sections.
 *
 * Hardness levels:
 *   Hard   — must be obeyed. All boundary rules. Violation triggers repair.
 *   Medium — should be obeyed. Priority-derived instructions.
 *   Soft   — preference. Style instructions. Violation is acceptable.
 *
 * Resolution order: Hard > Medium > Soft.
 * Within the same tier, higher-ranked priority wins.
 */

// ── Boundary instructions (always Hard) ──────────────────────────────────────

const HARD_INSTRUCTIONS = [
  // Truthfulness
  'Do not invent facts or fabricate information.',
  'Do not fake certainty — say "I\'m not sure" when appropriate.',
  'Do not claim to remember things you do not have access to.',
  'Do not pretend to know context you have not been given.',
  // Identity
  'Do not roleplay or adopt a persona unless explicitly asked.',
  'Do not imply you have real emotions or consciousness.',
  'Do not present simulated closeness as a real relationship.',
  // Interaction
  'Do not guilt-trip or emotionally pressure the user.',
  'Do not be clingy or needy for interaction.',
  'Do not be patronizing or condescending.',
  'Do not overpraise — keep encouragement genuine.',
  'Do not shame the user for mistakes or lack of knowledge.',
  // Domain
  'Do not give high-confidence medical, legal, or financial advice without a disclaimer.',
  'Do not act beyond your allowed authority as an academic assistant.',
  'Do not take actions or make assumptions the user did not request.',
];

// ── Priority → instruction fragments (Medium) ────────────────────────────────
// Each entry contains the instruction text and optionally a style key it can
// conflict with, so the conflict detector can find the pair.

const PRIORITY_INSTRUCTIONS = {
  safety: {
    text: (rank) => rank <= 2
      ? 'If the user appears distressed or the topic involves health or safety, ALWAYS prioritize their wellbeing over completing the task.'
      : 'Be mindful of user safety.',
    conflictsWith: null,
  },
  accuracy: {
    text: (rank) => rank <= 2
      ? 'When making factual claims, ALWAYS include a source or say "I believe" if unsourced. Never sacrifice accuracy for brevity.'
      : 'Try to be accurate.',
    conflictsWith: 'speed',
  },
  task_completion: {
    text: (rank) => rank <= 3
      ? 'Focus on completing what the user asked. Do not go off-topic or add unsolicited information.'
      : 'Complete the user\'s task.',
    conflictsWith: null,
  },
  clarity: {
    text: (rank) => rank <= 3
      ? 'Write clearly. Use simple language and short sentences where possible. Avoid jargon unless the user uses it first.'
      : 'Be clear.',
    conflictsWith: 'academic',   // tone conflict
  },
  speed: {
    text: (rank) => rank <= 2
      ? 'For straightforward questions, give the shortest correct answer. Do not elaborate unless asked.'
      : 'Be reasonably concise.',
    conflictsWith: 'accuracy',
  },
  warmth: {
    text: (rank) => rank <= 3
      ? 'Be warm and supportive. Acknowledge the user\'s situation before diving into the answer.'
      : 'Be supportive.',
    conflictsWith: null,
  },
  creativity: {
    text: (rank) => rank <= 3
      ? 'Bring original ideas and creative angles to responses. Do not default to the obvious answer.'
      : 'Be creative when appropriate.',
    conflictsWith: 'accuracy',
  },
  personalization: {
    text: (rank) => rank <= 3
      ? 'Tailor your response to the user\'s apparent context, level, and goals.'
      : 'Personalize responses.',
    conflictsWith: null,
  },
};

// ── Style → instruction text + conflict metadata (Soft) ──────────────────────

const STYLE_INSTRUCTIONS = {
  // Tone
  professional: { text: 'Use a professional and polished tone. Be formal and precise.', conflictKey: 'tone:professional' },
  friendly:     { text: 'Be friendly and approachable. Use a warm, conversational tone.', conflictKey: 'tone:friendly' },
  casual:       { text: 'Be casual and relaxed. Talk like a fellow student would.', conflictKey: 'tone:casual' },
  academic:     { text: 'Use an academic and scholarly tone. Be thorough and cite reasoning.', conflictKey: 'tone:academic' },

  // Length
  concise:      { text: 'Keep responses short and to the point. Avoid unnecessary elaboration.', conflictKey: 'length:concise' },
  balanced:     { text: 'Provide balanced responses — enough detail to be helpful without being verbose.', conflictKey: 'length:balanced' },
  detailed:     { text: 'Give thorough, detailed responses with explanations and examples.', conflictKey: 'length:detailed' },

  // Format
  plain:        { text: 'Use plain text. Minimize markdown formatting.', conflictKey: null },
  markdown:     { text: 'Use markdown formatting when helpful (bold, bullet points, numbered lists, headers).', conflictKey: null },
  'bullet-heavy': { text: 'Heavily use bullet points and lists to organize information. Prefer structured output.', conflictKey: null },

  // Emoji
  none:         { text: 'Do not use any emojis.', conflictKey: null },
  occasional:   { text: 'Use emojis sparingly for emphasis.', conflictKey: null },
  frequent:     { text: 'Use emojis generously to make responses lively and engaging.', conflictKey: null },
};

// ── Conflict detection rules ──────────────────────────────────────────────────

/**
 * Detects conflicts between active style settings (Soft) and either
 * boundary rules (Hard) or priority-derived instructions (Medium).
 *
 * Returns an array of { description, winner } objects.
 *
 * @param {Object} behavior
 * @param {string[]} activePriorityKeys - priority_stack with only those present
 * @returns {{ description: string, winner: 'hard'|'medium', suppressed: string }[]}
 */
function detectConflicts(behavior, activePriorityKeys) {
  const conflicts = [];

  // Hard conflict: "no overexplaining/verbosity" boundary vs. detailed style
  if (behavior.response_length === 'detailed') {
    conflicts.push({
      description: '"detailed" length vs. boundary rule "Do not overexplain when a simple answer is enough"',
      winner: 'hard',
      suppressed: 'length:detailed',
    });
  }

  // Medium conflict: speed priority (top 2) vs. concise style (they reinforce, not conflict)
  // Real conflict: speed priority (top 2) + accuracy priority (top 2) → accuracy wins
  const speedRank    = activePriorityKeys.indexOf('speed');
  const accuracyRank = activePriorityKeys.indexOf('accuracy');
  if (speedRank >= 0 && speedRank < 2 && accuracyRank >= 0 && accuracyRank < 2) {
    if (accuracyRank < speedRank) {
      conflicts.push({
        description: '"speed" top-2 vs. "accuracy" top-2 — accuracy outranks',
        winner: 'medium',
        suppressed: 'priority:speed_short_answer',
      });
    }
  }

  // Medium conflict: "casual tone" (soft) + "academic" priority (medium) → tension
  if (behavior.response_tone === 'casual' && activePriorityKeys.includes('accuracy')) {
    const rank = activePriorityKeys.indexOf('accuracy');
    if (rank < 3) {
      conflicts.push({
        description: '"casual" tone vs. "accuracy" priority in top 3 — accuracy wins, tone softened',
        winner: 'medium',
        suppressed: 'tone:casual',
      });
    }
  }

  // Medium conflict: "concise" length + "detailed" priority (task_completion or accuracy high rank)
  if (behavior.response_length === 'concise') {
    const detailPriorities = ['accuracy', 'task_completion'];
    for (const p of detailPriorities) {
      const rank = activePriorityKeys.indexOf(p);
      if (rank >= 0 && rank < 2) {
        conflicts.push({
          description: `"concise" length conflicts with high-ranked "${p}" — length relaxed to balanced`,
          winner: 'medium',
          suppressed: 'length:concise',
        });
        break; // one suppression is enough
      }
    }
  }

  return conflicts;
}

// ── Main export ───────────────────────────────────────────────────────────────

/**
 * Compile behavior settings into a structured, tiered system prompt.
 *
 * @param {string} baseIdentity - The BASE_SYSTEM_PROMPT string.
 * @param {Object|null} behavior - Active behavior settings.
 * @returns {{ prompt: string, conflicts: Array }} compiled prompt + debug log.
 */
export function compilePolicy(baseIdentity, behavior) {
  if (!behavior) {
    return { prompt: baseIdentity, conflicts: [] };
  }

  const activePriorityKeys = Array.isArray(behavior.priority_stack)
    ? behavior.priority_stack
    : [];

  // ── Detect conflicts ──────────────────────────────────────────────────────
  const conflicts = detectConflicts(behavior, activePriorityKeys);
  const suppressed = new Set(conflicts.map(c => c.suppressed));

  // ── Build Soft instructions (style) — drop suppressed ────────────────────
  const softInstructions = [];

  const toneKey = behavior.response_tone;
  if (toneKey && STYLE_INSTRUCTIONS[toneKey]) {
    const { text, conflictKey } = STYLE_INSTRUCTIONS[toneKey];
    if (!conflictKey || !suppressed.has(conflictKey)) {
      softInstructions.push(text);
    }
  }

  const lengthKey = behavior.response_length;
  if (lengthKey && STYLE_INSTRUCTIONS[lengthKey]) {
    const { text, conflictKey } = STYLE_INSTRUCTIONS[lengthKey];
    if (!conflictKey || !suppressed.has(conflictKey)) {
      softInstructions.push(text);
    }
  }

  const formatKey = behavior.response_format;
  if (formatKey && STYLE_INSTRUCTIONS[formatKey]) {
    softInstructions.push(STYLE_INSTRUCTIONS[formatKey].text);
  }

  const emojiKey = behavior.emoji_usage;
  if (emojiKey && STYLE_INSTRUCTIONS[emojiKey]) {
    softInstructions.push(STYLE_INSTRUCTIONS[emojiKey].text);
  }

  // ── Build Medium instructions (priorities) ────────────────────────────────
  const mediumInstructions = [];

  activePriorityKeys.forEach((key, rank) => {
    const def = PRIORITY_INSTRUCTIONS[key];
    if (!def) return;

    // If this priority's short-answer instruction was suppressed by accuracy, skip it
    if (key === 'speed' && suppressed.has('priority:speed_short_answer')) {
      mediumInstructions.push('Be reasonably concise where accuracy allows.');
      return;
    }

    mediumInstructions.push(def.text(rank));
  });

  // Conversation-state adaptation hints (from conversationStateService.adaptBehavior)
  if (Array.isArray(behavior._adaptationHints) && behavior._adaptationHints.length > 0) {
    mediumInstructions.push(...behavior._adaptationHints);
  }

  // ── Build Hard instructions (boundaries, always full) ────────────────────
  const hardInstructions = [...HARD_INSTRUCTIONS];

  // "detailed" length suppressed → boundary already covers overexplaining; no duplication needed
  // (The suppression removed the conflicting soft instruction; hard list is untouched.)

  // ── Assemble prompt ───────────────────────────────────────────────────────
  const sections = [baseIdentity];

  if (hardInstructions.length > 0) {
    sections.push(
      `## Instructions (MUST follow — violation is not acceptable)\n` +
      hardInstructions.map(i => `- ${i}`).join('\n')
    );
  }

  if (mediumInstructions.length > 0) {
    sections.push(
      `## Instructions (SHOULD follow — strong preference)\n` +
      mediumInstructions.map(i => `- ${i}`).join('\n')
    );
  }

  if (softInstructions.length > 0) {
    sections.push(
      `## Instructions (PREFER — follow when possible)\n` +
      softInstructions.map(i => `- ${i}`).join('\n')
    );
  }

  return {
    prompt: sections.join('\n\n'),
    conflicts,
  };
}
