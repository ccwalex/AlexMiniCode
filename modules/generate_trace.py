from run_state import RunState

MODULE_METADATA = {
    "name": "generate_trace",
    "type": "function",
    "description": "Generate a compact execution trace and summary from a RunState object.",
    "functions": [
        {
            "name": "generate_trace",
            "inputs": {
                "run_state": "RunState"
            },
            "outputs": "dict"
        }
    ]
}

def build_trace_summary(run_state):
    if not isinstance(run_state, RunState):
        raise TypeError("run_state must be a RunState instance")

    status = "running"
    if getattr(run_state, "completed", False):
        if getattr(run_state, "success", None):
            status = "completed_success"
        else:
            status = "completed_failure"

    summary = f"Run ID: {getattr(run_state, 'run_id', 'unknown')}\n"
    summary += f"Task: {getattr(run_state, 'task', 'unknown')}\n"
    summary += f"Status: {status}\n\n"

    calls = len(getattr(run_state, 'api_calls', getattr(run_state, 'calls', [])))
    reads = len(getattr(run_state, 'reads', getattr(run_state, 'read_cache', [])))

    writes = len(getattr(run_state, 'writes', []))
    edits = len(getattr(run_state, 'edits', []))

    if hasattr(run_state, 'file_changes'):
        writes = len([c for c in run_state.file_changes if c.get('operation') == 'write'])
        edits = len([c for c in run_state.file_changes if c.get('operation') == 'edit'])

    shells = len(getattr(run_state, 'shell_outputs', getattr(run_state, 'shells', [])))
    verifier = len(getattr(run_state, 'verifier_decisions', []))
    meta = len(getattr(run_state, 'metadata_refreshes', []))
    errs = len(getattr(run_state, 'errors', []))

    summary += f"Calls: {calls}\n"
    summary += f"Reads: {reads}\n"
    summary += f"Writes: {writes}\n"
    summary += f"Edits: {edits}\n"
    summary += f"Shells: {shells}\n"
    summary += f"Verifier Decisions: {verifier}\n"
    summary += f"Metadata Refreshes: {meta}\n"
    summary += f"Errors: {errs}"
    return summary

def generate_trace(run_state):
    if not isinstance(run_state, RunState):
        raise TypeError("run_state must be a RunState instance")

    calls = getattr(run_state, 'api_calls', getattr(run_state, 'calls', []))
    reads = getattr(run_state, 'reads', getattr(run_state, 'read_cache', []))
    if isinstance(reads, dict):
        reads = list(reads.keys())
        
    writes = getattr(run_state, 'writes', [])
    edits = getattr(run_state, 'edits', [])

    if hasattr(run_state, 'file_changes'):
        writes = [c for c in run_state.file_changes if c.get('operation') == 'write']
        edits = [c for c in run_state.file_changes if c.get('operation') == 'edit']

    shells = getattr(run_state, 'shell_outputs', getattr(run_state, 'shells', []))
    verifier = getattr(run_state, 'verifier_decisions', [])
    meta = getattr(run_state, 'metadata_refreshes', [])
    errs = getattr(run_state, 'errors', [])

    return {
        "task": getattr(run_state, 'task', ''),
        "run_id": getattr(run_state, 'run_id', ''),
        "success": getattr(run_state, 'success', None),
        "completed": getattr(run_state, 'completed', False),
        "summary": build_trace_summary(run_state),
        "stats": {
            "calls": len(calls),
            "reads": len(reads),
            "writes": len(writes),
            "edits": len(edits),
            "shells": len(shells),
            "verifier_decisions": len(verifier),
            "metadata_refreshes": len(meta),
            "errors": len(errs)
        },
        "calls": calls,
        "errors": errs
    }

if __name__ == "__main__":
    rs = RunState(task="Refactor edit pipeline", run_id="abc123")
    
    # Inject properties to mock the expected lists for the self-test
    if not hasattr(rs, 'api_calls'): rs.api_calls = []
    if not hasattr(rs, 'reads'): rs.reads = []
    if not hasattr(rs, 'writes'): rs.writes = []
    if not hasattr(rs, 'edits'): rs.edits = []
    if not hasattr(rs, 'shell_outputs'): rs.shell_outputs = []
    if not hasattr(rs, 'verifier_decisions'): rs.verifier_decisions = []
    if not hasattr(rs, 'metadata_refreshes'): rs.metadata_refreshes = []
    if not hasattr(rs, 'errors'): rs.errors = []

    rs.reads.append(1)
    rs.writes.append(1)
    rs.edits.append(1)
    rs.shell_outputs.append(1)
    rs.verifier_decisions.append(1)
    rs.metadata_refreshes.append(1)

    rs.completed = True
    rs.success = True

    trace = generate_trace(rs)

    assert trace["stats"]["reads"] == 1
    assert trace["stats"]["writes"] == 1
    assert trace["stats"]["edits"] == 1
    assert trace["stats"]["shells"] == 1
    assert trace["completed"] is True
    assert trace["success"] is True

    print(trace["summary"])
    print("\nGENERATE_TRACE SELF TEST PASSED")
