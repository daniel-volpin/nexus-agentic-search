class LLMUnavailable(RuntimeError):
    """All configured providers failed."""


class InputTooLarge(ValueError):
    """Input token count exceeded the configured role cap."""


class BudgetExceeded(RuntimeError):
    """Daily USD budget has been exhausted."""
