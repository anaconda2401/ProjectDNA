"""
ai_architect/context_builder.py
Builds compact, high-signal context from ProjectDNA analysis results.
Redacts secrets, ignores sensitive files, and enforces token budgeting before LLM transmission.
"""

import os
import re
import json
from typing import Dict, Any, List, Optional, Set

# Regex patterns to detect and redact credentials and tokens
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth[_-]?key)\s*[:=]\s*['\"]([^'\"]{8,})['\"]"),
    re.compile(r"gsk_[a-zA-Z0-9_-]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
]

# Sensitive file basenames that must never be read or included in LLM context
SENSITIVE_FILES: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "client_secret.json",
    "secrets.yaml",
    "secrets.json",
}


def redact_secrets(text: str) -> str:
    """Scan string for API keys, passwords, and private keys, replacing them with redaction placeholders."""
    redacted = text
    for pat in SECRET_PATTERNS:
        if pat.pattern.startswith("(?i)(api[_-]?key"):
            redacted = pat.sub(r"\1='[REDACTED_SECRET]'", redacted)
        else:
            redacted = pat.sub("[REDACTED_CREDENTIAL]", redacted)
    return redacted


def is_sensitive_path(path: str) -> bool:
    """Check if file path or basename is in sensitive files list."""
    base = os.path.basename(path).lower()
    if base in SENSITIVE_FILES or base.startswith(".env"):
        return True
    return False


def read_file_snippet(file_path: str, start_line: int = 1, max_lines: int = 35) -> str:
    """Read a limited snippet of code from disk, redacting secrets."""
    if not file_path or not os.path.exists(file_path) or is_sensitive_path(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        snippet_lines = lines[max(0, start_line - 1):start_line - 1 + max_lines]
        raw_text = "".join(snippet_lines)
        return redact_secrets(raw_text)
    except Exception:
        return ""


def build_review_context(result: Dict[str, Any], project_root: Optional[str] = None) -> str:
    """
    Extract a compact, focused context object from result dict.
    Returns serialized JSON string ready to include in LLM prompt.
    """
    summary = result.get("summary", {})
    modules = result.get("modules", [])
    issues = result.get("issues", [])
    all_functions = result.get("all_functions", [])
    module_metrics = result.get("module_metrics", {})
    layer_map = result.get("layer_map", {})
    external_libraries = result.get("external_libraries", [])

    # 1. Summary Metrics
    compact_summary = {
        "project_name": result.get("project_name", "Unknown"),
        "total_files": summary.get("total_files", 0),
        "total_lines": summary.get("total_lines", 0),
        "total_classes": summary.get("total_classes", 0),
        "total_functions": summary.get("total_functions", 0),
        "avg_complexity": round(summary.get("avg_complexity", 0.0), 2),
    }

    # 2. Layer Distribution
    layer_counts: Dict[str, int] = {}
    for l in layer_map.values():
        layer_counts[l] = layer_counts.get(l, 0) + 1

    # 3. Measured Static Issues (Top 12)
    compact_issues = []
    for iss in issues[:12]:
        compact_issues.append({
            "rule": iss.get("rule"),
            "severity": iss.get("severity"),
            "module": iss.get("module"),
            "message": redact_secrets(iss.get("message", "")),
        })

    # 4. Top Coupling Modules (High Fan-out or Fan-in)
    metrics_list = []
    if isinstance(module_metrics, dict):
        for mod, m in module_metrics.items():
            metrics_list.append({
                "module": mod,
                "fan_in": m.get("afferent_coupling", 0),
                "fan_out": m.get("efferent_coupling", 0),
                "instability": round(m.get("instability", 0.0), 2),
            })
    elif isinstance(module_metrics, list):
        for m in module_metrics:
            metrics_list.append({
                "module": m.get("module"),
                "fan_in": m.get("afferent_coupling", 0),
                "fan_out": m.get("efferent_coupling", 0),
                "instability": round(m.get("instability", 0.0), 2),
            })

    # Sort by total coupling
    metrics_list.sort(key=lambda x: x["fan_in"] + x["fan_out"], reverse=True)
    top_coupled = metrics_list[:6]

    # 5. Top Complex Functions
    top_functions = []
    for fn in all_functions[:6]:
        top_functions.append({
            "name": fn.get("qualified", fn.get("name")),
            "module": fn.get("module"),
            "file": fn.get("path"),
            "lineno": fn.get("lineno"),
            "complexity": fn.get("complexity"),
            "calls": fn.get("calls", [])[:5],
        })

    # 6. Sample Snippets for Problematic Functions (if project_root provided and file accessible)
    snippets: List[Dict[str, Any]] = []
    if project_root and os.path.isdir(project_root):
        for fn in top_functions[:3]:
            rel_path = fn.get("file", "")
            if rel_path and not is_sensitive_path(rel_path):
                abs_path = os.path.join(project_root, rel_path)
                snip = read_file_snippet(abs_path, start_line=fn.get("lineno", 1), max_lines=25)
                if snip:
                    snippets.append({
                        "file": rel_path,
                        "symbol": fn.get("name"),
                        "lines": f"{fn.get('lineno')}-{fn.get('lineno', 1) + 25}",
                        "code": snip,
                    })

    context_obj = {
        "summary": compact_summary,
        "layers": layer_counts,
        "detected_static_issues": compact_issues,
        "top_coupled_modules": top_coupled,
        "most_complex_functions": top_functions,
        "external_libraries": [lib.get("name") for lib in external_libraries[:8]],
        "code_snippets": snippets,
    }

    return json.dumps(context_obj, indent=2)
