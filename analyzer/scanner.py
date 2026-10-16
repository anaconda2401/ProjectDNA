"""
scanner.py
Walks a project directory, discovers Python files, and returns their relative paths.
Excludes common non-project directories.
"""

import os
from typing import List, Tuple

IGNORED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", "build", "dist", ".tox", ".eggs",
    "*.egg-info", ".mypy_cache", ".pytest_cache",
}

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024   # 1 MB per file
MAX_FILES = 500                          # hard limit for projects


def scan_project(root: str) -> Tuple[List[str], List[str]]:
    """
    Walk *root* and return (python_files, warnings).

    python_files: list of absolute paths to .py files
    warnings: list of human-readable warning strings
    """
    python_files: List[str] = []
    warnings: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored dirs in-place so os.walk doesn't descend into them
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.endswith(".egg-info")
        ]

        for fname in filenames:
            if not fname.endswith(".py"):
                continue

            full = os.path.join(dirpath, fname)

            try:
                size = os.path.getsize(full)
            except OSError:
                warnings.append(f"Cannot stat file: {full}")
                continue

            if size > MAX_FILE_SIZE_BYTES:
                warnings.append(
                    f"Skipped (too large, {size // 1024} KB): "
                    f"{os.path.relpath(full, root)}"
                )
                continue

            python_files.append(full)

            if len(python_files) >= MAX_FILES:
                warnings.append(
                    f"Reached file limit ({MAX_FILES}). "
                    "Only the first batch was analysed."
                )
                return python_files, warnings

    return python_files, warnings
