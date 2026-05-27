class LLMUnavailable(RuntimeError):
    """All configured providers failed."""


class InputTooLarge(ValueError):
    """Input token count exceeded the configured role cap."""


class BudgetExceeded(RuntimeError):
    """Daily USD budget has been exhausted."""


class SynthesisToolsDisabled(ValueError):
    """Caller tried to pass tools to the ``synthesis`` role.

    Spec 10: the synthesis role MUST have tool calling disabled at the
    API parameter — not just by prompt instruction. Defense in depth
    against prompt-injection forcing a tool call from inside crawled
    content.
    """
