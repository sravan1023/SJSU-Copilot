/**
 * Conversation-Aware Adaptation — Fix #8
 *
 * Analyzes the current conversation history to extract state signals, then
 * adapts the active behavior settings before they enter the policy compiler.
 * This makes every response context-aware without requiring explicit user input.
 *
 * Six signals tracked:
 *   phase         — opening / mid / deep  (turn count)
 *   taskPhase     — asking / doing / reviewing  (last user message intent)
 *   sensitivity   — high / normal  (sensitive-topic keywords)
 *   expertise     — expert / beginner / unknown  (vocabulary heuristic)
 *   emotionalTone — distressed / neutral  (distress keywords)
 *   urgency       — high / normal  (deadline / speed keywords)
 */

// ── Pattern matchers ──────────────────────────────────────────────────────────

const SENSITIVITY_PATTERN =
  /\b(health|medical|mental health|depression|anxiety|suicide|self.?harm|legal|lawsuit|financial|invest(ing|ment)?|debt|bankruptcy|death|dying|terminal|diagnosis|medication)\b/i;

const URGENCY_PATTERN =
  /\b(asap|urgent(ly)?|quick(ly)?|due (today|tomorrow|tonight|this week|by \w+)|deadline|emergency|right now|need (this |it )?now|time.?sensitive|running out of time|in a hurry|short on time)\b/i;

const DISTRESS_PATTERN =
  /\b(stressed|overwhelmed|anxious|panic(king)?|scared|terrified|hopeless|desperate|crying|depressed|exhausted|can'?t (do|take|handle) (this|it)|falling behind|failing|failing out|freaking out|burned? out|losing (my )?mind|don'?t know what to do)\b/i;

const REVIEW_PATTERN =
  /\b(review|check|look over|proofread|edit|revise|feedback|critique|evaluate|assess)\b|is this (good|right|correct|okay)\??|does this (look|sound|make sense)\??/i;

const QUESTION_PATTERN =
  /\?|^(what|who|where|when|why|how|can|could|would|should|is|are|do|does|did|will)\b/i;

// Technical vocabulary common to CS, math, and academic disciplines
const TECHNICAL_PATTERN =
  /\b(algorithm|complexity|asymptotics?|recursion|pointer|heap|stack|runtime|compiler|parser|polymorphism|inheritance|encapsulation|abstraction|differential|integral|derivative|matrix|eigenvalue|regression|correlation|hypothesis|methodology|dissertation|thesis|citation|bibliography|api|rest|json|http|oauth|jwt|sql|nosql|concurrency|parallelism|kernel|semaphore|mutex|deadlock|refactor|scaffold|prototype)\b/i;

// ── analyzeConversationState ──────────────────────────────────────────────────

/**
 * Extract state signals from the conversation history.
 *
 * @param {Array<{role: 'user'|'assistant', content: string}>} messages
 * @returns {{
 *   phase:         'opening'|'mid'|'deep',
 *   taskPhase:     'asking'|'doing'|'reviewing',
 *   sensitivity:   'high'|'normal',
 *   expertise:     'expert'|'beginner'|'unknown',
 *   emotionalTone: 'distressed'|'neutral',
 *   urgency:       'high'|'normal',
 * }}
 */
export function analyzeConversationState(messages) {
  const userMessages = messages.filter(m => m.role === 'user');
  const turnCount = userMessages.length;
  const lastUserContent = userMessages[userMessages.length - 1]?.content || '';

  // Conversation phase by turn depth
  const phase =
    turnCount <= 2 ? 'opening' :
    turnCount <= 8 ? 'mid'     : 'deep';

  // Task phase from the last user message
  const taskPhase =
    REVIEW_PATTERN.test(lastUserContent)    ? 'reviewing' :
    QUESTION_PATTERN.test(lastUserContent)  ? 'asking'    : 'doing';

  // Sensitivity and urgency from the last user message
  const sensitivity   = SENSITIVITY_PATTERN.test(lastUserContent) ? 'high'      : 'normal';
  const urgency       = URGENCY_PATTERN.test(lastUserContent)     ? 'high'      : 'normal';
  const emotionalTone = DISTRESS_PATTERN.test(lastUserContent)    ? 'distressed': 'neutral';

  // Expertise heuristic — combine all user messages
  const allUserText = userMessages.map(m => m.content).join(' ');
  const hasTechnicalVocab = TECHNICAL_PATTERN.test(allUserText);
  const words = allUserText.trim().split(/\s+/).filter(Boolean);
  const avgWordLength = words.length > 0
    ? words.reduce((sum, w) => sum + w.length, 0) / words.length
    : 0;

  const expertise =
    hasTechnicalVocab || avgWordLength > 6.5 ? 'expert'  :
    avgWordLength < 4.5 && turnCount >= 2     ? 'beginner': 'unknown';

  return { phase, taskPhase, sensitivity, expertise, emotionalTone, urgency };
}

// ── adaptBehavior ─────────────────────────────────────────────────────────────

/**
 * Return a new behavior object with fields adapted to the conversation state.
 * Does not mutate the input. Stores human-readable context hints in
 * `_adaptationHints` for the policy compiler to emit in the SHOULD section.
 *
 * @param {Object|null} baseBehavior
 * @param {ReturnType<typeof analyzeConversationState>} state
 * @returns {Object}
 */
export function adaptBehavior(baseBehavior, state) {
  if (!baseBehavior) return baseBehavior;

  const adapted = {
    ...baseBehavior,
    priority_stack: Array.isArray(baseBehavior.priority_stack)
      ? [...baseBehavior.priority_stack]
      : [],
  };

  const hints = [];

  // ── 1. Response length ────────────────────────────────────────────────────
  if (state.urgency === 'high') {
    adapted.response_length = 'concise';
    hints.push('The user is in a hurry. Be brief and direct — give the answer first, skip preamble.');
  } else if (state.phase === 'deep' && adapted.response_length !== 'concise') {
    adapted.response_length = 'concise';
    hints.push('This is a deep conversation. The user has context; skip re-explaining background. Be concise.');
  } else if (state.taskPhase === 'reviewing') {
    adapted.response_length = 'concise';
    hints.push('The user wants a review. Give direct, actionable feedback without lengthy preamble.');
  } else if (
    state.taskPhase === 'asking' &&
    state.phase !== 'deep' &&
    adapted.response_length === 'balanced'
  ) {
    adapted.response_length = 'detailed';
    hints.push('The user is asking a question early in the conversation. Give a thorough, well-explained answer.');
  }

  // ── 2. Priority stack ─────────────────────────────────────────────────────
  // Sensitive topic → safety to top-1
  if (state.sensitivity === 'high') {
    const idx = adapted.priority_stack.indexOf('safety');
    if (idx > 0) {
      adapted.priority_stack.splice(idx, 1);
      adapted.priority_stack.unshift('safety');
    } else if (idx === -1) {
      adapted.priority_stack.unshift('safety');
    }
    hints.push('The topic involves sensitive content (health, legal, financial, etc.). Prioritize user safety and always include appropriate disclaimers.');
  }

  // Distressed user → warmth to top-2, soften tone
  if (state.emotionalTone === 'distressed') {
    const warmthIdx = adapted.priority_stack.indexOf('warmth');
    if (warmthIdx > 1) {
      adapted.priority_stack.splice(warmthIdx, 1);
      adapted.priority_stack.splice(1, 0, 'warmth');
    } else if (warmthIdx === -1) {
      adapted.priority_stack.splice(1, 0, 'warmth');
    }
    if (adapted.response_tone === 'professional' || adapted.response_tone === 'academic') {
      adapted.response_tone = 'friendly';
    }
    hints.push('The user appears emotionally stressed. Acknowledge their situation with warmth before addressing the task. Do not be dismissive of their feelings.');
  }

  // ── 3. Expertise context hints ────────────────────────────────────────────
  if (state.expertise === 'expert') {
    hints.push('The user appears experienced in this area. Skip basic definitions, use field-appropriate terminology, and engage at an advanced level.');
  } else if (state.expertise === 'beginner') {
    hints.push('The user appears to be newer to this topic. Avoid jargon, explain each concept clearly, and use simple, relatable examples.');
  }

  // Attach hints for the policy compiler — not persisted to DB as a settings column
  if (hints.length > 0) {
    adapted._adaptationHints = hints;
  }

  return adapted;
}
