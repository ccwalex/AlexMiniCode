MODULE_METADATA = {
    "name": "update_registry",
    "type": "function",
    "description": "Update metadata registry by generating missing metadata files and rebuilding global metadata JSON.",
    "functions": [
        {
            "name": "update_registry",
            "inputs": {},
            "outputs": "tuple (bool, str)"
        }
    ]
}

from refresh_registry import run_registry_metadata_pass


def update_registry():
    """
    Update metadata registry by generating missing metadata files and rebuilding
    global metadata JSON.

    Existing sidecars are left unchanged. Only files without metadata are generated.
    """
    return run_registry_metadata_pass(overwrite=False, summary_verb="updated")


if __name__ == "__main__":
    ok, msg = update_registry()
    print(ok)
    print(msg)
