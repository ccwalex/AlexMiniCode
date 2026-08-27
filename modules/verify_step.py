import json

from clean_python_content import clean_python_content
from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata
from validate_metadata_matches_code import validate_metadata_matches_code
from build_shell_verifier_prompt import build_shell_verifier_prompt
from build_write_verifier_prompt import build_write_verifier_prompt
import hashlib


MODULE_METADATA = {
    "name": "verify_step",
    "type": "function",
    "description": "Verify a proposed agent action using hardcoded safety checks, metadata validation, LLM memory checks, and optional verifier-model audit.",
    "functions": [
        {
            "name": "verify_step",
            "inputs": {
                "step": "dict representing one proposed agent action",
                "modules_override": "dict/list or None; optional module registry override for write_file verifier prompt",
                "read_cache": "dict or None; paths already read in the current task"
            },
            "outputs": "dict with approved bool, reason str, and optional command/metadata/content_hash fields"
        }
    ]
}


def verify_step(step, modules_override=None, read_cache=None):
    def hash_content(content):
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    def load_llm_memory():
        path = safe_path("agent_memory/reasoning/llm_memory.json")
    
        if not os.path.exists(path):
            return []
    
        try:
            return json.load(open(path))
        except:
            return []
    def call_verifier_model(
        system_prompt,
        user_prompt,
        thinking="medium",
        max_tokens=8192,
    ):
        """
        Verifier model call with automatic Gemini fallback on timeout.
        """
    
        base_payload = {
            "max_tokens": max_tokens,
            "thinking": thinking,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        }
    
        try:
            r = requests.post(
                AWS_RELAY_URL,
                json=base_payload,
                timeout=VERIFIER_TIMEOUT,
            )
            return r.json()
    
        except requests.exceptions.Timeout:
            print(
                f"⚠️ verifier timed out on primary route after {VERIFIER_TIMEOUT}s; "
                f"retrying with {VERIFIER_FALLBACK_MODEL}"
            )
    
            fallback_payload = dict(base_payload)
            fallback_payload["provider"] = VERIFIER_FALLBACK_PROVIDER
            fallback_payload["model"] = VERIFIER_FALLBACK_MODEL
    
            if VERIFIER_FALLBACK_PROVIDER == "gemini":
                fallback_payload["gemini"] = {
                    "enterprise": True,
                    "location": "global",
                    "api_version": "v1",
                    "response_mime_type": "application/json",
                }
                fallback_payload['thinking'] = 'low'
    
            try:
                r = requests.post(
                    AWS_RELAY_URL,
                    json=fallback_payload,
                    timeout=VERIFIER_TIMEOUT,
                )
                return r.json()
    
            except Exception as fallback_e:
                return {
                    "approved": False,
                    "reason": (
                        "verifier fallback failure after primary timeout: "
                        f"{str(fallback_e)}"
                    ),
                }
    
        except Exception as e:
            return {
                "approved": False,
                "reason": f"verifier failure: {str(e)}",
            }
    if read_cache is None:
        read_cache = {}

    action = step.get("action", "")
    cmd = step.get("cmd", "")
    path = step.get("path", "")
    content = step.get("content", "")

    # =========================
    # READ FILE DUPLICATE CHECK
    # =========================

    if action == "read_file":
        if not path:
            return {
                "approved": False,
                "reason": "read_file requires path"
            }

        if path in read_cache:
            return {
                "approved": False,
                "reason": (
                    f"{path} has already been read in this task. "
                    "Use attached_file context instead of reading it again."
                )
            }

    # =========================
    # HARD RULES
    # =========================

    joined = " ".join([str(cmd), str(path)])

    forbidden = ["rm -rf", "sudo", "/etc", ".."]

    if any(f in joined for f in forbidden):
        return {
            "approved": False,
            "reason": f"unsafe pattern detected: {joined}"
        }

    if action == "run_shell":
        if any(x in cmd for x in [">", ">>", "<<"]):
            return {
                "approved": False,
                "reason": "Use write_file instead of shell redirection"
            }

        write_like_patterns = [
            "tee ",
            "sed -i",
            "perl -pi",
            "cat >",
            "echo >"
        ]

        if any(p in cmd for p in write_like_patterns):
            return {
                "approved": False,
                "reason": "Shell command appears to modify files; use write_file instead"
            }

    # =========================
    # BASIC SHAPE CHECKS
    # =========================

    if action == "write_file":
        if not path:
            return {
                "approved": False,
                "reason": "write_file missing path"
            }

        if "content" not in step:
            return {
                "approved": False,
                "reason": "write_file missing content"
            }

        if path.endswith(".py"):
            content = clean_python_content(content)
            step["content"] = content

    elif action == "run_shell":
        if not cmd:
            return {
                "approved": False,
                "reason": "run_shell missing cmd"
            }

    elif action == "read_file":
        return {
            "approved": True,
            "reason": "read_file approved"
        }

    elif action in ["request_feedback", "reset_context"]:
        return {
            "approved": True,
            "reason": "control action approved"
        }

    elif action == "write_llm_memory":
        if not step.get("issue") or not step.get("solution"):
            return {
                "approved": False,
                "reason": "write_llm_memory missing issue or solution"
            }

        return {
            "approved": True,
            "reason": "memory write shape approved"
        }

    else:
        return {
            "approved": False,
            "reason": f"unknown action: {action}"
        }

    # =========================
    # MODULE METADATA EXTRACTION
    # =========================

    verified_metadata = None
    """
    if action == "write_file" and path.startswith("code/modules/") and path.endswith(".py"):
        meta, err = extract_module_metadata_from_content(content)

        if err:
            return {
                "approved": False,
                "reason": err
            }

        ok, reason = validate_module_metadata(meta)

        if not ok:
            return {
                "approved": False,
                "reason": reason
            }

        ok, reason = validate_metadata_matches_code(meta, content)

        if not ok:
            return {
                "approved": False,
                "reason": reason
            }

        verified_metadata = meta
    """
    # =========================
    # LLM MEMORY CHECK
    # =========================

    memory = load_llm_memory()
    text_for_check = json.dumps(step, ensure_ascii=False).lower()

    for m in memory:
        issue = str(m.get("issue", "")).lower()
        check = str(m.get("check", "")).lower()

        if check and check in text_for_check:
            return {
                "approved": False,
                "reason": f"violates known check: {check}"
            }

        if len(issue) > 12 and issue in text_for_check:
            return {
                "approved": False,
                "reason": f"violates known issue: {issue}"
            }

    # =========================
    # MODEL AUDIT
    # =========================

    system = None
    user = None
    thinking = "medium"
    max_tokens = 8192

    if action == "run_shell":
        system, user = build_shell_verifier_prompt(step)

    elif action == "write_file":
        system, user = build_write_verifier_prompt(
            step,
            modules_override=modules_override,
        )
        thinking = "medium"
        max_tokens = 8192

    if system and user:
        audit = call_verifier_model(system, user, thinking, max_tokens)

        if isinstance(audit, dict):
            if audit.get("approved") is False:
                result = {
                    "approved": False,
                    "reason": audit.get("reason", "model audit rejected")
                }

                if action == "run_shell" and isinstance(audit.get("command"), str):
                    result["command"] = audit["command"].strip()

                return result

            if audit.get("approved") is True:
                result = {
                    "approved": True,
                    "reason": audit.get("reason", "model audit approved")
                }

                if verified_metadata is not None:
                    result["metadata"] = verified_metadata
                    result["content_hash"] = hash_content(content)

                return result

        return {
            "approved": False,
            "reason": f"verifier response malformed: {str(audit)[:300]}"
        }

    return {
        "approved": False,
        "reason": "no verifier path reached"
    }