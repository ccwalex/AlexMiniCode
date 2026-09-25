#!/usr/bin/env python3
"""Generate dummy verifier / metadata-writer harness requests for arena.ai testing."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = ROOT / "modules"
if str(MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(MODULES_DIR))

from build_py_meta_prompt import build_py_meta_prompt
from build_write_verifier_prompt import build_write_verifier_prompt
from cfg import CFG


EXAMPLE_DIR = Path(__file__).resolve().parent
SAMPLE_CODE_PATH = EXAMPLE_DIR / "sample_image_csv_dataset.py"
OUTPUT_DIR = EXAMPLE_DIR / "arena_requests"

MINIMAL_MEMORY = {
    "agent_memory/core/principles.md": """# Project principles (dummy harness context)

- Keep dataset code self-contained and import-safe.
- Validate CSV columns before indexing rows.
- Raise clear errors for missing image files.
- Prefer pathlib.Path over raw string joins for filesystem paths.
""",
    "agent_memory/core/metadata.json": """{
  "code/modules": {
    "ImageCsvDataset": {
      "type": "class",
      "description": "PyTorch Dataset for local image folders with paired CSV manifests.",
      "path": "code/modules/image_csv_dataset.py"
    }
  }
}
""",
    "agent_memory/reasoning/llm_memory.json": "{}\n",
    "agent_memory/reasoning/failures.md": "# Recent failures\n\n- None for this dummy harness export.\n",
}


def ensure_minimal_agent_memory(project_root: Path) -> None:
    for rel_path, content in MINIMAL_MEMORY.items():
        target = project_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def load_sample_code() -> str:
    return SAMPLE_CODE_PATH.read_text(encoding="utf-8")


def build_verifier_request(code: str, path: str = "code/modules/image_csv_dataset.py") -> dict:
    ensure_minimal_agent_memory(ROOT)
    CFG.PROJECT_ROOT = str(ROOT)
    step = {
        "action": "write_file",
        "path": path,
        "content": code,
    }
    system_prompt, user_prompt = build_write_verifier_prompt(
        step,
        file_context_override={
            "selected_files": [
                "code/modules/image_csv_dataset.py",
                "data/images/manifest.csv",
            ],
            "notes": "Planner is adding a torchvision-style dataset for local image folders.",
        },
    )
    return {
        "harness_role": "verifier",
        "description": "Approve or reject a proposed /write of a PyTorch Image+CSV Dataset.",
        "source_file": str(SAMPLE_CODE_PATH),
        "proposed_step": step,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "expected_response_schema": {
            "approved": "bool",
            "reason": "short string",
        },
        "arena_instructions": (
            "Paste system + user messages into arena.ai. "
            "The model must return ONLY JSON: "
            '{"approved": true|false, "reason": "..."}'
        ),
    }


def build_metadata_request(code: str, path: str = "code/modules/image_csv_dataset.py") -> dict:
    system_prompt, user_prompt = build_py_meta_prompt(code, path=path)
    return {
        "harness_role": "meta_writer",
        "description": "Generate MODULE_METADATA-compatible JSON for the PyTorch dataset module.",
        "source_file": str(SAMPLE_CODE_PATH),
        "target_path": path,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "expected_response_schema": {
            "name": "ImageCsvDataset",
            "type": "class",
            "description": "str",
            "functions": [
                {
                    "name": "str",
                    "inputs": {"arg": "type / shape"},
                    "outputs": "type / shape",
                }
            ],
        },
        "arena_instructions": (
            "Paste system + user messages into arena.ai. "
            "The model must return ONLY one JSON object (no markdown fences)."
        ),
    }


def build_bad_verifier_request() -> dict:
    """Variant with an obvious bug for testing verifier rejection on arena.ai."""
    bad_code = load_sample_code().replace(
        "label = int(row[\"label\"])",
        "label = row[\"label\"]",
    )
    req = build_verifier_request(bad_code)
    req["description"] = (
        "Verifier should likely reject: label stays a pandas scalar/object instead of int."
    )
    req["expected_verdict"] = {
        "approved": False,
        "reason_hint": "label type mismatch / missing int cast for Dataset target",
    }
    return req


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    code = load_sample_code()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    verifier_ok = build_verifier_request(code)
    metadata = build_metadata_request(code)
    verifier_bad = build_bad_verifier_request()

    write_json(OUTPUT_DIR / "verifier_write_ok.json", verifier_ok)
    write_json(OUTPUT_DIR / "meta_writer.json", metadata)
    write_json(OUTPUT_DIR / "verifier_write_bad.json", verifier_bad)

    bundle = {
        "task_summary": "PyTorch Dataset: local image folder + paired CSV manifest",
        "sample_code_path": str(SAMPLE_CODE_PATH),
        "csv_manifest_path": str(EXAMPLE_DIR / "manifest.csv"),
        "requests": {
            "verifier_write_ok": "verifier_write_ok.json",
            "verifier_write_bad": "verifier_write_bad.json",
            "meta_writer": "meta_writer.json",
        },
        "arena_usage": [
            "Open arena.ai and choose a model side.",
            "For verifier tests: paste messages[0] as system, messages[1] as user.",
            "For metadata tests: same message order from meta_writer.json.",
            "Compare model JSON against expected_response_schema / expected_verdict.",
        ],
    }
    write_json(OUTPUT_DIR / "README_bundle.json", bundle)

    print(f"Wrote harness requests to {OUTPUT_DIR}")
    for name in ("verifier_write_ok.json", "meta_writer.json", "verifier_write_bad.json", "README_bundle.json"):
        print(f"  - {name}")


if __name__ == "__main__":
    main()
