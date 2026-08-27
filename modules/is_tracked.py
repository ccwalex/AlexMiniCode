MODULE_METADATA = {
    "name": "is_tracked",
    "type": "function",
    "description": "Check if a folder is tracked in the agent's configuration.",
    "functions": [
        {
            "name": "is_tracked",
            "inputs": { "folder_path": "str" },
            "outputs": "bool"
        }
    ]
}

import os

from track_folders import TrackFolders


def is_tracked(folder_path: str) -> bool:
    """Check if a folder is currently tracked."""
    tracker = TrackFolders()
    tracker.load()
    
    candidate = os.path.normpath(folder_path)
    tracked_folders = tracker.get_all()
    for folder in tracked_folders:
        tracked = os.path.normpath(str(folder.get("folder_path") or ""))
        if not tracked:
            continue
        if candidate == tracked or candidate.startswith(tracked + os.sep):
            return True
    return False
