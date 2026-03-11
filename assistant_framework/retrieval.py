from __future__ import annotations

import re
from dataclasses import dataclass

from .workspace import Workspace

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STRUCTURED_MEMORY_DIRS = (
    "assistant/facts",
    "assistant/people",
    "assistant/tasks",
    "assistant/routines",
)


@dataclass
class Evidence:
    path: str
    snippet: str
    score: int


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _tail_lines(text: str, limit: int) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text.strip()
    return "\n".join(lines[-limit:]).strip()


def _iter_structured_memory_files(workspace: Workspace) -> list[str]:
    root = workspace.resolve(".")
    files: list[str] = []
    for directory in _STRUCTURED_MEMORY_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for file_path in sorted(path for path in base.rglob("*") if path.is_file()):
            files.append(str(file_path.relative_to(root)))
    return files


def _iter_candidate_documents(workspace: Workspace) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for pattern in (
        "assistant/facts/*.json",
        "assistant/people/*.json",
        "assistant/tasks/*.json",
        "assistant/routines/*.json",
        "collected/normalized/*.jsonl",
    ):
        root = workspace.resolve(".")
        for file_path in sorted(root.glob(pattern)):
            try:
                text = file_path.read_text(encoding="utf-8")
            except OSError:
                continue
            relative = str(file_path.relative_to(root))
            if file_path.suffix == ".jsonl":
                for index, line in enumerate(text.splitlines(), start=1):
                    if line.strip():
                        candidates.append((f"{relative}#L{index}", line))
                continue
            candidates.append((relative, text))
    return candidates


def search_workspace(workspace: Workspace, query: str, *, limit: int = 3) -> list[Evidence]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    matches: list[Evidence] = []
    for path, text in _iter_candidate_documents(workspace):
        haystack = text.lower()
        score = sum(1 for token in query_tokens if token in haystack)
        if not score:
            continue
        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 220:
            snippet = snippet[:217] + "..."
        matches.append(Evidence(path=path, snippet=snippet, score=score))
    matches.sort(key=lambda item: (-item.score, item.path))
    return matches[:limit]


def format_evidence_context(evidence: list[Evidence]) -> str:
    if not evidence:
        return ""
    lines = ["[Workspace evidence]"]
    for item in evidence:
        lines.append(f"- source: {item.path}")
        lines.append(f"  snippet: {item.snippet}")
    return "\n".join(lines)


def build_structured_memory_context(workspace: Workspace, *, line_limit: int = 50) -> str:
    files = _iter_structured_memory_files(workspace)
    lines = [
        "[Structured memory file previews]",
        "These previews cover every file currently under assistant/facts, assistant/people, assistant/tasks, and assistant/routines.",
        f"Each preview is a file preview of the last {line_limit} lines of that file.",
        "If you need more context from any file, use the read skill (the file skill with action `read`) on the relevant path.",
    ]
    if not files:
        lines.append("No structured memory files found.")
        return "\n".join(lines)

    for relative_path in files:
        content = workspace.read_text(relative_path, default="")
        preview = _tail_lines(content, line_limit) if content else ""
        lines.append(f"File: {relative_path}")
        lines.append(preview or "(empty file)")
    return "\n".join(lines)


def append_sources(reply: str, evidence: list[Evidence]) -> str:
    if not evidence or "Sources:" in reply:
        return reply
    referenced_sources: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        source = item.path.split("#", 1)[0]
        if source in seen:
            continue
        if source not in reply and item.path not in reply:
            continue
        seen.add(source)
        referenced_sources.append(source)
    if not referenced_sources:
        return reply
    lines = [reply.rstrip(), "", "Sources:"]
    for source in referenced_sources:
        lines.append(f"- {source}")
    return "\n".join(lines).strip()
