from __future__ import annotations
import csv
import os
from cfg import CFG
from ensure_memory_files import ensure_memory_files

MODULE_METADATA = {
    "name": "TrackFolders",
    "type": "class",
    "description": "Manages tracked folders and their code types using a dependency-free CSV store.",
    "functions": [
        {
            "name": "__init__",
            "inputs": {
                "csv_path": "str | None"
            },
            "outputs": "None",
            "description": "Initialize with an optional custom CSV path, defaulting to agent_memory/core/tracked_folders.csv"
        },
        {
            "name": "add_folder",
            "inputs": {
                "folder_path": "str",
                "code_type": "str",
                "metadata_path": "str"
            },
            "outputs": "None",
            "description": "Add or update a tracked folder."
        },
        {
            "name": "remove_folder",
            "inputs": {
                "folder_path": "str"
            },
            "outputs": "bool",
            "description": "Remove a tracked folder. Returns True if removed, False if not found."
        },
        {
            "name": "get_all",
            "inputs": {},
            "outputs": "list[dict]",
            "description": "Get all tracked folders as a list of dicts."
        },
        {
            "name": "get_dataframe",
            "inputs": {},
            "outputs": "list[dict]",
            "description": "Get a backward-compatible tabular copy of tracked folder rows."
        },
        {
            "name": "save",
            "inputs": {},
            "outputs": "None",
            "description": "Persist tracked folder rows to CSV."
        },
        {
            "name": "load",
            "inputs": {},
            "outputs": "None",
            "description": "Load tracked folder rows from CSV."
        }
    ]
}

class TrackFolders:
    def __init__(self, csv_path: str | None = None):
        if csv_path is None:
            ensure_memory_files()
            self.csv_path = os.path.join(CFG.PROJECT_ROOT, "agent_memory", "core", "tracked_folders.csv")
        else:
            self.csv_path = csv_path
        self.rows = []
        self.load()

    def load(self) -> None:
        if os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, "r", encoding="utf-8", newline="") as handle:
                    self.rows = [
                        {
                            "folder_path": row.get("folder_path", ""),
                            "code_type": row.get("code_type", ""),
                            "metadata_path": row.get("metadata_path", ""),
                        }
                        for row in csv.DictReader(handle)
                    ]
            except Exception:
                self.rows = []
        else:
            self.rows = []

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        with open(self.csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["folder_path", "code_type", "metadata_path"],
            )
            writer.writeheader()
            writer.writerows(self.rows)

    def add_folder(self, folder_path: str, code_type: str, metadata_path: str) -> None:
        folder_path = os.path.normpath(folder_path)
        metadata_path = os.path.normpath(metadata_path)
        for row in self.rows:
            if row["folder_path"] == folder_path:
                row["code_type"] = code_type
                row["metadata_path"] = metadata_path
                self.save()
                return
        self.rows.append({
            "folder_path": folder_path,
            "code_type": code_type,
            "metadata_path": metadata_path,
        })
        self.save()

    def remove_folder(self, folder_path: str) -> bool:
        folder_path = os.path.normpath(folder_path)
        kept = [row for row in self.rows if row["folder_path"] != folder_path]
        if len(kept) != len(self.rows):
            self.rows = kept
            self.save()
            return True
        return False

    def get_all(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    def get_dataframe(self):
        """Backward-compatible tabular view without requiring pandas."""
        return self.get_all()
