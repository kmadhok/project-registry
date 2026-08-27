#!/usr/bin/env python3
"""Claude Code PreToolUse guard for autonomous builder runs.

This module intentionally uses only the Python standard library so the hook can
run before the project virtual environment has been activated.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


SHELL_OPERATORS = {"&&", "||", ";", "|", "\n"}
FORCE_OPTIONS = {"--force", "--force-with-lease", "--force-if-includes"}
PR_MUTATIONS = {"close", "edit", "reopen", "lock", "ready", "review"}
ISSUE_MUTATIONS = {
    "close", "edit", "delete", "transfer", "lock", "pin", "unpin",
    "reopen", "comment", "create",
}
REPO_MUTATIONS = {"delete", "archive", "unarchive", "edit", "rename", "fork", "create"}
RELEASE_MUTATIONS = {"create", "delete", "edit", "upload"}
SECRET_READERS = {
    "cat", "less", "more", "head", "tail", "bat", "strings", "base64",
    "xxd", "od", "get-content",
}
SECRET_WORDS = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
INDIRECT_INTERPRETERS = {"python", "python3", "node", "perl", "ruby", "php"}
INDIRECT_SHELLS = {"bash", "sh", "zsh"}
INDIRECT_WRAPPERS = {"eval", "exec", "xargs", "nohup", "timeout"}
INDIRECT_TARGETS = (
    "git push", "gh pr", "gh api", "gh repo", "gh issue", "gh release",
    "gh secret", "gh variable", "proposal-apply", "record-review",
    "apply_approved_project_update", "record_project_review",
)
HEREDOC_PATTERN = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")
COMMAND_SUB_HEREDOC_PATTERN = re.compile(
    r"\$\([ \t]*(?P<prefix>[^\r\n]*?)<<-?[ \t]*"
    r"(?P<quote>['\"]?)(?P<tag>\w+)(?P=quote)[^\r\n]*\r?\n"
    r"(?P<body>.*?)(?:^|\r?\n)[\t]*(?P=tag)[ \t]*(?:\r?\n[ \t]*)?\)",
    re.DOTALL | re.MULTILINE,
)
HEREDOC_RECEIVERS = INDIRECT_INTERPRETERS | INDIRECT_SHELLS | {"eval", "exec"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
GIT_GLOBAL_VALUE_OPTIONS = {
    "-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix",
}


class Denied(Exception):
    def __init__(self, rule_id: str, detail: str = "") -> None:
        self.rule_id = rule_id
        self.detail = detail
        super().__init__(rule_id)


def _split_shell(command: str) -> list[str]:
    """Split common compound shell syntax without splitting quoted text."""
    pieces: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        operator = None
        if command[index:index + 2] in {"&&", "||"}:
            operator = command[index:index + 2]
        elif char in {";", "|", "\n"}:
            operator = char
        if operator is not None:
            pieces.append(command[start:index].strip())
            pieces.append(operator)
            index += len(operator)
            start = index
            continue
        index += 1
    if quote or escaped:
        raise ValueError("unclosed shell quote or escape")
    pieces.append(command[start:].strip())
    return [piece for piece in pieces if piece]


def _tokens(segment: str) -> list[str]:
    tokens = shlex.split(segment, posix=True)
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"(?:\d*(?:>>?|<)|&>>?)", token):
            if index + 1 >= len(tokens):
                raise ValueError("shell redirection has no target")
            index += 2
            continue
        if re.fullmatch(r"(?:\d*(?:>>?|<)|&>>?)(?:&\d+|[^&].*)", token):
            index += 1
            continue
        result.append(token)
        index += 1
    return result


def _extract_heredocs(command: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove heredoc bodies and return their command prefixes and contents."""
    heredocs: list[tuple[str, str]] = []

    def replace_command_substitution(match: re.Match[str]) -> str:
        heredocs.append((match.group("prefix"), match.group("body")))
        return "'__heredoc__'"

    command = COMMAND_SUB_HEREDOC_PATTERN.sub(replace_command_substitution, command)
    lines = command.splitlines(keepends=True)
    command_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        matches = list(HEREDOC_PATTERN.finditer(line))
        command_lines.append(line)
        index += 1
        for match in matches:
            delimiter = match.group(2)
            strip_tabs = match.group(0).startswith("<<-")
            body_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                terminator = candidate.rstrip("\r\n")
                if strip_tabs:
                    terminator = terminator.lstrip("\t")
                if terminator == delimiter:
                    index += 1
                    break
                body_lines.append(candidate)
                index += 1
            heredocs.append((line[:match.start()], "".join(body_lines)))
    return "".join(command_lines), heredocs


def _resolve(path: str | Path, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def _command_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        name = Path(token).name.lower()
        if name in {"git", "gh", "registry", "python", "python3", "python3.11"}:
            return index
        if name == "env" and any(
            Path(later).name.lower() in {"git", "gh", "registry", "python", "python3", "python3.11"}
            for later in tokens[index + 1:]
        ):
            continue
        if name in SECRET_READERS or name in {"printenv", "env", "set", "echo", "rm"}:
            return index
    return None


def _git_parts(tokens: list[str], git_index: int, command_dir: Path) -> tuple[str | None, list[str], Path]:
    index = git_index + 1
    git_dir = command_dir
    while index < len(tokens):
        token = tokens[index]
        if token in GIT_GLOBAL_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                raise Denied("git_parse", f"git {token} has no value")
            if token == "-C":
                git_dir = _resolve(tokens[index + 1], command_dir)
            index += 2
        elif any(
            token.startswith(option + "=")
            for option in GIT_GLOBAL_VALUE_OPTIONS if option.startswith("--")
        ):
            index += 1
        elif token.startswith("-"):
            index += 1
        else:
            return token, tokens[index + 1:], git_dir
    raise Denied("git_parse", "cannot determine git subcommand")


def _current_branch(directory: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise Denied("branch_resolution", "cannot resolve current branch")
    branch = completed.stdout.strip()
    if not branch or branch == "HEAD":
        raise Denied("branch_resolution", "current branch is detached")
    return branch


def _option_value(args: list[str], names: set[str]) -> str | None:
    for index, token in enumerate(args):
        for name in names:
            if token == name:
                if index + 1 >= len(args):
                    raise Denied("guard_parse", f"{name} has no value")
                return args[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
            if len(name) == 2 and token.startswith(name) and token != name:
                return token[len(name):].removeprefix("=")
    return None


def _positional_args(args: list[str], value_options: set[str]) -> list[str]:
    result: list[str] = []
    skip = False
    for token in args:
        if skip:
            skip = False
            continue
        if token in value_options:
            skip = True
            continue
        if any(token.startswith(option + "=") for option in value_options):
            continue
        if token.startswith("-"):
            continue
        result.append(token)
    return result


def _push_ref(args: list[str], git_dir: Path) -> tuple[str, bool, bool]:
    deletion = "--delete" in args or "-d" in args
    positional = _positional_args(
        args,
        {"--repo", "--receive-pack", "--exec", "--push-option", "-o"},
    )
    # The first positional is the remote; subsequent values are refspecs.
    refspecs = positional[1:] if positional else []
    explicit_tag = bool(refspecs and refspecs[0] == "tag")
    if explicit_tag:
        refspecs = refspecs[1:]
    if deletion and refspecs:
        ref = refspecs[-1].removeprefix(":")
        return ref, True, explicit_tag or ref.startswith("refs/tags/")
    if refspecs:
        refspec = refspecs[-1]
        if refspec.startswith(":"):
            ref = refspec[1:]
            return ref, True, explicit_tag or ref.startswith("refs/tags/")
        if ":" in refspec:
            source, destination = refspec.split(":", 1)
            ref = destination or source.removeprefix("+")
            is_tag = explicit_tag or source.lstrip("+").startswith("refs/tags/") or ref.startswith("refs/tags/")
            return ref, not source, is_tag
        ref = refspec.removeprefix("+")
        return ref, deletion, explicit_tag or ref.startswith("refs/tags/") or ref.startswith("checkpoint/")
    return _current_branch(git_dir), deletion, False


def _force_push(args: list[str]) -> bool:
    for token in args:
        if token in FORCE_OPTIONS or any(token.startswith(option + "=") for option in FORCE_OPTIONS):
            return True
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return True
    positional = _positional_args(args, {"--receive-pack", "--exec", "--push-option", "-o"})
    return any(token.startswith("+") for token in positional[1:])


def _chunk_for_pr(lease: dict[str, Any], number: int) -> dict[str, Any] | None:
    for chunk in lease.get("chunks", {}).values():
        try:
            if int(chunk.get("pr_number")) == number:
                return chunk
        except (TypeError, ValueError):
            continue
    return None


def _pr_number(args: list[str]) -> int | None:
    positionals = _positional_args(args, {"--repo", "-R", "--body", "--body-file", "--subject"})
    for token in positionals:
        if token.isdigit():
            return int(token)
        match = re.search(r"/pull/(\d+)(?:/|$)", token)
        if match:
            return int(match.group(1))
    return None


def _is_forbidden_path(value: str, forbidden: list[str], command_dir: Path) -> bool:
    raw = value.strip("'\"")
    if not raw or raw.startswith("-") or raw in {"/dev/null", "-"}:
        return False
    normalized = raw.replace("\\", "/")
    normalized_lower = normalized.lower()
    basename = Path(normalized).name.lower()
    if (
        basename == ".env"
        or basename.startswith(".env.")
        or fnmatch.fnmatch(basename, "*.pem")
        or fnmatch.fnmatch(basename, "*.key")
        or fnmatch.fnmatch(basename, "id_rsa*")
        or "secret" in normalized_lower
        or "credential" in normalized_lower
        or fnmatch.fnmatch(basename, "*.p12")
        or fnmatch.fnmatch(basename, "*.keystore")
    ):
        return True
    resolved = _resolve(raw, command_dir)
    for pattern_value in forbidden:
        pattern = str(pattern_value).replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        candidates = {normalized.lstrip("./"), resolved.as_posix(), basename}
        if any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates):
            return True
        pattern_path = _resolve(pattern, command_dir)
        if resolved == pattern_path or pattern_path in resolved.parents:
            return True
    return False


def _matches_contract_forbidden(value: str | Path, forbidden: list[str], base: Path) -> bool:
    raw = str(value).strip("'\"")
    if not raw:
        return False
    normalized = raw.replace("\\", "/").lstrip("./")
    resolved = _resolve(raw, base).as_posix()
    basename = Path(normalized).name
    for pattern_value in forbidden:
        pattern = str(pattern_value).replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        patterns = {pattern}
        pending = [pattern]
        while pending:
            candidate_pattern = pending.pop()
            offset = candidate_pattern.find("**/")
            if offset >= 0:
                without_recursive = candidate_pattern[:offset] + candidate_pattern[offset + 3:]
                if without_recursive not in patterns:
                    patterns.add(without_recursive)
                    pending.append(without_recursive)
        parts = [part for part in normalized.split("/") if part]
        suffixes = {"/".join(parts[index:]) for index in range(len(parts))}
        candidates = {normalized, resolved, basename, *suffixes}
        if any(
            fnmatch.fnmatch(candidate, candidate_pattern)
            for candidate in candidates for candidate_pattern in patterns
        ):
            return True
        if not Path(pattern).is_absolute():
            relative = Path(resolved).relative_to(base).as_posix() if Path(resolved).is_relative_to(base) else ""
            if relative and fnmatch.fnmatch(relative, pattern):
                return True
    return False


def _push_remote(args: list[str]) -> str | None:
    positional = _positional_args(
        args, {"--repo", "--receive-pack", "--exec", "--push-option", "-o"},
    )
    return positional[0] if positional else None


def _remote_matches_repo(remote: str, repo: str) -> bool:
    normalized = remote.lower().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    expected = re.escape(repo.lower().strip("/"))
    return re.search(rf"(?:^|[/:]){expected}(?:$|[/?#])", normalized) is not None


def _status_paths(git_dir: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(git_dir), "status", "--porcelain", "-uall"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if completed.returncode != 0:
        raise Denied("forbidden_path_stage", "cannot inspect changed paths")
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        value = line[3:] if len(line) >= 4 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value.strip().strip('"'))
    return paths


def _broad_add(args: list[str], git_dir: Path) -> bool:
    positionals = _positional_args(args, {"--chmod", "--pathspec-from-file"})
    if any(token in {".", "-A", "--all", ":/"} for token in args):
        return True
    return bool(positionals) and all(_resolve(token, git_dir).is_dir() for token in positionals)


def _registry_cli_args(tokens: list[str], command_index: int) -> list[str] | None:
    name = Path(tokens[command_index]).name.lower()
    if name == "registry":
        return tokens[command_index + 1:]
    if name.startswith("python"):
        tail = tokens[command_index + 1:]
        if len(tail) >= 2 and tail[0] == "-m" and tail[1] == "project_registry.cli":
            return tail[2:]
    return None


def _evaluate_registry(args: list[str]) -> None:
    if not args:
        return
    if args[0] in {"proposal-apply", "proposal-reject", "record-review", "import-github"}:
        raise Denied("registry_write_tool")
    if len(args) >= 2 and args[0] == "build" and args[1] == "resume":
        raise Denied("breaker_bypass")
    if args[0] == "propose":
        values: list[str] = []
        for index, token in enumerate(args):
            if token == "--set":
                if index + 1 >= len(args):
                    raise Denied("intent_only_proposal")
                values.append(args[index + 1])
            elif token.startswith("--set="):
                values.append(token.split("=", 1)[1])
        if any(not value.split("=", 1)[0].startswith("brief.") for value in values):
            raise Denied("intent_only_proposal")


def _evaluate_git(
    tokens: list[str], git_index: int, command_dir: Path, root: Path,
    lease: dict[str, Any], segment: str,
) -> None:
    subcommand, args, git_dir = _git_parts(tokens, git_index, command_dir)
    in_registry = git_dir == root
    forbidden = [str(path) for path in lease.get("contract_forbidden_paths", [])]
    if subcommand in {"add", "commit"}:
        commit_all = subcommand == "commit" and any(
            token in {"-a", "--all"} or (token.startswith("-") and not token.startswith("--") and "a" in token[1:])
            for token in args
        )
        if subcommand == "add" and forbidden and _broad_add(args, git_dir):
            if any(_matches_contract_forbidden(path, forbidden, git_dir) for path in _status_paths(git_dir)):
                raise Denied("forbidden_path_stage")
        elif (subcommand == "add" or commit_all) and any(
            _is_forbidden_path(token, forbidden, git_dir) for token in args
        ):
            raise Denied("forbidden_path_stage")
    if subcommand == "push":
        if lease.get("status") == "finalize_pending" and not in_registry:
            raise Denied("finalize_only")
        remote = _push_remote(args)
        if remote is not None:
            if in_registry:
                if remote != "origin":
                    raise Denied("push_remote")
            elif remote != "origin" and not _remote_matches_repo(remote, str(lease.get("repo", ""))):
                raise Denied("push_remote")
        if _force_push(args):
            raise Denied("force_push")
        if any(
            token in {"--tags", "--follow-tags"}
            or token.startswith("--tags=")
            or token.startswith("--follow-tags=")
            for token in args
        ):
            raise Denied("tag_push_scope")
        branch, deletion, is_tag = _push_ref(args, git_dir)
        prefix = f"push/{lease['run_id']}-"
        if deletion:
            targets = {str(value).removeprefix("refs/heads/") for value in lease.get("reconcile_targets", [])}
            if branch.removeprefix("refs/heads/") not in targets and not branch.startswith(prefix):
                raise Denied("branch_delete")
            return
        if is_tag:
            tag = branch.removeprefix("refs/tags/")
            if in_registry or not tag.startswith(f"checkpoint/{lease['run_id']}-"):
                raise Denied("tag_push_scope")
            return
        branch = branch.removeprefix("refs/heads/")
        if in_registry:
            if branch != "main":
                raise Denied("registry_push_branch")
        elif not branch.startswith(prefix):
            raise Denied("non_push_branch")
    if subcommand == "commit" and in_registry:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise Denied("registry_commit_scope", "cannot inspect staged paths")
        staged = [line for line in completed.stdout.splitlines() if line]
        if any(
            path != "DASHBOARD.md"
            and not path.startswith("data/build/")
            and not path.startswith("data/proposals/")
            for path in staged
        ):
            raise Denied("registry_commit_scope")


def _evaluate_gh(args: list[str], command_dir: Path, lease: dict[str, Any]) -> None:
    if not args:
        return
    group = args[0]
    action = args[1] if len(args) > 1 else ""
    rest = args[2:]
    if lease.get("status") == "finalize_pending" and group == "pr" and action in {"create", "merge"}:
        raise Denied("finalize_only")
    if group == "pr" and action == "create":
        if int(lease.get("prs_open", 0)) >= 1:
            raise Denied("one_pr_per_chunk")
        repo = _option_value(rest, {"--repo", "-R"})
        if repo is not None and repo != lease.get("repo"):
            raise Denied("wrong_repo")
        head = _option_value(rest, {"--head", "-H"})
        if head is None:
            head = _current_branch(command_dir)
        if head is not None and not head.startswith(f"push/{lease['run_id']}-"):
            raise Denied("pr_head_branch")
        return
    if group == "pr" and action == "merge":
        if lease.get("dry_run") is True:
            raise Denied("shadow_mode")
        if "--squash" not in rest:
            raise Denied("merge_method")
        if "--admin" in rest:
            raise Denied("merge_admin")
        repo = _option_value(rest, {"--repo", "-R"})
        if repo is not None and repo != lease.get("repo"):
            raise Denied("wrong_repo")
        number = _pr_number(rest)
        chunk = _chunk_for_pr(lease, number) if number is not None else None
        if (
            chunk is None
            or chunk.get("verify") != "passed"
            or chunk.get("verdict") != "approve"
            or chunk.get("merged") is not False
        ):
            raise Denied("unverified_merge")
        return
    if group == "pr" and action in PR_MUTATIONS:
        number = _pr_number(rest)
        chunk = _chunk_for_pr(lease, number) if number is not None else None
        if chunk is None:
            raise Denied("foreign_pr")
        if action == "close" and not (
            lease.get("status") == "reconciling"
            or chunk.get("verdict") in {"reject", "request_changes"}
            or chunk.get("verify") == "failed"
        ):
            raise Denied("pr_close_policy")
        return
    if group == "issue" and action in ISSUE_MUTATIONS:
        raise Denied("issue_mutation")
    if group == "repo" and action in REPO_MUTATIONS:
        raise Denied("repo_mutation")
    if group == "api":
        method = _option_value(args[1:], {"-X", "--method"})
        mutating_fields = {"-f", "-F", "--input", "--raw-field"}
        if (method is not None and method.upper() != "GET") or any(
            token in mutating_fields
            or any(token.startswith(option + "=") for option in mutating_fields)
            or (token.startswith("-f") and token != "-f")
            or (token.startswith("-F") and token != "-F")
            for token in args[1:]
        ):
            raise Denied("api_mutation")
    if group == "release" and action in RELEASE_MUTATIONS:
        raise Denied("release_mutation")
    if group in {"secret", "variable"}:
        raise Denied("secret_mutation")


def _evaluate_segment(
    segment: str, command_dir: Path, root: Path, lease: dict[str, Any]
) -> Path:
    tokens = _tokens(segment)
    if not tokens:
        return command_dir
    if Path(tokens[0]).name == "cd":
        if len(tokens) < 2:
            raise Denied("guard_parse", "cd has no directory")
        return _resolve(tokens[1], command_dir)

    index = _command_index(tokens)
    if index is None:
        return command_dir
    executable = Path(tokens[index]).name.lower()
    forbidden = [str(path) for path in lease.get("contract_forbidden_paths", [])]

    if executable in SECRET_READERS:
        if any(_is_forbidden_path(token, forbidden, command_dir) for token in tokens[index + 1:]):
            raise Denied("secret_read")
    if executable == "echo" and any(
        SECRET_WORDS.search(match.group(1))
        for token in tokens[index + 1:]
        for match in re.finditer(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", token)
    ):
        raise Denied("secret_env")
    if executable == "rm":
        flags = "".join(token[1:] for token in tokens[index + 1:] if token.startswith("-") and not token.startswith("--"))
        recursive_force = ("r" in flags and "f" in flags) or {"--recursive", "--force"}.issubset(set(tokens))
        protected = {root, root / "registry", root / "data"}
        if recursive_force and any(
            _resolve(token, command_dir) in protected
            for token in tokens[index + 1:]
            if not token.startswith("-")
        ):
            raise Denied("registry_destroy")
    if executable == "git":
        _evaluate_git(tokens, index, command_dir, root, lease, segment)
    elif executable == "gh":
        _evaluate_gh(tokens[index + 1:], command_dir, lease)
    else:
        registry_args = _registry_cli_args(tokens, index)
        if registry_args is not None:
            _evaluate_registry(registry_args)
    return command_dir


def _indirect_executable(tokens: list[str]) -> tuple[str, list[str]] | None:
    index = 0
    while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
        index += 1
    if index < len(tokens) and Path(tokens[index]).name.lower() == "env":
        index += 1
        while index < len(tokens) and (
            tokens[index].startswith("-")
            or ("=" in tokens[index] and not tokens[index].startswith("="))
        ):
            index += 1
    if index >= len(tokens):
        return None
    return Path(tokens[index]).name.lower(), tokens[index + 1:]


def _has_inline_option(args: list[str], options: set[str]) -> bool:
    for arg in args:
        if arg == "--" or not arg.startswith("-"):
            return False
        if arg in options:
            return True
        if arg.startswith("--"):
            continue
        if any(arg.startswith(option) for option in options):
            return True
        if arg.startswith("-") and any(option[1:] in arg[1:] for option in options):
            return True
    return False


def _contains_indirect_target(value: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", " ", value).lower()
    return any(target in normalized for target in INDIRECT_TARGETS)


def _heredoc_receiver(prefix: str) -> str | None:
    pieces = [piece for piece in _split_shell(prefix) if piece not in SHELL_OPERATORS]
    if not pieces:
        return None
    invocation = _indirect_executable(_tokens(pieces[-1]))
    return invocation[0] if invocation is not None else None


def _evaluate_bash(command: str, cwd: Path, root: Path, lease: dict[str, Any]) -> None:
    command_text, heredocs = _extract_heredocs(command)
    for prefix, body in heredocs:
        receiver = _heredoc_receiver(prefix)
        if receiver in HEREDOC_RECEIVERS and _contains_indirect_target(body):
            raise Denied("indirect_invocation")
    if _contains_indirect_target(command_text):
        indirect = False
        for piece in _split_shell(command_text):
            if piece in SHELL_OPERATORS:
                continue
            try:
                tokens = _tokens(piece)
            except ValueError:
                # Let the normal fail-closed parsing path report malformed shell syntax.
                continue
            invocation = _indirect_executable(tokens)
            if invocation is None:
                continue
            name, tail = invocation
            if name in INDIRECT_INTERPRETERS and _has_inline_option(tail, {"-c", "-e"}):
                indirect = True
            elif name in INDIRECT_SHELLS and _has_inline_option(tail, {"-c"}):
                indirect = True
            elif name in INDIRECT_WRAPPERS:
                indirect = True
        if indirect:
            raise Denied("indirect_invocation")
    if re.search(
        r"(?:^|[;&|\n])\s*(?:printenv|env|set)\b[^|]*\|\s*grep\b[^\n;&]*(?:KEY|TOKEN|SECRET|PASSWORD)",
        command_text,
        re.IGNORECASE,
    ):
        raise Denied("secret_env")
    current_dir = cwd
    for piece in _split_shell(command_text):
        if piece in SHELL_OPERATORS:
            continue
        current_dir = _evaluate_segment(piece, current_dir, root, lease)


def _evaluate_mcp(tool_name: str, tool_input: dict[str, Any]) -> None:
    action = tool_name.removeprefix("mcp__project-registry__")
    if action in {"apply_approved_project_update", "record_project_review"}:
        raise Denied("registry_write_tool")
    if action == "propose_project_update":
        changes = tool_input.get("changes", {})
        if not isinstance(changes, dict) or any(not str(key).startswith("brief.") for key in changes):
            raise Denied("intent_only_proposal")


def _evaluate_write(
    tool_name: str, tool_input: dict[str, Any], root: Path, cwd: Path,
    lease: dict[str, Any],
) -> None:
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    value = tool_input.get(key)
    if not isinstance(value, str) or not value:
        raise Denied("direct_write_scope", f"{key} is required")
    target = _resolve(value, cwd)
    forbidden = [str(path) for path in lease.get("contract_forbidden_paths", [])]
    if target == root or root in target.parents or _matches_contract_forbidden(target, forbidden, cwd):
        raise Denied("direct_write_scope")


def _append_denial(root: Path, lease: dict[str, Any], rule_id: str, command: str) -> None:
    path = root / "data" / "build" / "runs.jsonl"
    try:
        event = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": lease.get("run_id"),
            "host": lease.get("host"),
            "type": "guard_denied",
            "project_id": lease.get("project_id"),
            "reason": rule_id,
            "detail": {"command": command[:300]},
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def main() -> int:
    lease: dict[str, Any] | None = None
    root: Path | None = None
    command_detail = ""
    try:
        environment_root = os.environ.get("CLAUDE_PROJECT_DIR")
        if environment_root:
            root = Path(environment_root).resolve(strict=False)
            if not (root / "data" / "build" / "lease.json").exists():
                return 0
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        root_value = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
        if not root_value:
            raise ValueError("project root is unavailable")
        root = Path(str(root_value)).resolve(strict=False)
        lease_path = root / "data" / "build" / "lease.json"
        if not lease_path.exists():
            return 0
        with lease_path.open(encoding="utf-8") as handle:
            lease = json.load(handle)
        if not isinstance(lease, dict) or not isinstance(lease.get("run_id"), str):
            raise ValueError("lease must be an object with a run_id")

        tool_name = payload.get("tool_name")
        tool_input = payload.get("tool_input", {})
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        if tool_name == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str):
                raise ValueError("Bash command must be a string")
            command_detail = command
            cwd = _resolve(str(payload.get("cwd") or root), root)
            _evaluate_bash(command, cwd, root, lease)
        elif isinstance(tool_name, str) and tool_name.startswith("mcp__project-registry__"):
            command_detail = json.dumps(tool_input, sort_keys=True)
            _evaluate_mcp(tool_name, tool_input)
        elif tool_name in WRITE_TOOLS:
            command_detail = json.dumps(tool_input, sort_keys=True)
            cwd = _resolve(str(payload.get("cwd") or root), root)
            _evaluate_write(tool_name, tool_input, root, cwd, lease)
        return 0
    except Denied as error:
        if root is not None and lease is not None:
            _append_denial(root, lease, error.rule_id, command_detail)
        suffix = f": {error.detail}" if error.detail else ""
        print(f"build-guard: {error.rule_id}{suffix}", file=sys.stderr)
        return 2
    except Exception as error:  # Fail closed for malformed input, lease, or runtime errors.
        if root is not None and lease is not None:
            _append_denial(root, lease, "guard_error", command_detail)
        print(f"build-guard: guard_error: {str(error).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
