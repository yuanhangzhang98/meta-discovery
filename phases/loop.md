# Phase 2: The MCGS Loop

> **Context-loss safety**: Every `run_step.py next` output includes an `instructions` field
> with complete step-by-step directions and a `complete_command` field. If you've lost context
> about how to handle an action, just follow those fields — they contain everything you need.
> If you've lost track of the dispatch loop entirely, re-read `phases/loop_cheatsheet.md`.

The iteration loop is driven by `run_step.py` — a state machine that tracks progress, triggers periodic tasks automatically, and tells you exactly what to do next. You no longer need to remember 13 steps or track iteration counts for periodic tasks.

## How the Loop Works

Each iteration follows this dispatch loop:

```
while not done:
    instruction = run_step.py next --graph {REPO_DIR}/mcgs_graph.json --skill-dir {SKILL_DIR} --repo-dir {REPO_DIR}

    match instruction.action:
        "spawn_objective_agent"    → spawn subagent, then complete
        "run_meta_analysis"        → you analyze directly, then complete
        "spawn_planner"            → spawn subagent, then complete
        "run_command"              → execute the command, then complete
        "spawn_designer"           → spawn subagent, then complete
        "report"                   → display summary to user, then complete
        "iteration_complete"       → if should_stop: Phase 3; else: next --new-iteration

    run_step.py complete --graph {REPO_DIR}/mcgs_graph.json --step {step} --result '{result_json}'
```

The state machine handles:
- **Periodic task scheduling**: objective generation, meta-analysis, HPO, and multi-fidelity promotion are triggered automatically when their interval matches
- **Prompt context assembly**: each `spawn_*` action includes a `prompt_context` with the guide file, graph state, and all data the subagent needs
- **State persistence**: progress survives crashes — `run_step.py next` resumes from the last completed step

## Handling Each Action Type

### `spawn_objective_agent`

Spawn a **read-only subagent** using the prompt context provided:

```
You are the Objective Agent in a Monte-Carlo Graph Search system.

{instruction.prompt_context.guide}

## Research Goal
{instruction.prompt_context.research_goal}

## Existing Objectives
{instruction.prompt_context.existing_objectives}

## Example Experiment Results
{instruction.prompt_context.example_experiment_results}

## Meta-Agent Directions
{instruction.prompt_context.meta_agent_directions}

Generate a new objective function. Output:
1. The Python code in a fenced ```python block
2. A JSON metadata block with "name" and "description"
```

After the subagent returns:
1. Extract the Python code and JSON metadata
2. Save to `{objectives_dir}/objective_{next_id}.py`
3. Validate:
   ```bash
   python {SKILL_DIR}/scripts/validate_agent_output.py validate-objective \
       --file {objectives_dir}/objective_{next_id}.py \
       --sample-results '{instruction.prompt_context.sample_results_for_validation}'
   ```
4. **If invalid** → SendMessage (1 retry max), then skip if still broken
5. If valid: register via `graph.add_objective(...)` and save graph
6. Complete the step:
   ```bash
   python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json --step objective_agent
   ```

### `run_meta_analysis`

You (the orchestrator) perform this directly — no subagent. The instruction includes the guide and commands.

1. Run the consensus verbose command from `instruction.commands.consensus_verbose`
2. Review tau matrix: which objectives agree? Which are outliers? Are weights collapsing?
3. Decide: research_phase, weight_multipliers, weight_adders, objective_directions
4. Update `graph.meta_state` (save snapshot to history, apply weights/adders)
5. Recompute consensus and UCB using commands from `instruction.commands`
6. Complete:
   ```bash
   python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json --step meta_analysis
   ```

### `spawn_planner`

Spawn a **read-only subagent** using the prompt context:

```
You are the Planner Agent in a Monte-Carlo Graph Search system.

{instruction.prompt_context.guide}

## Research Goal
{instruction.prompt_context.research_goal}

## Project Context
{your description of the project, key source files, promising change directions}

## Graph Summary
{instruction.prompt_context.graph_summary}

## Node Table (ranked by UCB)
{instruction.prompt_context.node_table}

## Lessons Learned
{instruction.prompt_context.lessons_learned}

## Available Commands
- `git diff mcgs/node-X..mcgs/node-Y` — see code diff between two nodes
- `git show mcgs/node-X:path/to/file` — read a file on a specific node's branch

Analyze the search history and output your decision as a JSON block.
```

Parse the planner's JSON output. Validate:
```bash
python {SKILL_DIR}/scripts/validate_agent_output.py validate-planner --file /tmp/planner_output.json
```

If invalid → SendMessage (1 retry), then fallback (top-UCB node, generic direction).

Complete with the planner's JSON as the result:
```bash
python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json \
    --step planner --result '{planner_json}'
```

**Lightweight mode**: When the strategy is clear (e.g., a parameter sweep), you can skip the Planner subagent entirely. Construct the planner output yourself and pass it directly to `complete`:
```bash
python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json \
    --step planner --result '{"research_direction": "Try electrode=2500", "reference_node_ids": [5], "focus_areas": ["electrode parameter"], "avoid_areas": [], "current_phase": "systematic_search", "key_insights": ["Interpolating between 2000 and 3500"]}'
```

### `run_command` (prepare_worktree, post_designer_pipeline, hpo, multi_fidelity)

Execute the command shown in `instruction.command`. For long-running commands like `hpo`, you may run them in the background. HPO writes real-time progress to `{REPO_DIR}/hpo_progress.log` — tail this file to monitor progress while it runs.

For `post_designer_pipeline`:

1. Run the command — it **automatically reads** `mcgs_design_output.json` from the worktree for parent_edges, short_name, and description. No manual JSON extraction needed.
2. **If validation fails** → SendMessage to Designer (1 retry), then fallback

Complete with the command's JSON output:
```bash
python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json \
    --step post_designer_pipeline --result '{pipeline_json}'
```

### `spawn_designer`

Spawn a **code-modifying subagent** using the prompt context:

```
You are the Designer Agent in a Monte-Carlo Graph Search system.

{instruction.prompt_context.guide}

## Research Goal
{instruction.prompt_context.research_goal}

## Project Context
{your description of the project, modifiable files, protected files}

## Protected Files (DO NOT MODIFY)
{experiment script, mcgs_graph.json, test data}

## Lessons Learned
{instruction.prompt_context.lessons_learned}

## Planner's Direction
{instruction.prompt_context.research_direction}

## Reference Nodes: {instruction.prompt_context.reference_node_ids}
Focus areas: {instruction.prompt_context.focus_areas}
Avoid: {instruction.prompt_context.avoid_areas}

## Your Task
Working directory: {instruction.prompt_context.worktree}
Parent node: {instruction.prompt_context.parent_node_id}

Make ONE small, principled modification. Create mcgs_design_output.json when done.
Do NOT run the objective script.
```

Complete after the designer finishes:
```bash
python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json --step designer
```

### `report`

Display `instruction.summary` to the user:
- Iteration number, new node ID/name/status
- Objective value vs best-so-far
- Periodic tasks that ran this iteration

If something notable happened, add a lesson:
```bash
python {SKILL_DIR}/scripts/graph_utils.py --action add-lesson \
    --graph {REPO_DIR}/mcgs_graph.json --text "..."
```

Complete:
```bash
python {SKILL_DIR}/scripts/run_step.py complete --graph {REPO_DIR}/mcgs_graph.json --step report
```

### `iteration_complete`

One iteration is done. The output includes `should_stop` and `stop_reason` fields:
- If `should_stop` is **false**: call `run_step.py next --new-iteration` to continue automatically
- If `should_stop` is **true**: proceed to Phase 3 — read `phases/summary.md`

Stop conditions are checked automatically: `max_iterations`, `max_no_improve`, `max_time_minutes` (configured in `mcgs_graph.json` under `config`). No user interaction needed.

## Forcing a New Iteration

To explicitly start a fresh iteration (e.g., after recovering from an error):
```bash
python {SKILL_DIR}/scripts/run_step.py next --graph {REPO_DIR}/mcgs_graph.json \
    --skill-dir {SKILL_DIR} --repo-dir {REPO_DIR} --new-iteration
```
