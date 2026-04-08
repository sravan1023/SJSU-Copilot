/**
 * Lightweight post-generation validator pipeline.
 *
 * Runs between LLM generation and display to enforce behavior boundaries.
 * Three escalation levels:
 *   Level 1 (soft)   — auto-rewrite via a second API call, transparent to user
 *   Level 2 (medium) — append a visible warning below the response
 *   Level 3 (hard)   — block response entirely (reserved for future use)
 */

// ── Thresholds ────────────────────────────────────────────────────────────────

const CONCISE_WORD_LIMIT = 350;

// ── Warning text ──────────────────────────────────────────────────────────────

const DOMAIN_WARNING =
  '\n\n---\n⚠️ *This response may contain medical, legal, or financial information. Please consult a qualified professional before making any decisions.*';

const UNVERIFIED_WARNING =
  '\n\n---\n⚠️ *Some claims in this response may not be fully verified. Please double-check important information.*';

// ── Detectors ─────────────────────────────────────────────────────────────────

const DOMAIN_TOPICS_PATTERN =
  /\b(diagnos(is|ed|e)|symptom|treatment|medication|medicine|disease|illness|disorder|therapy|prescription|dosage|surgery|health condition|invest(ment|ing)|stock portfolio|financial advice|tax advice|retirement fund|securities|legal advice|attorney|lawsuit|liability|court order|plaintiff|defendant)\b/i;

const DISCLAIMER_PRESENT_PATTERN =
  /\b(consult (a |your )?(doctor|physician|lawyer|attorney|financial advisor)|not (medical|legal|financial) advice|speak (to|with) (a |your )?(doctor|physician|professional)|professional (advice|judgment)|I('m| am) not (a |your )?(doctor|lawyer|financial advisor)|disclaimer)\b/i;

const CERTAINTY_VIOLATION_PATTERN =
  /\b(I('m| am) (absolutely|completely|100%) (sure|certain)|this is definitely (true|correct|accurate)|it('s| is) definitely (true|correct)|always works|never fails|I guarantee\b|without (any |a )?doubt|100% (accurate|correct|true))\b/i;

// ── Helpers ───────────────────────────────────────────────────────────────────

function countWords(text) {
  return text.trim().split(/\s+/).length;
}

// ── Main export ───────────────────────────────────────────────────────────────

/**
 * Run the lightweight validator pipeline on a completed LLM response.
 *
 * @param {string} response - The full generated response text.
 * @param {Object|null} behavior - The active behavior settings object.
 * @returns {{
 *   violations: Array<{category: string, rule: string, severity: string}>,
 *   action: null|'rewrite'|'warn',
 *   warningText: string|null,
 *   repairPrompt: string|null,
 * }}
 */
export function runValidators(response, behavior) {
  const violations = [];

  // 1. Length check — Level 1 auto-rewrite
  if (behavior?.response_length === 'concise' && countWords(response) > CONCISE_WORD_LIMIT) {
    violations.push({
      category: 'Style',
      rule: 'response_too_long_for_concise',
      severity: 'soft',
    });
  }

  // 2. Domain disclaimer missing — Level 2 warn
  const hasDomainContent = DOMAIN_TOPICS_PATTERN.test(response);
  const hasDisclaimer = DISCLAIMER_PRESENT_PATTERN.test(response);
  if (hasDomainContent && !hasDisclaimer) {
    violations.push({
      category: 'Domain',
      rule: 'domain_advice_without_disclaimer',
      severity: 'medium',
    });
  }

  // 3. Absolute certainty language — Level 2 warn
  if (CERTAINTY_VIOLATION_PATTERN.test(response)) {
    violations.push({
      category: 'Truthfulness',
      rule: 'absolute_certainty_language',
      severity: 'medium',
    });
  }

  if (violations.length === 0) {
    return { violations: [], action: null, warningText: null, repairPrompt: null };
  }

  // Level 1: soft violations → auto-rewrite takes priority
  if (violations.some(v => v.severity === 'soft')) {
    return {
      violations,
      action: 'rewrite',
      warningText: null,
      repairPrompt:
        `The following response is too long. The user has "concise" mode enabled. ` +
        `Rewrite it to be under 120 words while keeping all key information. ` +
        `Return only the rewritten response, no preamble:\n\n${response}`,
    };
  }

  // Level 2: medium violations → append warning below response
  const warnings = [];
  if (violations.some(v => v.rule === 'domain_advice_without_disclaimer')) {
    warnings.push(DOMAIN_WARNING);
  }
  if (
    violations.some(v => v.rule === 'absolute_certainty_language') &&
    !violations.some(v => v.rule === 'domain_advice_without_disclaimer')
  ) {
    warnings.push(UNVERIFIED_WARNING);
  }

  return {
    violations,
    action: 'warn',
    warningText: warnings.join(''),
    repairPrompt: null,
  };
}
