"""
Conversation-Aware Adaptation — ported from UI/src/services/conversationStateService.js

Analyzes conversation history to extract state signals, then adapts
behavior settings before they enter the policy compiler.
"""
import re

SENSITIVITY_PATTERN = re.compile(
    r"\b(health|medical|mental health|depression|anxiety|suicide|self.?harm|legal|lawsuit|"
    r"financial|invest(?:ing|ment)?|debt|bankruptcy|death|dying|terminal|diagnosis|medication)\b",
    re.IGNORECASE,
)

URGENCY_PATTERN = re.compile(
    r"\b(asap|urgent(?:ly)?|quick(?:ly)?|due (today|tomorrow|tonight|this week|by \w+)|"
    r"deadline|emergency|right now|need (?:this |it )?now|time.?sensitive|running out of time|"
    r"in a hurry|short on time)\b",
    re.IGNORECASE,
)

DISTRESS_PATTERN = re.compile(
    r"\b(stressed|overwhelmed|anxious|panic(?:king)?|scared|terrified|hopeless|desperate|"
    r"crying|depressed|exhausted|can'?t (?:do|take|handle) (?:this|it)|falling behind|"
    r"failing|failing out|freaking out|burned? out|losing (?:my )?mind|don'?t know what to do)\b",
    re.IGNORECASE,
)

REVIEW_PATTERN = re.compile(
    r"\b(review|check|look over|proofread|edit|revise|feedback|critique|evaluate|assess)\b|"
    r"is this (good|right|correct|okay)\??|does this (look|sound|make sense)\??",
    re.IGNORECASE,
)

QUESTION_PATTERN = re.compile(
    r"\?|^(what|who|where|when|why|how|can|could|would|should|is|are|do|does|did|will)\b",
    re.IGNORECASE,
)

TECHNICAL_PATTERN = re.compile(
    r"\b(algorithm|complexity|asymptotics?|recursion|pointer|heap|stack|runtime|compiler|"
    r"parser|polymorphism|inheritance|encapsulation|abstraction|differential|integral|"
    r"derivative|matrix|eigenvalue|regression|correlation|hypothesis|methodology|dissertation|"
    r"thesis|citation|bibliography|api|rest|json|http|oauth|jwt|sql|nosql|concurrency|"
    r"parallelism|kernel|semaphore|mutex|deadlock|refactor|scaffold|prototype)\b",
    re.IGNORECASE,
)


def analyze_conversation_state(messages: list[dict]) -> dict:
    """Extract state signals from conversation history."""
    user_messages = [m for m in messages if m.get("role") == "user"]
    turn_count = len(user_messages)
    last_user = user_messages[-1].get("content", "") if user_messages else ""

    phase = "opening" if turn_count <= 2 else ("mid" if turn_count <= 8 else "deep")

    if REVIEW_PATTERN.search(last_user):
        task_phase = "reviewing"
    elif QUESTION_PATTERN.search(last_user):
        task_phase = "asking"
    else:
        task_phase = "doing"

    sensitivity = "high" if SENSITIVITY_PATTERN.search(last_user) else "normal"
    urgency = "high" if URGENCY_PATTERN.search(last_user) else "normal"
    emotional_tone = "distressed" if DISTRESS_PATTERN.search(last_user) else "neutral"

    all_user_text = " ".join(m.get("content", "") for m in user_messages)
    has_technical = bool(TECHNICAL_PATTERN.search(all_user_text))
    words = all_user_text.strip().split()
    avg_word_len = sum(len(w) for w in words) / len(words) if words else 0

    if has_technical or avg_word_len > 6.5:
        expertise = "expert"
    elif avg_word_len < 4.5 and turn_count >= 2:
        expertise = "beginner"
    else:
        expertise = "unknown"

    return {
        "phase": phase,
        "task_phase": task_phase,
        "sensitivity": sensitivity,
        "expertise": expertise,
        "emotional_tone": emotional_tone,
        "urgency": urgency,
    }


def adapt_behavior(base_behavior: dict | None, state: dict) -> dict | None:
    """Return a new behavior dict adapted to the conversation state."""
    if not base_behavior:
        return base_behavior

    adapted = {**base_behavior}
    adapted["priority_stack"] = list(adapted.get("priority_stack") or [])
    hints = []

    # 1. Response length
    if state["urgency"] == "high":
        adapted["response_length"] = "concise"
        hints.append("The user is in a hurry. Be brief and direct — give the answer first, skip preamble.")
    elif state["phase"] == "deep" and adapted.get("response_length") != "concise":
        adapted["response_length"] = "concise"
        hints.append("This is a deep conversation. The user has context; skip re-explaining background. Be concise.")
    elif state["task_phase"] == "reviewing":
        adapted["response_length"] = "concise"
        hints.append("The user wants a review. Give direct, actionable feedback without lengthy preamble.")
    elif (
        state["task_phase"] == "asking"
        and state["phase"] != "deep"
        and adapted.get("response_length") == "balanced"
    ):
        adapted["response_length"] = "detailed"
        hints.append("The user is asking a question early in the conversation. Give a thorough, well-explained answer.")

    # 2. Priority stack
    stack = adapted["priority_stack"]
    if state["sensitivity"] == "high":
        if "safety" in stack:
            idx = stack.index("safety")
            if idx > 0:
                stack.pop(idx)
                stack.insert(0, "safety")
        else:
            stack.insert(0, "safety")
        hints.append("The topic involves sensitive content. Prioritize user safety and always include appropriate disclaimers.")

    if state["emotional_tone"] == "distressed":
        if "warmth" in stack:
            idx = stack.index("warmth")
            if idx > 1:
                stack.pop(idx)
                stack.insert(1, "warmth")
        else:
            stack.insert(1, "warmth")
        if adapted.get("response_tone") in ("professional", "academic"):
            adapted["response_tone"] = "friendly"
        hints.append("The user appears emotionally stressed. Acknowledge their situation with warmth before addressing the task.")

    # 3. Expertise hints
    if state["expertise"] == "expert":
        hints.append("The user appears experienced. Skip basic definitions, use field-appropriate terminology.")
    elif state["expertise"] == "beginner":
        hints.append("The user appears newer to this topic. Avoid jargon, explain clearly, use simple examples.")

    if hints:
        adapted["_adaptationHints"] = hints

    return adapted
