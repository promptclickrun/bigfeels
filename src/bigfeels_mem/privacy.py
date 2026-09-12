"""Best-effort secret redaction before local storage and provider submission."""
import re

PATTERNS = [
    (re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', re.S), '[REDACTED PRIVATE KEY]'),
    (re.compile(r'(?i)(\b(?:authorization\s*:\s*(?:bearer|basic)|bearer)\s+)\S+'), r'\1[REDACTED]'),
    (re.compile(r'''(?ix)(["']?\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token)["']?\s*[=:]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)'''), r'\1"[REDACTED]"'),
    (re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b'), '[REDACTED]'),
]


def redact(content):
    for pattern, replacement in PATTERNS:
        content = pattern.sub(replacement, content)
    return content
