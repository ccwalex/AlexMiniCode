import hashlib


MODULE_METADATA = {
    "name": "render_read_cache",
    "type": "function",
    "description": "Render cached file reads into attached_file prompt blocks with SHA256 hashes and optional truncation.",
    "functions": [
        {
            "name": "render_read_cache",
            "inputs": {
                "read_cache": "dict mapping file paths to cached file content",
                "max_chars_per_file": "int maximum displayed characters per file before truncation, default 30000"
            },
            "outputs": "str containing rendered attached_file blocks for prompt injection"
        }
    ]
}


def render_read_cache(read_cache, max_chars_per_file=30000):
    parts = []

    for path, content in read_cache.items():
        content_str = str(content)
        sha = hashlib.sha256(content_str.encode("utf-8")).hexdigest()

        if len(content_str) > max_chars_per_file:
            displayed = content_str[:max_chars_per_file]
            truncated_note = f"\n\n[TRUNCATED: original length {len(content_str)} chars]"
        else:
            displayed = content_str
            truncated_note = ""

        parts.append(
            f'\n<attached_file path="{path}" sha256="{sha}">\n'
            f'{displayed}{truncated_note}\n'
            f'</attached_file>\n'
        )

    return "\n".join(parts)