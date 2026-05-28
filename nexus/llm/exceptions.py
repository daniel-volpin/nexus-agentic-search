class LLMUnavailable(RuntimeError):
    """All configured providers failed."""


class InputTooLarge(ValueError):
    """Input token count exceeded the configured role cap."""


class BudgetExceeded(RuntimeError):
    """Daily USD budget has been exhausted."""


class SynthesisToolsDisabled(ValueError):
    """Caller tried to pass tools to the ``synthesis`` role.

    The synthesis role has tool calling disabled at the API parameter —
    not just by prompt instruction — as defense in depth against
    prompt-injection in crawled content coercing a tool call.
    """
