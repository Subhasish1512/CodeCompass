#!/usr/bin/env python
"""
Verification script for incremental indexing.

Creates a temp git repo, applies scripted commits, runs update after each,
and compares DB state against a fresh full index.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codecompass.config import get_settings
from codecompass.db import init_db, close_db
from codecompass.db.repository import (
    create_repository, get_repository_by_name, list_files,
    list_symbols, get_symbol, get_file_by_path,
)
from codecompass.indexing.pipeline import index_repository
from codecompass.indexing.incremental import update_repository


def run_cmd(cmd: list, cwd: Path, check=True):
    """Run a command and return result."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result


def setup_temp_repo() -> Path:
    """Create a temporary git repository with initial Python files."""
    temp_dir = Path(tempfile.mkdtemp(prefix="codecompass_test_"))
    repo_dir = temp_dir / "test_repo"
    repo_dir.mkdir()

    # Initialize git
    run_cmd(["git", "init"], repo_dir)
    run_cmd(["git", "config", "user.name", "Test User"], repo_dir)
    run_cmd(["git", "config", "user.email", "test@example.com"], repo_dir)

    # Create initial Python files
    (repo_dir / "main.py").write_text("""
def greet(name: str) -> str:
    '''Greet a person by name.'''
    return f"Hello, {name}!"

def calculate(a: int, b: int) -> int:
    '''Add two numbers.'''
    return a + b

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b

    def multiply(self, a: int, b: int) -> int:
        return a * b

if __name__ == "__main__":
    print(greet("World"))
    print(calculate(2, 3))
""")

    (repo_dir / "utils.py").write_text("""
def format_name(name: str) -> str:
    '''Format a name.'''
    return name.strip().title()

def validate_email(email: str) -> bool:
    '''Simple email validation.'''
    return "@" in email and "." in email

class StringHelper:
    @staticmethod
    def reverse(s: str) -> str:
        return s[::-1]

    @staticmethod
    def capitalize_words(s: str) -> str:
        return " ".join(w.capitalize() for w in s.split())
""")

    (repo_dir / "models.py").write_text("""
from dataclasses import dataclass
from typing import Optional

@dataclass
class User:
    id: int
    name: str
    email: Optional[str] = None

    def greet(self) -> str:
        return f"Hi, I'm {self.name}"

@dataclass
class Product:
    id: int
    name: str
    price: float

    def discounted_price(self, discount: float) -> float:
        return self.price * (1 - discount)
""")

    run_cmd(["git", "add", "."], repo_dir)
    run_cmd(["git", "commit", "-m", "Initial commit"], repo_dir)

    return repo_dir


def apply_commit(repo_dir: Path, commit_num: int) -> None:
    """Apply a scripted commit to the repository."""
    if commit_num == 1:
        # Add a new file
        (repo_dir / "new_module.py").write_text("""
def new_function(x: int) -> int:
    '''A brand new function.'''
    return x * 2

class NewClass:
    def method(self) -> str:
        return "new"
""")
        run_cmd(["git", "add", "new_module.py"], repo_dir)
        run_cmd(["git", "commit", "-m", "Add new_module.py"], repo_dir)

    elif commit_num == 2:
        # Modify a function in main.py
        main_content = (repo_dir / "main.py").read_text()
        main_content = main_content.replace(
            "def calculate(a: int, b: int) -> int:",
            "def calculate(a: int, b: int, c: int = 0) -> int:"
        )
        main_content = main_content.replace(
            "return a + b",
            "return a + b + c"
        )
        (repo_dir / "main.py").write_text(main_content)
        run_cmd(["git", "add", "main.py"], repo_dir)
        run_cmd(["git", "commit", "-m", "Modify calculate function"], repo_dir)

    elif commit_num == 3:
        # Rename utils.py to helpers.py
        run_cmd(["git", "mv", "utils.py", "helpers.py"], repo_dir)
        run_cmd(["git", "commit", "-m", "Rename utils.py to helpers.py"], repo_dir)

    elif commit_num == 4:
        # Delete models.py
        run_cmd(["git", "rm", "models.py"], repo_dir)
        run_cmd(["git", "commit", "-m", "Delete models.py"], repo_dir)

    elif commit_num == 5:
        # Change a callee that other files call
        main_content = (repo_dir / "main.py").read_text()
        # Change greet to use format_name from helpers (was utils)
        if "from helpers import format_name" not in main_content:
            main_content = "from helpers import format_name\n\n" + main_content
        main_content = main_content.replace(
            'return f"Hello, {name}!"',
            'return f"Hello, {format_name(name)}!"'
        )
        (repo_dir / "main.py").write_text(main_content)
        run_cmd(["git", "add", "main.py"], repo_dir)
        run_cmd(["git", "commit", "-m", "Use format_name in greet"], repo_dir)


def get_db_state(repo_name: str) -> dict:
    """Get normalized database state for comparison."""
    init_db()
    try:
        repo = get_repository_by_name(repo_name)
        if not repo:
            return {}

        repository_id = repo["id"]
        files = list_files(repository_id)

        state = {
            "files": {},
            "symbols": {},
            "edges": set(),
            "chunks": {},
        }

        for file_row in files:
            file_path = file_row["path"]
            state["files"][file_path] = file_row["content_hash"].hex()

            symbols = list_symbols(file_row["id"])
            for sym in symbols:
                key = (file_path, sym["qualified_name"], sym["kind"])
                state["symbols"][key] = {
                    "name": sym["name"],
                    "qualified_name": sym["qualified_name"],
                    "kind": sym["kind"],
                    "start_line": sym["start_line"],
                    "end_line": sym["end_line"],
                    "signature": sym["signature"],
                    "docstring": sym["docstring"],
                }

                # Get edges from this symbol
                from codecompass.db import get_db
                db = get_db()
                edges = db.execute(
                    "SELECT * FROM edges WHERE src_symbol_id = %s",
                    (sym["id"],)
                )
                for edge in edges:
                    if edge["dst_symbol_id"]:
                        dst = get_symbol(edge["dst_symbol_id"])
                        if dst:
                            state["edges"].add((
                                sym["qualified_name"],
                                edge["edge_type"],
                                dst["qualified_name"]
                            ))

        return state
    finally:
        close_db()


def normalize_state(state: dict) -> dict:
    """Normalize state for comparison (ignore auto-generated IDs)."""
    # Already normalized in get_db_state
    return state


def compare_states(state1: dict, state2: dict, label1: str, label2: str) -> list:
    """Compare two states and return list of differences."""
    diffs = []

    # Compare files
    all_files = set(state1.get("files", {}).keys()) | set(state2.get("files", {}).keys())
    for f in sorted(all_files):
        h1 = state1.get("files", {}).get(f)
        h2 = state2.get("files", {}).get(f)
        if h1 != h2:
            diffs.append(f"File {f}: {label1} hash={h1}, {label2} hash={h2}")

    # Compare symbols
    all_symbols = set(state1.get("symbols", {}).keys()) | set(state2.get("symbols", {}).keys())
    for s in sorted(all_symbols):
        sym1 = state1.get("symbols", {}).get(s)
        sym2 = state2.get("symbols", {}).get(s)
        if sym1 != sym2:
            diffs.append(f"Symbol {s}: {label1}={sym1}, {label2}={sym2}")

    # Compare edges
    edges1 = state1.get("edges", set())
    edges2 = state2.get("edges", set())
    for e in sorted(edges1 - edges2):
        diffs.append(f"Edge {e}: in {label1} but not {label2}")
    for e in sorted(edges2 - edges1):
        diffs.append(f"Edge {e}: in {label2} but not {label1}")

    return diffs


def full_reindex(repo_dir: Path, repo_name: str) -> dict:
    """Do a full re-index and return DB state."""
    # Delete existing repo if exists
    init_db()
    try:
        from codecompass.db.repository import get_repository_by_name, delete_file
        from codecompass.db import get_db
        db = get_db()
        existing = get_repository_by_name(repo_name)
        if existing:
            # Delete all files
            from codecompass.db.repository import list_files
            for f in list_files(existing["id"]):
                delete_file(f["id"])
            # Delete repo
            db.execute_void("DELETE FROM repositories WHERE id = %s", (existing["id"],))
    finally:
        close_db()

    # Fresh index
    stats = index_repository(
        name=repo_name,
        root_path=str(repo_dir),
        url="",
        incremental=False
    )
    print(f"  Full index: {stats.files_processed} files, {stats.symbols_created} symbols, {stats.edges_created} edges")

    return get_db_state(repo_name)


def run_update(repo_name: str) -> dict:
    """Run incremental update and return DB state."""
    stats = update_repository(name=repo_name, show_progress=False)
    print(f"  Update: +{stats.files_added} ~{stats.files_modified} -{stats.files_deleted} R{stats.files_renamed}, {stats.symbols_created} symbols, {stats.edges_created} edges, {stats.edges_reresolved} re-resolved")
    return get_db_state(repo_name)


def main():
    print("=" * 60)
    print("CodeCompass Incremental Indexing Verification")
    print("=" * 60)

    # Setup
    repo_dir = setup_temp_repo()
    repo_name = "test_repo_inc"

    try:
        print(f"\nCreated temp repo at: {repo_dir}")

        # Initial index
        print("\n[Step 0] Initial full index...")
        init_db()
        close_db()
        baseline_state = full_reindex(repo_dir, repo_name)

        # Apply commits and test
        for commit_num in range(1, 6):
            print(f"\n[Step {commit_num}] Applying commit {commit_num}...")
            apply_commit(repo_dir, commit_num)

            print("  Running incremental update...")
            update_state = run_update(repo_name)

            print("  Running fresh full index for comparison...")
            fresh_state = full_reindex(repo_dir, repo_name)

            print("  Comparing states...")
            diffs = compare_states(update_state, fresh_state, "Update", "Fresh Index")

            if diffs:
                print(f"\n  [FAIL] Found {len(diffs)} differences:")
                for d in diffs:
                    print(f"    - {d}")
                return 1
            else:
                print("  [PASS] States match!")

        # Test idempotency: run update twice
        print("\n[Idempotency Test] Running update twice with no changes...")
        state1 = run_update(repo_name)
        state2 = run_update(repo_name)

        diffs = compare_states(state1, state2, "First Update", "Second Update")
        if diffs:
            print(f"\n  [FAIL] Idempotency test failed - {len(diffs)} differences:")
            for d in diffs:
                print(f"    - {d}")
            return 1
        else:
            print("  [PASS] Idempotency confirmed - no changes on second run!")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        return 0

    finally:
        # Cleanup
        print(f"\nCleaning up {repo_dir}...")
        shutil.rmtree(repo_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())