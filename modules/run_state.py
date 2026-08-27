import time
import uuid
import json

MODULE_METADATA = {
    "name": "RunState",
    "type": "class",
    "description": "Lightweight Gen2 run state container for tracking API calls, file operations, verifier decisions, metadata refreshes, shell outputs, errors, and completion status.",
    "functions": [
        {
            "name": "RunState",
            "inputs": {
                "task": "str original task, default empty",
                "run_id": "str or None optional run identifier"
            },
            "outputs": "RunState object with methods for recording and summarizing one execution run"
        }
    ]
}

class RunState:
    def __init__(self, task="", run_id=None):
        self.task = task
        self.original_task = task
        self.rewritten_task = task
        self.run_id = run_id if run_id is not None else str(uuid.uuid4())
        self.started_at = time.time()
        self.checkpoint = None
        self.calls = []
        self.reads = []
        self.writes = []
        self.edits = []
        self.shells = []
        self.scratchpads = []
        self.verifier_decisions = []
        self.metadata_refreshes = []
        self.errors = []
        self.completed = False
        self.success = None

    def add_call(self, call, status="", output=None):
        record = {
            "timestamp": time.time(),
            "call": call,
            "status": status,
            "output": output
        }
        self.calls.append(record)
        return record

    def add_read(self, path, success, content_preview="", error=""):
        record = {
            "timestamp": time.time(),
            "path": path,
            "success": success,
            "content_preview": content_preview,
            "error": error
        }
        self.reads.append(record)
        return record

    def add_write(self, path, success, reason="", content_hash=""):
        record = {
            "timestamp": time.time(),
            "path": path,
            "success": success,
            "reason": reason,
            "content_hash": content_hash
        }
        self.writes.append(record)
        return record

    def add_edit(self, path, success, reason="", mutation_log=None):
        record = {
            "timestamp": time.time(),
            "path": path,
            "success": success,
            "reason": reason,
            "mutation_log": mutation_log if mutation_log is not None else []
        }
        self.edits.append(record)
        return record

    def add_shell(self, cmd, success, output_preview="", run_id="", output=""):
        record = {
            "timestamp": time.time(),
            "cmd": cmd,
            "success": success,
            "output": output if output else output_preview,
            "output_preview": output_preview,
            "run_id": run_id
        }
        self.shells.append(record)
        return record

    def add_scratchpad(self, action, success, content_preview="", error=""):
        record = {
            "timestamp": time.time(),
            "action": action,
            "success": success,
            "content_preview": content_preview,
            "error": error,
        }
        self.scratchpads.append(record)
        return record

    def add_verifier_decision(self, kind, target, approved, reason=""):
        record = {
            "timestamp": time.time(),
            "kind": kind,
            "target": target,
            "approved": approved,
            "reason": reason
        }
        self.verifier_decisions.append(record)
        return record

    def add_metadata_refresh(self, path, success, reason=""):
        record = {
            "timestamp": time.time(),
            "path": path,
            "success": success,
            "reason": reason
        }
        self.metadata_refreshes.append(record)
        return record

    def add_error(self, stage, error, context=None):
        record = {
            "timestamp": time.time(),
            "stage": stage,
            "error": error,
            "context": context if context is not None else {}
        }
        self.errors.append(record)
        return record

    def mark_completed(self, success=True):
        self.completed = True
        self.success = success

    def to_dict(self):
        return {
            "task": self.task,
            "original_task": self.original_task,
            "rewritten_task": self.rewritten_task,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "checkpoint": self.checkpoint,
            "calls": self.calls,
            "reads": self.reads,
            "writes": self.writes,
            "edits": self.edits,
            "shells": self.shells,
            "scratchpads": self.scratchpads,
            "verifier_decisions": self.verifier_decisions,
            "metadata_refreshes": self.metadata_refreshes,
            "errors": self.errors,
            "completed": self.completed,
            "success": self.success
        }

    def compact_summary(self, max_chars=4000):
        lines = []
        lines.append(f"Run ID: {self.run_id}")
        lines.append(f"Task: {self.task}")
        lines.append(f"Status: {'Completed' if self.completed else 'In Progress'} (Success: {self.success})")
        
        lines.append(f"Calls: {len(self.calls)}")
        lines.append(f"Reads: {len(self.reads)}")
        lines.append(f"Writes: {len(self.writes)}")
        lines.append(f"Edits: {len(self.edits)}")
        lines.append(f"Shells: {len(self.shells)}")
        lines.append(f"Verifier Decisions: {len(self.verifier_decisions)}")
        lines.append(f"Metadata Refreshes: {len(self.metadata_refreshes)}")
        lines.append(f"Errors: {len(self.errors)}")
        
        for err in self.errors:
            lines.append(f"  [ERROR] {err['stage']}: {err['error']}")
            
        summary = "\n".join(lines)
        if len(summary) > max_chars:
            summary = summary[:max_chars - 3] + "..."
        return summary

if __name__ == "__main__":
    rs = RunState(task="demo task")
    rs.add_call({"endpoint": "/read"}, status="ok")
    rs.add_read("test.py", True, "print('hello')")
    rs.add_write("test.py", True, reason="approved")
    rs.add_edit("test.py", True, reason="approved")
    rs.add_shell("ls -la", True, "file1\nfile2")
    rs.add_verifier_decision("write", "test.py", True, reason="looks good")
    rs.add_metadata_refresh("test.py", True, "updated")
    rs.add_error("execution", "timeout")
    rs.mark_completed(success=False)
    
    print(rs.compact_summary())
    
    j = json.dumps(rs.to_dict())
    assert len(rs.calls) == 1
    assert len(rs.reads) == 1
    assert len(rs.writes) == 1
    assert len(rs.edits) == 1
    assert len(rs.shells) == 1
    assert len(rs.verifier_decisions) == 1
    assert len(rs.metadata_refreshes) == 1
    assert len(rs.errors) == 1
    print("Self-test passed.")
