"""Eval table CRUD for ollama-queue.

Plain English: Placeholder for eval-specific database operations. Currently,
eval CRUD lives in eval/engine.py which accesses the DB connection directly.
This mixin exists so the Database MRO includes it and future eval DB methods
have a clear home.
"""


class EvalMixin:
    """Eval pipeline database operations.

    Currently empty — eval CRUD is handled by eval/engine.py via direct
    DB connection access. This mixin participates in the Database MRO so
    that eval-specific DB methods can be added here in the future without
    changing the class hierarchy.
    """

    pass
