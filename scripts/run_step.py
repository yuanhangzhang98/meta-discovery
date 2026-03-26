#!/usr/bin/env python3
"""MCGS iteration state machine.

Manages iteration progress so the orchestrator only needs to:
  1. Call `next` to find out what to do
  2. Do the thing (spawn subagent or run command)
  3. Call `complete` with the result
  4. Repeat until iteration_complete

Every `next` output includes self-contained `instructions` — step-by-step
directions the orchestrator can follow even without access to loop.md.

Usage:
    # Get next action:
    python run_step.py next --graph mcgs_graph.json --skill-dir /path/to/meta-discovery

    # Complete a step:
    python run_step.py complete --graph mcgs_graph.json --step planner --result '{"research_direction": "...", ...}'

    # Start a fresh iteration explicitly:
    python run_step.py next --graph mcgs_graph.json --skill-dir /path/to/meta-discovery --new-iteration
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import (
    MCGSGraph,
    IterationState,
    load_graph,
    save_graph,
    format_graph_summary,
    format_node_table,
    format_objective_table,
)

# Step ordering — steps in brackets are conditional on periodic tasks
STEP_ORDER = [
    "start",
    "objective_agent",      # conditional: multi-obj + interval match
    "meta_analysis",        # conditional: multi-obj + interval match
    "planner",
    "prepare_worktree",
    "designer",
    "post_designer_pipeline",
    "hpo",                  # conditional: interval match
    "multi_fidelity",       # conditional: multi-fidelity enabled
    "report",
    "iteration_complete",
]

CONDITIONAL_STEPS = {"objective_agent", "meta_analysis", "hpo", "multi_fidelity"}


# ──────────────────────────────────────────────────────────────────────────────
# Instruction template loading
# ──────────────────────────────────────────────────────────────────────────────

_PROTOCOL_REMINDER = (
    "DISPATCH LOOP: call `run_step.py next` -> follow the `instructions` field below -> "
    "call `run_step.py complete` -> repeat until iteration_complete. "
    "NEVER skip run_step.py. NEVER invent your own steps. NEVER bypass periodic tasks."
)


def _read_file(path: Path) -> str:
    """Read a text file, returning empty string if not found."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"(File not found: {path.name})"


def _read_guide(skill_dir: Path, filename: str) -> str:
    """Read a reference guide file."""
    return _read_file(skill_dir / "references" / filename)


def _read_instruction_template(skill_dir: Path, action_name: str) -> str:
    """Read an instruction template from instructions/ directory."""
    return _read_file(skill_dir / "instructions" / f"{action_name}.md")


def _fill_template(template: str, **kwargs: Any) -> str:
    """Fill {placeholder} tokens in a template with concrete values.

    Missing keys are left as-is (no KeyError). Values are converted to str.
    """
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


def _wrap_with_protocol(
    action: Dict[str, Any],
    graph_path: str,
    skill_dir: str,
    repo_dir: str,
) -> Dict[str, Any]:
    """Inject protocol reminder and complete_command into every action output."""
    action["protocol"] = _PROTOCOL_REMINDER

    # Add complete_command for non-terminal steps
    step = action.get("step", "")
    if step not in ("iteration_complete",):
        scripts_dir = Path(__file__).parent
        action["complete_command"] = (
            f"python {scripts_dir / 'run_step.py'} complete "
            f"--graph {graph_path} --step {step} --result '{{}}'"
        )

    return action


# ──────────────────────────────────────────────────────────────────────────────
# Periodic task logic
# ──────────────────────────────────────────────────────────────────────────────

def _check_periodic_tasks(graph: MCGSGraph, iteration: int) -> List[str]:
    """Determine which periodic tasks are due for this iteration."""
    tasks = []
    config = graph.config
    if config.multi_objective:
        if config.objective_interval > 0 and iteration % config.objective_interval == 0:
            tasks.append("objective_agent")
        if config.meta_interval > 0 and iteration % config.meta_interval == 0:
            tasks.append("meta_analysis")
    if config.hpo_interval > 0 and iteration % config.hpo_interval == 0:
        tasks.append("hpo")
    if config.multi_fidelity:
        tasks.append("multi_fidelity")
    return tasks


def _next_step(current: str, periodic_tasks: List[str]) -> str:
    """Advance to the next step, skipping conditional steps that aren't due."""
    idx = STEP_ORDER.index(current)
    for next_step in STEP_ORDER[idx + 1:]:
        if next_step in CONDITIONAL_STEPS and next_step not in periodic_tasks:
            continue
        return next_step
    return "iteration_complete"


# ──────────────────────────────────────────────────────────────────────────────
# Helper data extractors
# ──────────────────────────────────────────────────────────────────────────────

def _get_recent_experiment_results(graph: MCGSGraph, count: int = 3) -> List[Dict[str, Any]]:
    """Get experiment_results from recently evaluated nodes."""
    evaluated = [n for n in graph.nodes if n.experiment_results is not None]
    evaluated.sort(key=lambda n: n.id, reverse=True)
    results = []
    for n in evaluated[:count]:
        results.append({
            "node_id": n.id,
            "short_name": n.short_name,
            "experiment_results": n.experiment_results,
        })
    return results


def _get_top_ucb_node(graph: MCGSGraph) -> Optional[int]:
    """Get the node ID with highest UCB among evaluated nodes."""
    evaluated = [n for n in graph.nodes if n.status == "evaluated"]
    if not evaluated:
        return 0
    return max(evaluated, key=lambda n: n.ucb_score).id


# ──────────────────────────────────────────────────────────────────────────────
# Action generators for each step
# ──────────────────────────────────────────────────────────────────────────────

def _action_objective_agent(
    graph: MCGSGraph, state: IterationState, skill_dir: Path, graph_path: str,
) -> Dict[str, Any]:
    """Generate action for spawning the Objective Agent."""
    guide = _read_guide(skill_dir, "objective_agent_guide.md")
    recent = _get_recent_experiment_results(graph)
    sample_results = recent[0]["experiment_results"] if recent else {}
    scripts_dir = Path(__file__).parent

    obj_directions = ""
    if graph.meta_state:
        obj_directions = graph.meta_state.objective_directions or "No specific guidance yet."

    prompt_context = {
        "guide": guide,
        "research_goal": graph.config.research_goal,
        "existing_objectives": format_objective_table(graph),
        "example_experiment_results": json.dumps(recent, indent=2),
        "sample_results_for_validation": json.dumps(sample_results),
        "meta_agent_directions": obj_directions,
        "next_objective_id": len(graph.objectives),
        "objectives_dir": graph.config.objectives_dir,
    }

    # Build instructions from template
    template = _read_instruction_template(skill_dir, "objective_agent")
    instructions = _fill_template(
        template,
        description=f"Generate a new objective function (iteration {state.iteration})",
        scripts_dir=str(scripts_dir),
        **prompt_context,
    )

    return {
        "action": "spawn_objective_agent",
        "step": "objective_agent",
        "description": f"Generate a new objective function (iteration {state.iteration})",
        "instructions": instructions,
        "prompt_context": prompt_context,
    }


def _action_meta_analysis(
    graph: MCGSGraph, state: IterationState, skill_dir: Path, graph_path: str,
) -> Dict[str, Any]:
    """Generate action for meta-agent analysis (orchestrator performs directly)."""
    guide = _read_guide(skill_dir, "meta_agent_guide.md")
    scripts_dir = Path(__file__).parent

    commands = {
        "consensus_verbose": f"python {scripts_dir / 'consensus.py'} --graph {graph_path} --verbose",
        "recompute_consensus": f"python {scripts_dir / 'consensus.py'} --graph {graph_path}",
        "recompute_ucb": f"python {scripts_dir / 'compute_ucb.py'} --graph {graph_path}",
    }

    template = _read_instruction_template(skill_dir, "meta_analysis")
    instructions = _fill_template(
        template,
        description=f"Perform meta-agent analysis (iteration {state.iteration})",
        guide=guide,
        consensus_verbose_cmd=commands["consensus_verbose"],
        recompute_consensus_cmd=commands["recompute_consensus"],
        recompute_ucb_cmd=commands["recompute_ucb"],
    )

    return {
        "action": "run_meta_analysis",
        "step": "meta_analysis",
        "description": f"Perform meta-agent analysis (iteration {state.iteration})",
        "instructions": instructions,
        "prompt_context": {
            "guide": guide,
            "graph_summary": format_graph_summary(graph),
            "objective_table": format_objective_table(graph),
        },
        "commands": commands,
    }


def _action_planner(
    graph: MCGSGraph, state: IterationState, skill_dir: Path, graph_path: str,
) -> Dict[str, Any]:
    """Generate action for spawning the Planner."""
    guide = _read_guide(skill_dir, "planner_guide.md")
    scripts_dir = Path(__file__).parent

    prompt_context = {
        "guide": guide,
        "research_goal": graph.config.research_goal,
        "graph_summary": format_graph_summary(graph),
        "node_table": format_node_table(graph),
        "lessons_learned": graph.lessons_learned or ["None yet."],
        "meta_state": graph.meta_state.to_dict() if graph.meta_state else None,
    }

    template = _read_instruction_template(skill_dir, "planner")
    lessons_str = "\n".join(f"- {l}" for l in (graph.lessons_learned or ["None yet."]))
    instructions = _fill_template(
        template,
        description=f"Analyze search history and decide next direction (iteration {state.iteration})",
        scripts_dir=str(scripts_dir),
        research_goal=graph.config.research_goal,
        guide=guide,
        graph_summary=format_graph_summary(graph),
        node_table=format_node_table(graph),
        lessons_learned=lessons_str,
        complete_command_with_result=(
            f"python {scripts_dir / 'run_step.py'} complete "
            f"--graph {graph_path} --step planner --result '<planner_json>'"
        ),
    )

    return {
        "action": "spawn_planner",
        "step": "planner",
        "description": f"Analyze search history and decide next direction (iteration {state.iteration})",
        "instructions": instructions,
        "prompt_context": prompt_context,
    }


def _action_prepare_worktree(
    graph: MCGSGraph, state: IterationState, repo_dir: str, graph_path: str, skill_dir: Path,
) -> Dict[str, Any]:
    """Generate action for creating the designer's worktree."""
    parent_id = state.parent_node_id
    if parent_id is None:
        parent_id = state.reference_node_ids[0] if state.reference_node_ids else 0
    new_id = graph.next_id
    worktree_path = f"/tmp/mcgs-worktree-{new_id}"

    parent_node = graph.get_node(parent_id)
    parent_branch = parent_node.branch if parent_node else MCGSGraph.node_branch_name(parent_id)

    command = f"git worktree add {worktree_path} {parent_branch}"

    # Store worktree path eagerly so it's available even if orchestrator
    # doesn't pass it back in cmd_complete result JSON
    state.designer_worktree = worktree_path

    template = _read_instruction_template(skill_dir, "prepare_worktree")
    instructions = _fill_template(
        template,
        description=f"Create designer worktree from node {parent_id}",
        command=command,
    )

    return {
        "action": "run_command",
        "step": "prepare_worktree",
        "description": f"Create designer worktree from node {parent_id}",
        "instructions": instructions,
        "command": command,
        "worktree": worktree_path,
        "parent_branch": parent_branch,
        "parent_node_id": parent_id,
        "new_node_id": new_id,
    }


def _action_designer(
    graph: MCGSGraph, state: IterationState, skill_dir: Path, graph_path: str,
) -> Dict[str, Any]:
    """Generate action for spawning the Designer."""
    guide = _read_guide(skill_dir, "designer_guide.md")
    planner = state.planner_output or {}
    ref_ids = state.reference_node_ids
    parent_id = state.parent_node_id

    prompt_context = {
        "guide": guide,
        "research_goal": graph.config.research_goal,
        "research_direction": planner.get("research_direction", "Continue exploring"),
        "reference_node_ids": ref_ids,
        "focus_areas": planner.get("focus_areas", []),
        "avoid_areas": planner.get("avoid_areas", []),
        "lessons_learned": graph.lessons_learned or ["None yet."],
        "worktree": state.designer_worktree,
        "parent_node_id": parent_id,
    }

    lessons_str = "\n".join(f"- {l}" for l in (graph.lessons_learned or ["None yet."]))
    template = _read_instruction_template(skill_dir, "designer")
    instructions = _fill_template(
        template,
        description=f"Make one code modification (iteration {state.iteration})",
        guide=guide,
        research_goal=graph.config.research_goal,
        research_direction=planner.get("research_direction", "Continue exploring"),
        reference_node_ids=str(ref_ids),
        focus_areas=str(planner.get("focus_areas", [])),
        avoid_areas=str(planner.get("avoid_areas", [])),
        lessons_learned=lessons_str,
        worktree=state.designer_worktree,
        parent_node_id=str(parent_id),
    )

    return {
        "action": "spawn_designer",
        "step": "designer",
        "description": f"Make one code modification (iteration {state.iteration})",
        "instructions": instructions,
        "prompt_context": prompt_context,
    }


def _action_post_designer(
    graph: MCGSGraph, state: IterationState, repo_dir: str,
    timeout: int, graph_path: str, skill_dir: Path,
) -> Dict[str, Any]:
    """Generate action for the post-designer pipeline."""
    scripts_dir = Path(__file__).parent
    ref_nodes = ",".join(str(r) for r in state.reference_node_ids)
    parent_id = state.parent_node_id
    parent_node = graph.get_node(parent_id)
    parent_branch = parent_node.branch if parent_node else MCGSGraph.node_branch_name(parent_id)

    # Determine protected files from config
    protected = f"{graph.config.eval_script},mcgs_graph.json"

    # The command now auto-reads parent_edges from mcgs_design_output.json
    command = (
        f"python {scripts_dir / 'run_iteration.py'} run "
        f"--worktree {state.designer_worktree} "
        f"--reference-nodes {ref_nodes} "
        f"--protected \"{protected}\" "
        f"--parent-branch {parent_branch} "
        f"--graph {repo_dir}/mcgs_graph.json "
        f"--repo-dir {repo_dir} "
        f"--timeout {timeout}"
    )

    template = _read_instruction_template(skill_dir, "post_designer")
    instructions = _fill_template(
        template,
        description="Validate, commit, execute, score, and update UCB",
        command=command,
    )

    return {
        "action": "run_command",
        "step": "post_designer_pipeline",
        "description": "Validate, commit, execute, score, and update UCB",
        "instructions": instructions,
        "command": command,
    }


def _action_hpo(
    graph: MCGSGraph, state: IterationState, repo_dir: str,
    graph_path: str, skill_dir: Path,
) -> Dict[str, Any]:
    """Generate action for HPO."""
    scripts_dir = Path(__file__).parent
    config = graph.config
    command = (
        f"python {scripts_dir / 'hpo_tune.py'} "
        f"--graph {repo_dir}/mcgs_graph.json "
        f"--repo-dir {repo_dir} "
        f"--auto "
        f"--max-iter {config.hpo_max_iter} "
        f"--backend {config.hpo_backend}"
    )

    template = _read_instruction_template(skill_dir, "hpo")
    instructions = _fill_template(
        template,
        description="Run hyperparameter optimization (auto-select best untuned node)",
        command=command,
        repo_dir=repo_dir,
    )

    return {
        "action": "run_command",
        "step": "hpo",
        "description": "Run hyperparameter optimization (auto-select best untuned node)",
        "instructions": instructions,
        "command": command,
    }


def _action_multi_fidelity(
    graph: MCGSGraph, state: IterationState, repo_dir: str,
    graph_path: str, skill_dir: Path,
) -> Dict[str, Any]:
    """Generate action for multi-fidelity promotion sweep."""
    scripts_dir = Path(__file__).parent
    command = (
        f"python {scripts_dir / 'multi_fidelity.py'} "
        f"--graph {repo_dir}/mcgs_graph.json "
        f"--repo-dir {repo_dir} "
        f"promote-sweep"
    )

    template = _read_instruction_template(skill_dir, "multi_fidelity")
    instructions = _fill_template(
        template,
        description="Run multi-fidelity promotion sweep",
        command=command,
    )

    return {
        "action": "run_command",
        "step": "multi_fidelity",
        "description": "Run multi-fidelity promotion sweep",
        "instructions": instructions,
        "command": command,
    }


def _action_report(
    graph: MCGSGraph, state: IterationState, skill_dir: Path, graph_path: str,
) -> Dict[str, Any]:
    """Generate iteration report."""
    scripts_dir = Path(__file__).parent
    node = graph.get_node(state.new_node_id) if state.new_node_id is not None else None
    best = graph.get_best_node()

    summary = {
        "iteration": state.iteration,
        "new_node_id": state.new_node_id,
    }
    if node:
        summary["new_node_name"] = node.short_name
        summary["new_node_objective"] = node.objective
        summary["new_node_consensus"] = node.consensus_score
        summary["new_node_status"] = node.status
    if best:
        summary["best_node_id"] = best.id
        summary["best_node_name"] = best.short_name
        summary["best_objective"] = best.objective

    summary["periodic_tasks_run"] = state.periodic_tasks
    summary["total_nodes"] = len(graph.nodes)

    template = _read_instruction_template(skill_dir, "report")
    instructions = _fill_template(
        template,
        description=f"Iteration {state.iteration} complete",
        summary=json.dumps(summary, indent=2, default=str),
        scripts_dir=str(scripts_dir),
        graph_path=graph_path,
    )

    return {
        "action": "report",
        "step": "report",
        "description": f"Iteration {state.iteration} complete",
        "instructions": instructions,
        "summary": summary,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main commands: next and complete
# ──────────────────────────────────────────────────────────────────────────────

def cmd_next(
    graph_path: str,
    skill_dir: str,
    repo_dir: str = ".",
    timeout: int = 300,
    new_iteration: bool = False,
) -> Dict[str, Any]:
    """Determine the next action the orchestrator should take."""
    graph = load_graph(graph_path)
    skill_path = Path(skill_dir)
    state = graph.iteration_state

    # If we're at iteration_complete and not forcing new, check stop conditions
    if state is not None and state.step == "iteration_complete" and not new_iteration:
        stop_reason = graph.check_stop_conditions()

        if stop_reason:
            stop_decision = (
                f"**STOP**: {stop_reason}. "
                f"Proceed to Phase 3 — read `phases/summary.md` and generate the final report."
            )
        else:
            stop_decision = (
                "No stop condition met. **CONTINUE**: call `run_step.py next` again "
                "(with `--new-iteration`) to start the next iteration immediately."
            )

        template = _read_instruction_template(skill_path, "iteration_complete")
        instructions = _fill_template(
            template, iteration=str(state.iteration), stop_decision=stop_decision,
        )
        result = {
            "action": "iteration_complete",
            "step": "iteration_complete",
            "description": f"Iteration {state.iteration} finished",
            "instructions": instructions,
            "iteration": state.iteration,
            "should_stop": stop_reason is not None,
            "stop_reason": stop_reason,
        }
        return _wrap_with_protocol(result, graph_path, skill_dir, repo_dir)

    # Start a new iteration if needed
    if state is None or state.step == "iteration_complete" or new_iteration:
        iteration = graph.total_iterations + 1
        periodic = _check_periodic_tasks(graph, iteration)
        state = IterationState(
            iteration=iteration,
            step="start",
            periodic_tasks=periodic,
        )
        graph.iteration_state = state
        save_graph(graph, graph_path)

    # Advance from current step
    step = state.step

    if step == "start":
        # Advance past start to the first real step
        step = _next_step("start", state.periodic_tasks)
        state.step = step
        save_graph(graph, graph_path)

    # Generate action for current step
    if step == "objective_agent":
        result = _action_objective_agent(graph, state, skill_path, graph_path)
    elif step == "meta_analysis":
        result = _action_meta_analysis(graph, state, skill_path, graph_path)
    elif step == "planner":
        result = _action_planner(graph, state, skill_path, graph_path)
    elif step == "prepare_worktree":
        result = _action_prepare_worktree(graph, state, repo_dir, graph_path, skill_path)
    elif step == "designer":
        result = _action_designer(graph, state, skill_path, graph_path)
    elif step == "post_designer_pipeline":
        result = _action_post_designer(graph, state, repo_dir, timeout, graph_path, skill_path)
    elif step == "hpo":
        result = _action_hpo(graph, state, repo_dir, graph_path, skill_path)
    elif step == "multi_fidelity":
        result = _action_multi_fidelity(graph, state, repo_dir, graph_path, skill_path)
    elif step == "report":
        # Reload graph to get latest node data
        graph = load_graph(graph_path)
        state = graph.iteration_state
        result = _action_report(graph, state, skill_path, graph_path)
    else:
        result = {"action": "error", "error": f"Unknown step: {step}"}

    return _wrap_with_protocol(result, graph_path, skill_dir, repo_dir)


def cmd_complete(
    graph_path: str,
    step: str,
    result_json: str = "{}",
) -> Dict[str, Any]:
    """Process the result of a completed step and advance the state."""
    graph = load_graph(graph_path)
    state = graph.iteration_state

    if state is None:
        return {"error": "No active iteration. Call 'next' first."}

    if step != state.step:
        return {"error": f"Expected step '{state.step}', got '{step}'"}

    try:
        result = json.loads(result_json) if isinstance(result_json, str) else result_json
    except json.JSONDecodeError:
        result = {}

    output: Dict[str, Any] = {"step": step, "status": "ok"}

    # Process step-specific results
    if step == "objective_agent":
        output["note"] = "Objective agent complete. Validate and register in graph."

    elif step == "meta_analysis":
        output["note"] = "Meta-analysis complete."

    elif step == "planner":
        # Store planner output for designer
        state.planner_output = result
        ref_ids = result.get("reference_node_ids", [])
        if not ref_ids:
            # Fallback: use top-UCB node
            top = _get_top_ucb_node(graph)
            ref_ids = [top] if top is not None else [0]
            output["warning"] = f"No reference_node_ids in planner output, falling back to node {ref_ids[0]}"
        state.reference_node_ids = ref_ids
        state.parent_node_id = ref_ids[0]

    elif step == "prepare_worktree":
        # Store worktree path and new node ID
        state.designer_worktree = result.get("worktree", state.designer_worktree)
        if "new_node_id" in result:
            state.new_node_id = result["new_node_id"]

    elif step == "designer":
        pass

    elif step == "post_designer_pipeline":
        # Extract new node ID from pipeline output
        commit_info = result.get("commit", {})
        if "node_id" in commit_info:
            state.new_node_id = commit_info["node_id"]
        elif "node_id" in result:
            state.new_node_id = result["node_id"]

    elif step == "hpo":
        output["note"] = "HPO complete."

    elif step == "multi_fidelity":
        output["note"] = "Multi-fidelity promotion sweep complete."

    elif step == "report":
        # Increment total_iterations and finalize
        graph.total_iterations = state.iteration

    # Advance to next step
    state.completed_steps.append(step)
    state.step = _next_step(step, state.periodic_tasks)
    output["next_step"] = state.step

    save_graph(graph, graph_path)
    return output


def cmd_reset(graph_path: str) -> Dict[str, Any]:
    """Reset iteration state to match actual graph state.

    Useful after manual operations that desync the state machine.
    Recomputes total_iterations from evaluated/failed nodes and clears
    the current iteration state so the next `next` call starts fresh.
    """
    graph = load_graph(graph_path)
    # Count completed iterations = nodes with terminal status, minus baseline
    terminal = [n for n in graph.nodes if n.status in ("evaluated", "failed")]
    old_total = graph.total_iterations
    graph.total_iterations = max(0, len(terminal) - 1)  # minus baseline node-0
    graph.iteration_state = None
    save_graph(graph, graph_path)
    return {
        "status": "reset",
        "old_total_iterations": old_total,
        "new_total_iterations": graph.total_iterations,
        "terminal_nodes": len(terminal),
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MCGS iteration state machine")
    sub = parser.add_subparsers(dest="action", required=True)

    # next
    p_next = sub.add_parser("next", help="Get the next action to perform")
    p_next.add_argument("--graph", default="mcgs_graph.json")
    p_next.add_argument("--skill-dir", default=str(Path(__file__).parent.parent),
                        help="Path to meta-discovery skill directory")
    p_next.add_argument("--repo-dir", default=".", help="Path to git repository")
    p_next.add_argument("--timeout", type=int, default=300, help="Experiment timeout")
    p_next.add_argument("--new-iteration", action="store_true",
                        help="Force start of a new iteration")

    # complete
    p_done = sub.add_parser("complete", help="Complete a step with its result")
    p_done.add_argument("--graph", default="mcgs_graph.json")
    p_done.add_argument("--step", required=True, help="Step name being completed")
    p_done.add_argument("--result", default="{}", help="JSON result from the step")

    # reset
    p_reset = sub.add_parser("reset", help="Reset iteration state to match actual graph")
    p_reset.add_argument("--graph", default="mcgs_graph.json")

    args = parser.parse_args()

    if args.action == "next":
        output = cmd_next(
            graph_path=args.graph,
            skill_dir=args.skill_dir,
            repo_dir=args.repo_dir,
            timeout=args.timeout,
            new_iteration=args.new_iteration,
        )
    elif args.action == "complete":
        output = cmd_complete(
            graph_path=args.graph,
            step=args.step,
            result_json=args.result,
        )
    elif args.action == "reset":
        output = cmd_reset(graph_path=args.graph)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
