"""
Repository walker with .gitignore support.

Discovers files for indexing, respecting .gitignore patterns and
skipping common vendored/build directories.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from codecompass.config import get_settings
from codecompass.parsing import is_supported


@dataclass
class WalkStats:
    """Statistics from walking a repository."""
    total_files: int = 0
    skipped_ignored: int = 0
    skipped_excluded_dir: int = 0
    skipped_excluded_ext: int = 0
    skipped_unsupported: int = 0
    skipped_binary: int = 0
    skipped_too_large: int = 0
    indexable_files: int = 0


DEFAULT_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".venv", "venv", "env", ".env",
    "dist", "build", "target",
    ".mypy_cache", ".ruff_cache", ".coverage",
    "htmlcov", ".tox", ".nox",
    "vendor", "vendored",
    ".idea", ".vscode", ".vs",
    "*.egg-info", "__pypackages__",
}

DEFAULT_EXCLUDED_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".dat", ".db", ".sqlite", ".sqlite3",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".tgz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    ".class", ".jar", ".war", ".ear",
    ".o", ".a", ".lib", ".obj",
    ".lock", ".log", ".tmp", ".temp", ".cache",
    ".min.js", ".min.css", ".map",
}


class GitIgnoreMatcher:
    """Matches paths against .gitignore patterns."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.patterns: List[tuple[str, bool, bool]] = []  # (pattern, is_negation, is_dir_only)
        self._load_gitignore()

    def _load_gitignore(self) -> None:
        """Load all .gitignore files in the repository."""
        # Load global patterns first (from repo root)
        gitignore_path = self.repo_root / ".gitignore"
        if gitignore_path.exists():
            self._parse_gitignore(gitignore_path, self.repo_root)

        # Also check for .git/info/exclude
        git_info_exclude = self.repo_root / ".git" / "info" / "exclude"
        if git_info_exclude.exists():
            self._parse_gitignore(git_info_exclude, self.repo_root)

    def _parse_gitignore(self, path: Path, base_dir: Path) -> None:
        """Parse a .gitignore file and add patterns."""
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for _line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    # Handle negation
                    is_negation = line.startswith("!")
                    if is_negation:
                        line = line[1:]

                    # Handle directory-only patterns (ending with /)
                    is_dir_only = line.endswith("/")
                    if is_dir_only:
                        line = line[:-1]

                    # Convert gitignore pattern to fnmatch pattern
                    pattern = self._gitignore_to_fnmatch(line, base_dir)

                    self.patterns.append((pattern, is_negation, is_dir_only))
        except Exception:
            # If we can't read .gitignore, just continue without it
            pass

    def _gitignore_to_fnmatch(self, pattern: str, base_dir: Path) -> str:
        """Convert gitignore pattern to fnmatch pattern."""
        # Handle leading slash - anchored to base_dir
        if pattern.startswith("/"):
            pattern = pattern[1:]
            # Make it relative to base_dir
            rel_base = base_dir.relative_to(self.repo_root)
            if str(rel_base) != ".":
                pattern = str(rel_base / pattern)

        # Handle ** pattern
        pattern = pattern.replace("**/", "**/").replace("/**", "/**")

        return pattern

    def match(self, path: Path, is_dir: bool = False) -> bool:
        """
        Check if a path matches any ignore pattern.

        Returns True if the path should be ignored.
        """
        # Get path relative to repo root
        try:
            rel_path = path.relative_to(self.repo_root)
        except ValueError:
            return False

        rel_str = rel_path.as_posix()
        matched = False

        for pattern, is_negation, is_dir_only in self.patterns:
            if is_dir_only and not is_dir:
                continue

            # Check both full path and basename
            if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(path.name, pattern):
                matched = not is_negation

        return matched


def discover_files(
    repo_root: Path,
    excluded_dirs: Optional[List[str]] = None,
    excluded_extensions: Optional[List[str]] = None,
    max_file_size_mb: int = 1,
    respect_gitignore: bool = True,
) -> Tuple[List[Path], WalkStats]:
    """
    Discover all indexable files in a repository.

    Args:
        repo_root: Root directory of the repository
        excluded_dirs: Additional directories to exclude
        excluded_extensions: Additional extensions to exclude
        max_file_size_mb: Maximum file size in MB
        respect_gitignore: Whether to respect .gitignore patterns

    Returns:
        Tuple of (list of indexable file paths, walk statistics)
    """
    settings = get_settings()

    # Combine defaults with config and user-provided
    all_excluded_dirs = DEFAULT_EXCLUDED_DIRS.copy()
    if settings.excluded_dirs:
        all_excluded_dirs.update(settings.excluded_dirs)
    if excluded_dirs:
        all_excluded_dirs.update(excluded_dirs)

    all_excluded_extensions = DEFAULT_EXCLUDED_EXTENSIONS.copy()
    if settings.excluded_extensions:
        all_excluded_extensions.update(settings.excluded_extensions)
    if excluded_extensions:
        all_excluded_extensions.update(excluded_extensions)

    stats = WalkStats()
    indexable_files: List[Path] = []

    # Initialize gitignore matcher
    gitignore = GitIgnoreMatcher(repo_root) if respect_gitignore else None

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue

        stats.total_files += 1

        # Check .gitignore
        if gitignore and gitignore.match(path, is_dir=False):
            stats.skipped_ignored += 1
            continue

        # Check excluded directories
        if any(part in all_excluded_dirs for part in path.parts):
            stats.skipped_excluded_dir += 1
            continue

        # Check excluded extensions
        if path.suffix.lower() in all_excluded_extensions:
            stats.skipped_excluded_ext += 1
            continue

        # Check if language is supported
        if not is_supported(str(path)):
            stats.skipped_unsupported += 1
            continue

        # Check file size and binary
        try:
            size = path.stat().st_size
            if size > max_file_size_mb * 1024 * 1024:
                stats.skipped_too_large += 1
                continue

            # Quick binary check
            with open(path, "rb") as f:
                chunk = f.read(8192)
                if b"\x00" in chunk:
                    stats.skipped_binary += 1
                    continue
        except Exception:
            stats.skipped_binary += 1
            continue

        indexable_files.append(path)
        stats.indexable_files += 1

    return indexable_files, stats


def iter_files(
    repo_root: Path,
    excluded_dirs: Optional[List[str]] = None,
    excluded_extensions: Optional[List[str]] = None,
    max_file_size_mb: int = 1,
    respect_gitignore: bool = True,
) -> Iterator[Path]:
    """
    Iterate over indexable files in a repository (memory efficient).

    Yields:
        Path objects for each indexable file
    """
    files, _ = discover_files(
        repo_root, excluded_dirs, excluded_extensions,
        max_file_size_mb, respect_gitignore
    )
    yield from files