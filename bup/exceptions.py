"""Domain and pipeline exceptions with stable HTTP mappings."""


class GridWiseError(Exception):
    """Base error for the optimization pipeline."""


class LLMInterpretationError(GridWiseError):
    """Raised when the language model call fails or returns unusable output."""


class GuardrailValidationError(GridWiseError, ValueError):
    """Raised when LLM output fails deterministic contract checks."""


class OptimizationInfeasibleError(GridWiseError):
    """Raised when the LP solver cannot find a feasible schedule."""
