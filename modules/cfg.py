from __future__ import annotations
import os

MODULE_METADATA = {
    "name": "CFG",
    "type": "class",
    "description": (
        "Central configuration class storing default runtime options, returns dict when called"
        "timeouts, and backend execution settings for the agent."
    ),
    "functions": [
        {
            "name": "get_timeout",
            "inputs": {
                "name": "str",
                "default": "int | None",
            },
            "outputs": "int",
            "description": "Return timeout value for the given timeout key.",
        },
        {
            "name": "as_dict",
            "inputs": {},
            "outputs": "dict",
            "description": "Return all CFG class attributes as a dictionary.",
        },
    ],
    "fields": {
        "RELAY_URL": "str",
        "PROJECT_ROOT": "str",
        "RUNS_DIR": "str",
        "MODEL_OPTIONS": "list",
        "EFFORT_OPTIONS": "list",
        "DEFAULT_MODEL": "str",
        "DEFAULT_EFFORT": "str",
        "DEFAULT_MAX_TOKENS": "int",
        "TIMEOUTS": "dict",
        "MAX_RETRIES": "int",
        "MAX_PLAN_STEPS": "int",
        "MAX_DEBUG_FEEDBACK_LOOPS": "int",
        "MAX_WRITE_REPAIR_ATTEMPTS": "int",
        "MAX_ITERATIONS": "int",
        "MAX_FEEDBACK_LOOPS": "int",
        "MAX_DEBUG_ITERATIONS": "int",
        "MAX_DEBUG_CYCLES": "int",
        "LLM_FEEDBACK_MAX_CHARS": "int",
        "LLM_CONTEXT_MAX_CHARS_PER_FILE": "int",
        "LLM_CONTEXT_MAX_CHARS_PER_SHELL": "int",
        "VERIFIER_FALLBACK_PROVIDER": "str",
        "VERIFIER_FALLBACK_MODEL": "str",
        "READ_COMMANDS": "list",
        "HEAVY_COMMAND_PREFIXES": "list",
        "METAWRITER_MODEL": "str",
        "METAWRITER_EFFORT": "str",
        "BACKGROUND_CONTEXT_ENABLED": "bool",
        "BACKGROUND_CONTEXT_MODEL": "str",
        "BACKGROUND_CONTEXT_EFFORT": "str",
        "BACKGROUND_CONTEXT_MAX_TOKENS": "int",
    },
}


class CFG:
    RELAY_URL = "http://100.116.15.21:8080/awsgm-relay"
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")    )
    MODEL_OPTIONS = [
        None,
        "pro",
        "flash",
    ]

    EFFORT_OPTIONS = [
        "l",
        "m",
        "h",
    ]

    DEFAULT_MODEL = "mini"
    DEFAULT_EFFORT = "m"
    DEFAULT_MAX_TOKENS = 16384

    TIMEOUTS = {
        "planner_call": 240,
        "verifier_call": 240,
        "shell_call": None,
        "debug_call": 240,
        "background_context_call": 240,
        "discussion_call": 240,
    }

    MAX_RETRIES = 2
    MAX_PLAN_STEPS = 10
    MAX_DEBUG_FEEDBACK_LOOPS = 5
    MAX_WRITE_REPAIR_ATTEMPTS = 2
    MAX_ITERATIONS = 20
    MAX_FEEDBACK_LOOPS = 10
    MAX_DEBUG_ITERATIONS = 10
    MAX_DEBUG_CYCLES = 3
    LLM_FEEDBACK_MAX_CHARS = 200000
    LLM_CONTEXT_MAX_CHARS_PER_FILE = 60000
    LLM_CONTEXT_MAX_CHARS_PER_SHELL = 30000
    RUNS_DIR = os.path.join(PROJECT_ROOT, "runs")
    VERIFIER_FALLBACK_PROVIDER = "gemini"
    VERIFIER_FALLBACK_MODEL = "gemini-3.5-flash"
    METAWRITER_MODEL = "mini"
    METAWRITER_PROVIDER = None
    METAWRITER_EFFORT = "m"
    METAWRITER_TOKENS = 4096
    BACKGROUND_CONTEXT_ENABLED = True
    BACKGROUND_CONTEXT_MODEL = "mini"
    BACKGROUND_CONTEXT_EFFORT = "l"
    BACKGROUND_CONTEXT_MAX_TOKENS = 4096
    READ_COMMANDS = ["cat", "head", "tail", "wc"]

    HEAVY_COMMAND_PREFIXES = [
        "python",
        "python3",
        "bash",
        "sh",
    ]

    @classmethod
    def get_timeout(cls, name: str, default: int | None = None) -> int:
        if default is None:
            default = cls.TIMEOUTS["planner_call"]
        return cls.TIMEOUTS.get(name, default)

    @classmethod
    def as_dict(cls) -> dict:
        return {
            "RELAY_URL": cls.RELAY_URL,
            "PROJECT_ROOT": cls.PROJECT_ROOT,
            "RUNS_DIR": cls.RUNS_DIR,
            "MODEL_OPTIONS": list(cls.MODEL_OPTIONS),
            "EFFORT_OPTIONS": list(cls.EFFORT_OPTIONS),
            "DEFAULT_MODEL": cls.DEFAULT_MODEL,
            "DEFAULT_EFFORT": cls.DEFAULT_EFFORT,
            "DEFAULT_MAX_TOKENS": cls.DEFAULT_MAX_TOKENS,
            "TIMEOUTS": dict(cls.TIMEOUTS),
            "MAX_RETRIES": cls.MAX_RETRIES,
            "MAX_PLAN_STEPS": cls.MAX_PLAN_STEPS,
            "MAX_DEBUG_FEEDBACK_LOOPS": cls.MAX_DEBUG_FEEDBACK_LOOPS,
            "MAX_WRITE_REPAIR_ATTEMPTS": cls.MAX_WRITE_REPAIR_ATTEMPTS,
            "MAX_ITERATIONS": cls.MAX_ITERATIONS,
            "MAX_FEEDBACK_LOOPS": cls.MAX_FEEDBACK_LOOPS,
            "MAX_DEBUG_ITERATIONS": cls.MAX_DEBUG_ITERATIONS,
            "MAX_DEBUG_CYCLES": cls.MAX_DEBUG_CYCLES,
            "LLM_FEEDBACK_MAX_CHARS": cls.LLM_FEEDBACK_MAX_CHARS,
            "LLM_CONTEXT_MAX_CHARS_PER_FILE": cls.LLM_CONTEXT_MAX_CHARS_PER_FILE,
            "LLM_CONTEXT_MAX_CHARS_PER_SHELL": cls.LLM_CONTEXT_MAX_CHARS_PER_SHELL,
            "VERIFIER_FALLBACK_PROVIDER": cls.VERIFIER_FALLBACK_PROVIDER,
            "VERIFIER_FALLBACK_MODEL": cls.VERIFIER_FALLBACK_MODEL,
            "READ_COMMANDS": list(cls.READ_COMMANDS),
            "HEAVY_COMMAND_PREFIXES": list(cls.HEAVY_COMMAND_PREFIXES),
            "METAWRITER_MODEL": cls.METAWRITER_MODEL,
            "METAWRITER_PROVIDER": cls.METAWRITER_PROVIDER,
            "METAWRITER_EFFORT": cls.METAWRITER_EFFORT,
            "METAWRITER_TOKENS": cls.METAWRITER_TOKENS,
            "BACKGROUND_CONTEXT_ENABLED": cls.BACKGROUND_CONTEXT_ENABLED,
            "BACKGROUND_CONTEXT_MODEL": cls.BACKGROUND_CONTEXT_MODEL,
            "BACKGROUND_CONTEXT_EFFORT": cls.BACKGROUND_CONTEXT_EFFORT,
            "BACKGROUND_CONTEXT_MAX_TOKENS": cls.BACKGROUND_CONTEXT_MAX_TOKENS,
        }
