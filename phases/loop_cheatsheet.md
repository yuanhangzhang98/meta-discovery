# Loop Cheat Sheet

Re-read this if you've lost track of the dispatch loop.

## The Dispatch Loop

```
while not done:
    instruction = python {SKILL_DIR}/scripts/run_step.py next \
        --graph {REPO_DIR}/mcgs_graph.json --skill-dir {SKILL_DIR} --repo-dir {REPO_DIR}

    # Read instruction.instructions — it tells you exactly what to do
    # Execute the action described in instructions

    python {SKILL_DIR}/scripts/run_step.py complete \
        --graph {REPO_DIR}/mcgs_graph.json --step {instruction.step} --result '{json}'
```

## Action Quick Reference

| Action | What to do |
|--------|-----------|
| `spawn_objective_agent` | Spawn READ-ONLY subagent with `prompt_context`, save objective file, validate, complete |
| `run_meta_analysis` | YOU analyze directly (no subagent), run consensus commands, update meta_state, complete |
| `spawn_planner` | Spawn READ-ONLY subagent with `prompt_context`, validate JSON output, complete with result |
| `run_command` | Execute `instruction.command`, complete with output |
| `spawn_designer` | Spawn CODE-MODIFYING subagent in worktree, complete |
| `report` | Display `instruction.summary` to user, complete |
| `iteration_complete` | If `should_stop`: proceed to Phase 3 (`phases/summary.md`). Otherwise: call `next --new-iteration` |

## Critical Rules

- **NEVER skip `run_step.py`** — it tracks state and triggers periodic tasks
- **ALWAYS call `complete`** after each step before calling `next` again
- **Follow `instruction.instructions`** — they contain everything you need
- Subagent prompts: use `prompt_context` fields — the guide, research_goal, graph data are all there
- Validation: use `validate_agent_output.py`, 1 retry on failure, then fallback
