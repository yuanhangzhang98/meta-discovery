#!/usr/bin/env python3
"""Create structured GitHub issues from orchestrator session feedback.

Wraps the `gh` CLI to file skill improvement issues on the meta-discovery repo.
Supports single-issue mode, batch mode (JSON file), and a pre-flight check.

Usage:
    # Check gh auth and list existing skill-feedback issues
    python create_feedback_issues.py --check --repo yuanhangzhang98/meta-discovery

    # File a single issue
    python create_feedback_issues.py \\
        --graph mcgs_graph.json \\
        --repo yuanhangzhang98/meta-discovery \\
        --category bug \\
        --title "run_iteration.py crashes on Windows with spaces in path" \\
        --body "During post-designer pipeline, ..." \\
        --suggested-fix "Use pathlib instead of string concatenation"

    # Batch mode: file multiple issues from a JSON file
    python create_feedback_issues.py \\
        --graph mcgs_graph.json \\
        --repo yuanhangzhang98/meta-discovery \\
        --batch feedback_items.json

    # Dry run (print what would be filed without creating issues)
    python create_feedback_issues.py \\
        --graph mcgs_graph.json \\
        --repo yuanhangzhang98/meta-discovery \\
        --batch feedback_items.json \\
        --dry-run

Batch JSON format:
    [
      {
        "category": "bug",
        "title": "Short descriptive title",
        "body": "Detailed description of the issue",
        "suggested_fix": "Optional suggested fix"
      }
    ]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, load_graph


# ── Constants ────────────────────────────────────────────────────────────────

VALID_CATEGORIES = ("bug", "enhancement", "documentation", "edge-case")

LABELS = {
    "skill-feedback": {"color": "7057ff", "description": "Auto-filed by meta-discovery Phase 4"},
    "bug":            {"color": "d73a4a", "description": "Something isn't working correctly"},
    "enhancement":    {"color": "a2eeef", "description": "New feature or improvement request"},
    "documentation":  {"color": "0075ca", "description": "Instructions or docs need improvement"},
    "edge-case":      {"color": "e4e669", "description": "Unusual situation the skill didn't handle well"},
}

ISSUE_DELAY_SECONDS = 1  # delay between issue creations to avoid rate limits


# ── gh CLI helpers ───────────────────────────────────────────────────────────

def _run_gh(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a gh CLI command and return the result."""
    return subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        check=check,
    )


def check_gh_auth() -> bool:
    """Return True if gh is installed and authenticated."""
    try:
        result = _run_gh(["auth", "status"], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def ensure_labels(repo: str) -> None:
    """Create skill-feedback labels if they don't already exist."""
    for name, meta in LABELS.items():
        _run_gh([
            "label", "create", name,
            "--repo", repo,
            "--color", meta["color"],
            "--description", meta["description"],
            "--force",  # update if exists
        ], check=False)


def check_duplicate(repo: str, title: str) -> Optional[str]:
    """Check for an existing open issue with a matching title.

    Returns the issue URL if a duplicate is found, None otherwise.
    """
    result = _run_gh([
        "issue", "list",
        "--repo", repo,
        "--label", "skill-feedback",
        "--search", title,
        "--state", "open",
        "--json", "title,url",
        "--limit", "10",
    ], check=False)
    if result.returncode != 0:
        return None
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for issue in issues:
        if issue.get("title", "").strip().lower() == title.strip().lower():
            return issue.get("url", "")
    return None


def create_issue(repo: str, title: str, body: str, labels: List[str]) -> Optional[str]:
    """Create a GitHub issue and return its URL."""
    result = _run_gh([
        "issue", "create",
        "--repo", repo,
        "--title", title,
        "--body", body,
        "--label", ",".join(labels),
    ], check=False)
    if result.returncode != 0:
        return None
    # gh prints the issue URL to stdout
    return result.stdout.strip()


def list_feedback_issues(repo: str) -> List[Dict[str, Any]]:
    """List open skill-feedback issues."""
    result = _run_gh([
        "issue", "list",
        "--repo", repo,
        "--label", "skill-feedback",
        "--state", "open",
        "--json", "number,title,labels,url",
        "--limit", "50",
    ], check=False)
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


# ── Session metadata ─────────────────────────────────────────────────────────

def extract_session_metadata(graph: MCGSGraph) -> Dict[str, Any]:
    """Extract session summary from the MCGS graph for issue context."""
    total = len(graph.nodes)
    evaluated = sum(1 for n in graph.nodes if n.status == "evaluated")
    failed = sum(1 for n in graph.nodes if n.status == "failed")
    mode = "multi-objective" if graph.config.multi_objective else "single-objective"

    best = None
    best_obj = None
    for n in graph.nodes:
        if n.status != "evaluated" or n.objective is None:
            continue
        if best is None:
            best = n
            best_obj = n.objective
        elif graph.config.minimize and n.objective < best_obj:
            best = n
            best_obj = n.objective
        elif not graph.config.minimize and n.objective > best_obj:
            best = n
            best_obj = n.objective

    # Determine stop reason
    stop_reason = "unknown"
    if graph.config.max_iterations and graph.total_iterations >= graph.config.max_iterations:
        stop_reason = f"max_iterations ({graph.config.max_iterations})"
    elif graph.config.max_time_minutes:
        stop_reason = f"max_time_minutes ({graph.config.max_time_minutes})"
    else:
        stop_reason = "user stopped or convergence"

    return {
        "total_iterations": graph.total_iterations,
        "total_nodes": total,
        "evaluated_nodes": evaluated,
        "failed_nodes": failed,
        "mode": mode,
        "research_goal": graph.config.research_goal,
        "best_node_id": best.id if best else None,
        "best_objective": best_obj,
        "stop_reason": stop_reason,
        "lessons_count": len(graph.lessons_learned),
    }


# ── Issue body formatting ────────────────────────────────────────────────────

def build_issue_body(
    category: str,
    body: str,
    suggested_fix: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Format a structured issue body."""
    sections = []

    # Session context
    if metadata:
        ctx = (
            f"## Session Context\n"
            f"- **Iterations**: {metadata['total_iterations']}\n"
            f"- **Nodes**: {metadata['total_nodes']} total, "
            f"{metadata['evaluated_nodes']} evaluated, "
            f"{metadata['failed_nodes']} failed\n"
            f"- **Mode**: {metadata['mode']}\n"
            f"- **Research goal**: {metadata.get('research_goal', 'N/A')}\n"
            f"- **Stop reason**: {metadata['stop_reason']}"
        )
        sections.append(ctx)

    # Description
    sections.append(f"## Description\n\n{body}")

    # Suggested fix
    if suggested_fix:
        sections.append(f"## Suggested Fix\n\n{suggested_fix}")

    # Footer
    sections.append(
        "---\n"
        "*Filed automatically by the meta-discovery skill (Phase 4: Feedback)*"
    )

    return "\n\n".join(sections)


# ── Local fallback ───────────────────────────────────────────────────────────

def write_local_fallback(
    items: List[Dict[str, str]],
    metadata: Optional[Dict[str, Any]],
    output_path: Path,
) -> str:
    """Write feedback to a local markdown file when gh is unavailable."""
    lines = ["# Skill Feedback (local fallback)\n"]

    if metadata:
        lines.append("## Session Context\n")
        lines.append(f"- Iterations: {metadata['total_iterations']}")
        lines.append(f"- Nodes: {metadata['total_nodes']} total, "
                      f"{metadata['evaluated_nodes']} evaluated, "
                      f"{metadata['failed_nodes']} failed")
        lines.append(f"- Mode: {metadata['mode']}")
        lines.append(f"- Stop reason: {metadata['stop_reason']}")
        lines.append("")

    for i, item in enumerate(items, 1):
        lines.append(f"## {i}. [{item['category']}] {item['title']}\n")
        lines.append(item["body"])
        if item.get("suggested_fix"):
            lines.append(f"\n**Suggested fix:** {item['suggested_fix']}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path)


# ── Main logic ───────────────────────────────────────────────────────────────

def process_items(
    items: List[Dict[str, str]],
    repo: str,
    metadata: Optional[Dict[str, Any]],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Process a list of feedback items. Returns JSON-serializable results."""
    results = {"created": [], "duplicates": [], "errors": []}

    if not dry_run:
        ensure_labels(repo)

    for i, item in enumerate(items):
        title = item["title"]
        category = item["category"]
        body_text = item["body"]
        suggested_fix = item.get("suggested_fix", "")

        if category not in VALID_CATEGORIES:
            results["errors"].append({
                "title": title,
                "error": f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}",
            })
            continue

        # Check for duplicates
        if not dry_run:
            dup_url = check_duplicate(repo, title)
            if dup_url:
                results["duplicates"].append({"title": title, "existing_url": dup_url})
                continue

        labels = ["skill-feedback", category]
        full_body = build_issue_body(category, body_text, suggested_fix, metadata)

        if dry_run:
            results["created"].append({
                "title": title,
                "labels": labels,
                "url": "(dry run — not created)",
                "body_preview": full_body[:200] + "..." if len(full_body) > 200 else full_body,
            })
        else:
            url = create_issue(repo, title, full_body, labels)
            if url:
                results["created"].append({"title": title, "labels": labels, "url": url})
            else:
                results["errors"].append({"title": title, "error": "gh issue create failed"})

            # Rate limit delay between issues
            if i < len(items) - 1:
                time.sleep(ISSUE_DELAY_SECONDS)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Create structured GitHub issues from session feedback.",
    )
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument("--graph", help="Path to mcgs_graph.json for session metadata")
    parser.add_argument("--check", action="store_true",
                        help="Check gh auth and list existing skill-feedback issues")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be filed without creating issues")

    # Single-issue mode
    parser.add_argument("--category", choices=VALID_CATEGORIES,
                        help="Issue category")
    parser.add_argument("--title", help="Issue title")
    parser.add_argument("--body", help="Issue description")
    parser.add_argument("--suggested-fix", default="", help="Optional suggested fix")

    # Batch mode
    parser.add_argument("--batch", help="Path to JSON file with feedback items")

    args = parser.parse_args()

    # ── Check mode ───────────────────────────────────────────────────────
    if args.check:
        auth_ok = check_gh_auth()
        existing = list_feedback_issues(args.repo) if auth_ok else []
        result = {
            "status": "ok" if auth_ok else "error",
            "gh_authenticated": auth_ok,
            "existing_issues": len(existing),
            "issues": existing,
        }
        if not auth_ok:
            result["message"] = (
                "gh is not authenticated. Run `gh auth login` or use local fallback."
            )
        print(json.dumps(result, indent=2))
        return

    # ── Load session metadata ────────────────────────────────────────────
    metadata = None
    if args.graph:
        graph_path = Path(args.graph)
        if graph_path.exists():
            graph = load_graph(str(graph_path))
            metadata = extract_session_metadata(graph)

    # ── Build items list ─────────────────────────────────────────────────
    items: List[Dict[str, str]] = []

    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(json.dumps({"status": "error", "message": f"Batch file not found: {args.batch}"}))
            sys.exit(1)
        items = json.loads(batch_path.read_text(encoding="utf-8"))
    elif args.title and args.category and args.body:
        items = [{
            "category": args.category,
            "title": args.title,
            "body": args.body,
            "suggested_fix": args.suggested_fix,
        }]
    else:
        print(json.dumps({
            "status": "error",
            "message": "Provide --batch or (--category, --title, --body)",
        }))
        sys.exit(1)

    if not items:
        print(json.dumps({"status": "ok", "message": "No feedback items to file."}))
        return

    # ── Check auth and process ───────────────────────────────────────────
    if not args.dry_run and not check_gh_auth():
        # Fall back to local file
        fallback_path = Path("mcgs_report/skill_feedback.md")
        path = write_local_fallback(items, metadata, fallback_path)
        print(json.dumps({
            "status": "fallback",
            "message": f"gh not authenticated. Feedback written to {path}",
            "path": path,
            "items_count": len(items),
        }))
        return

    results = process_items(items, args.repo, metadata, dry_run=args.dry_run)
    results["status"] = "ok"
    results["summary"] = {
        "created": len(results["created"]),
        "duplicates": len(results["duplicates"]),
        "errors": len(results["errors"]),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
