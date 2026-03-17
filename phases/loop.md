# Phase 2: The MCGS Loop

Repeat for each iteration (up to the user's requested count, or until they say stop):

## Step 1: Load Current State

Read `mcgs_graph.json` and run `compute_ucb.py` to ensure scores are current.

## Step 2: Objective Agent (Multi-Objective, Periodic)

**Skip this step** in single-objective mode or if `iteration % objective_interval != 0`.

When it's time to generate a new objective, spawn a **read-only subagent** for the Objective Agent:

**Subagent prompt template:**
```
You are the Objective Agent in a Monte-Carlo Graph Search system.

{contents of references/objective_agent_guide.md}

## Research Goal
{research_goal from config}

## Existing Objectives
{for each objective: name, description, code, current weight from format_objective_table()}

## Kendall Tau Correlation Matrix
{tau matrix from latest consensus.py run — read from graph stats or recompute}

## Example Experiment Results
{experiment_results from 2-3 recent evaluated nodes, formatted as JSON}

## Meta-Agent Directions
{graph.meta_state.objective_directions, if available, else "No specific guidance yet."}

Generate a new objective function. Output:
1. The Python code in a fenced ```python block
2. A JSON metadata block with "name" and "description"
```

After the subagent returns:
1. Extract the Python code and JSON metadata from the subagent's response
2. Save the objective file to `{objectives_dir}/objective_{next_id}.py`
3. Validate using the validation script:
   ```bash
   python {SKILL_DIR}/scripts/validate_agent_output.py validate-objective \
       --file {objectives_dir}/objective_{next_id}.py \
       --sample-results '{JSON experiment_results from a recent evaluated node}' \
       --metadata '{JSON metadata extracted from the subagent response}'
   ```
4. **If validation fails** → SendMessage to the Objective Agent (1 retry max):
   - Include the specific errors: `"Your objective function has these errors: {errors}. Fix the code and output corrected Python and JSON metadata."`
   - After the agent responds, extract the updated code, overwrite the file, and re-validate
5. **If still invalid after retry** → delete the file, log the failure, and continue (don't block the loop)
6. If valid: add to `graph.objectives` via `graph.add_objective(...)` and save graph

## Step 3: Meta-Agent Analysis (Multi-Objective, Periodic)

**Skip this step** in single-objective mode or if `iteration % meta_interval != 0`.

You (the orchestrator) perform this analysis directly — no subagent needed. Read `references/meta_agent_guide.md` for the decision framework.

1. Run `consensus.py --verbose` to get the latest correlation matrix and weights
2. Review the tau matrix: which objectives agree? Which are outliers?
3. Check progress: are recent designs improving?
4. Decide:
   - **Research phase**: exploring / converging / stuck / breakthrough_needed / refining
   - **Weight multipliers**: for each objective, set a multiplier (0.0–2.0)
   - **Weight adders**: for objectives suppressed by consensus but strategically important, set an adder (0.0–1.0) to guarantee influence despite low agreement
   - **Objective directions**: strategic guidance for the next Objective Agent invocation
5. Update `graph.meta_state` with your analysis:
   - Save a snapshot to `graph.meta_state.history`
   - Update `research_phase`, `research_assessment`, `objective_directions`, `weight_adjustments`, `weight_adders`
   - Apply weights: `graph.apply_meta_weights(weight_adjustments)`
   - Apply adders: `graph.apply_meta_adders(weight_adders)`
6. Save graph
7. Recompute consensus with updated weights:
   ```bash
   python {SKILL_DIR}/scripts/consensus.py --graph {REPO_DIR}/mcgs_graph.json
   python {SKILL_DIR}/scripts/compute_ucb.py --graph {REPO_DIR}/mcgs_graph.json
   ```

## Step 4: Run the Planner

Spawn a **read-only subagent** for the Planner role. Use the Agent tool with this structure:

**Subagent prompt template:**
```
You are the Planner Agent in a Monte-Carlo Graph Search system.

{contents of references/planner_guide.md}

## Research Goal
{research_goal from config}

## Project Context
{brief description: what the project does, key source files the Designer can modify,
 what kinds of changes are most promising — architecture, loss functions, algorithms, etc.}

## Graph Summary
{output of format_graph_summary()}

## Node Table (ranked by UCB)
{output of format_node_table()}

## Lessons Learned
{one line per entry from graph.lessons_learned, or "None yet." if empty}

## Available Commands
You can run these bash commands to inspect nodes:
- `git diff mcgs/node-X..mcgs/node-Y` — see code diff between two nodes
- `git show mcgs/node-X:path/to/file` — read a file on a specific node's branch

Analyze the search history and output your decision as a JSON block.
```

Parse the planner's JSON output to extract:
- `reference_node_ids` — which nodes to build from
- `research_direction` — what to try next
- `focus_areas` / `avoid_areas`

**Validate the planner output.** Save the planner's JSON to a temp file (or parse inline), then run:
```bash
python {SKILL_DIR}/scripts/validate_agent_output.py validate-planner \
    --file /tmp/planner_output.json
```

If validation fails → **SendMessage** to the Planner agent (1 retry max):
- Include the specific errors: `"Your output has these errors: {errors}. Output corrected JSON with all required fields: current_phase, key_insights, research_direction, reference_node_ids, focus_areas, avoid_areas."`
- After the agent responds, re-validate the corrected output

If still invalid after retry → **fallback**:
- Missing `reference_node_ids`: use the top-UCB evaluated node
- Missing `research_direction`: use `"Continue exploring from top-UCB node"`
- Missing other fields: use empty lists / `"early_exploration"`

## Step 5: Prepare the Designer's Workspace

The primary parent is `reference_node_ids[0]` — the node the designer will branch from.

Create a git worktree:
```bash
git worktree add /tmp/mcgs-worktree-{new_id} mcgs/node-{parent_id}
```

## Step 6: Run the Designer

Spawn a **code-modifying subagent** for the Designer role:

**Subagent prompt template:**
```
You are the Designer Agent in a Monte-Carlo Graph Search system.

{contents of references/designer_guide.md}

## Research Goal
{research_goal}

## Project Context
{brief description of the project — what it does, key source files, how experiments are run}

## Modifiable Files
{list of files/directories the designer is allowed to change — e.g., "src/models.py, src/losses.py, src/training.py"}

## Protected Files (DO NOT MODIFY)
{list of files that must not be changed — e.g., "run_experiment.py, mcgs_graph.json, test data"}

## Lessons Learned
{one line per entry from graph.lessons_learned, or "None yet." if empty}

## Planner's Direction
{planner's research_direction}

## Reference Nodes: {reference_node_ids}
Focus areas: {focus_areas}
Avoid: {avoid_areas}

## Your Task
Your working directory is /tmp/mcgs-worktree-{new_id}, checked out to node {parent_id}'s code.

Make ONE small, principled modification following the planner's direction.

You can inspect reference nodes with:
- `git diff mcgs/node-{parent_id}..mcgs/node-{ref_id}` to see what changed in a reference
- `git show mcgs/node-{ref_id}:path/to/file` to read files from a reference node

After making your changes, create mcgs_design_output.json with your short_name, description, and reference_weights.

Do NOT run the objective script — the execution engine handles evaluation.
```

## Steps 7–11: Validate, Commit, Execute, Score, UCB (Single Script)

After the designer finishes, use `run_iteration.py` which handles all deterministic steps in one call.

**First, validate** (check for errors before committing):
```bash
python {SKILL_DIR}/scripts/run_iteration.py validate \
    --worktree /tmp/mcgs-worktree-{new_id} \
    --reference-nodes {comma_separated_reference_node_ids} \
    --protected "{comma_separated_protected_files}" \
    --parent-branch mcgs/node-{parent_id} \
    --graph {REPO_DIR}/mcgs_graph.json
```

This outputs JSON with `validation.valid`, `validation.design_errors`, and `validation.protected_violations`.

**If validation fails** → SendMessage to the Designer (1 retry max):
- For format errors: `"Fix mcgs_design_output.json: {errors}"`
- For protected file violations: `"Revert these protected files: {list}. Use: git checkout mcgs/node-{parent_id} -- {file}"`
- After the Designer responds, re-run `validate`
- If still failing: apply fallback (equal weights, revert protected files manually), then proceed

**If validation passes** (or after fallback), run the full pipeline:
```bash
python {SKILL_DIR}/scripts/run_iteration.py run \
    --worktree /tmp/mcgs-worktree-{new_id} \
    --reference-nodes {comma_separated_reference_node_ids} \
    --protected "{comma_separated_protected_files}" \
    --parent-branch mcgs/node-{parent_id} \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR} \
    --parent-edges '{reference_weights_as_json_array}' \
    --timeout {TIMEOUT}
```

This single command:
1. Validates the design output and protected files
2. Creates branch `mcgs/node-{new_id}`, commits, registers node in graph
3. Removes the designer worktree (before execute, preventing "already checked out" errors)
4. Executes the experiment
5. Scores objectives (multi-objective mode)
6. Computes consensus (multi-objective mode)
7. Updates UCB scores

Output is a JSON summary with node status, objective value, and any errors.

**If multi-fidelity is enabled**, after the pipeline completes, periodically run a promotion sweep to advance top designs to higher fidelity:
```bash
python {SKILL_DIR}/scripts/multi_fidelity.py promote-sweep \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR}
```

**Lessons learned**: After each iteration, if something notable happened (validation error, failure with instructive error, protected file violation), add a lesson:
```bash
python {SKILL_DIR}/scripts/graph_utils.py --action add-lesson \
    --graph {REPO_DIR}/mcgs_graph.json \
    --text "run_experiment.py is protected — modify experiment_config.yaml instead"
```

## Step 11.5: Hyperparameter Optimization (Optional, Periodic)

Every `hpo_interval` iterations (default: 10), or when the user requests it, run HPO on the best untuned design:
```bash
python {SKILL_DIR}/scripts/hpo_tune.py \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR} \
    --auto \
    --max-iter {HPO_MAX_ITER} \
    --backend {HPO_BACKEND}
```

Or target a specific node:
```bash
python {SKILL_DIR}/scripts/hpo_tune.py \
    --graph {REPO_DIR}/mcgs_graph.json \
    --node-id {node_id} \
    --repo-dir {REPO_DIR} \
    --max-iter 50 \
    --register
```

HPO creates a new node with tuned hyperparameters (same architecture, better params). The Designer should focus on architecture/algorithm changes and leave hyperparameter tuning to HPO.

## Step 12: Report Progress

After each iteration, tell the user:
- **Iteration N/M**
- **New node**: ID, name, description
- **Objective**: value (and comparison to parent and best-so-far)
- **Status**: improved / regressed / failed
- **Best overall**: node ID, objective value

In multi-objective mode, also report:
- Per-objective scores for the new node
- Consensus score
- Any meta-agent analysis performed this iteration
- Any new objectives generated this iteration

If the new design improved on the best, celebrate briefly. If it regressed, note it as useful information for future iterations.

## Step 13: Continue or Stop

Continue the loop unless:
- Reached max iterations
- User says stop
- You notice the search has converged (last K iterations show no improvement) — suggest stopping and summarize

When done, proceed to Phase 3 — read `phases/summary.md`.
