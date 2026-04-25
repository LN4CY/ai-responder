"""Lightweight query-complexity classifier for dynamic model routing."""

_COMPLEX_KEYWORDS = frozenset([
    'analyze', 'analyse', 'explain', 'compare', 'plan', 'calculate',
    'debug', 'optimize', 'optimise', 'research', 'strategy',
    'pros and cons', 'step by step', 'how to', 'why is', 'what if',
    'difference between', 'summarize', 'summarise', 'recommend', 'suggest',
    'write code', 'implement', 'design', 'create a', 'build a',
    'write a', 'help me with', 'what are the',
])

_SIMPLE_KEYWORDS = frozenset([
    'hi', 'hello', 'hey', 'ping', 'status', 'time', 'who', 'online',
    'thanks', 'thank you', 'ok', 'okay', 'yes', 'no', 'bye', 'check',
])

_COMPLEX_LENGTH = 80  # chars — longer than this is likely complex
_SIMPLE_LENGTH = 35   # chars — shorter than this with simple words is fast-path simple


def classify_complexity(prompt: str, history=None) -> str:
    """Return 'simple' or 'complex' based on prompt heuristics.

    Simple → use the fast/cheap model.
    Complex → use the thinking/reasoning model.
    """
    text = prompt.lower().strip()
    words = set(text.split())

    # Short greeting or status check → definitely simple
    if len(text) <= _SIMPLE_LENGTH and words & _SIMPLE_KEYWORDS:
        return 'simple'

    # Long messages are almost always multi-step or analytical
    if len(text) > _COMPLEX_LENGTH:
        return 'complex'

    # Keyword scan (substring to catch multi-word phrases)
    for kw in _COMPLEX_KEYWORDS:
        if kw in text:
            return 'complex'

    # A deep conversation context hints at an ongoing complex task
    if history and len(history) >= 6:
        return 'complex'

    return 'simple'
