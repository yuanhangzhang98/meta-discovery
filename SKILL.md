---
name: meta-discovery
description: "Meta-Discovery: LLM-driven automated scientific research and code optimization using Monte-Carlo Graph Search (MCGS) with consensus objective aggregation. Supports single-objective mode (simple scalar evaluation) and multi-objective mode where evaluation criteria co-evolve alongside solutions, guided by meta-agent oversight. Use this skill whenever the user wants to systematically explore a design space by iteratively modifying a codebase, evaluating changes against objectives, and using UCB-based exploration-exploitation to guide the search. Triggers on: 'meta-discovery', 'Monte Carlo Graph Search', 'MCGS', 'automated research loop', 'automated discovery', 'explore design space', 'UCB search', 'exploration-exploitation optimization', 'planner and designer agents', 'iterative code optimization with LLM', 'multi-objective optimization', 'consensus objective', 'meta-optimization', 'scientific discovery', or any request to run an automated loop that repeatedly modifies code, evaluates it, and decides what to try next."
---

# Meta-Discovery Skill

This skill turns you (Claude) into the orchestrator of an automated research system. You drive a loop that uses specialized subagents — a **Planner** (read-only, strategic), a **Designer** (code-modifying, tactical), and optionally an **Objective Agent** (generates evaluation functions) — along with deterministic helper scripts for evaluation, consensus aggregation, and UCB score computation.

Each design is a git branch. The search history is a directed acyclic graph stored in `mcgs_graph.json`. The UCB algorithm balances exploiting the best designs with exploring under-visited lineages.

## Two Modes

- **Single-objective mode** (default): one evaluation script outputs a float. Simple and fast. Good when you know exactly what metric to optimize.
- **Multi-objective mode**: an experiment script outputs JSON metrics; multiple objective functions score those metrics; consensus aggregation combines them into a single robust ranking. Objectives co-evolve over time, guided by meta-agent analysis. Use this when the research goal is complex or when a single metric could be gamed.

## How to Use This Skill

This skill is split into phases. **Read only the phase you need** to keep your context window efficient:

### Phase 1: Setup (one-time)
Read `phases/setup.md` and `phases/notes.md`. Understand the project, write the experiment script, initialize MCGS, evaluate the baseline.

### Phase 2: The MCGS Loop (iterative)
Read `phases/loop.md`. This is the main iteration loop — planner, designer, execute, score, UCB.

### Phase 3: Summary (one-time, at the end)
Read `phases/summary.md`. Report results, show best diff, offer to apply.

## When to Read Reference Files

Read these **before spawning the corresponding subagent** — inject their content into the subagent prompt:

- `references/planner_guide.md` — before spawning the Planner
- `references/designer_guide.md` — before spawning the Designer
- `references/objective_agent_guide.md` — before spawning the Objective Agent (multi-objective only)
- `references/meta_agent_guide.md` — before performing meta-agent analysis (multi-objective only)
- `references/mcgs_algorithm.md` — for mathematical details (read once for understanding, not every iteration)

## Quick Reference: Key Scripts

All scripts are in `{SKILL_DIR}/scripts/`. The most important ones:

| Script | When to use |
|--------|-------------|
| `init_mcgs.py` | Phase 1: initialize MCGS in the repo |
| `run_iteration.py` | Phase 2: full post-designer pipeline (Steps 7-11) |
| `validate_agent_output.py` | Phase 2: validate subagent outputs before use |
| `compute_ucb.py` | Phase 2: update UCB scores (Step 1) |
| `hpo_tune.py` | Phase 2: periodic hyperparameter optimization |
| `multi_fidelity.py` | Phase 2: multi-fidelity promotion sweeps |
| `register_node.py` | Utility: register a node in the graph |
| `graph_utils.py` | Utility: inspect graph, clean up worktrees, add lessons |

For full documentation of all scripts, data model, and configuration, see `DOCUMENTATION.md`.

## Getting Started

1. Read `phases/setup.md` and `phases/notes.md`
2. Complete Phase 1 setup with the user
3. Read `phases/loop.md`
4. Run the MCGS loop
5. Read `phases/summary.md` when done
