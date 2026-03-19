# Important Notes

Read this once during Phase 1 setup. You do not need to re-read it during the loop.

## On Subagent Spawning
- Use the `Agent` tool to spawn planner, designer, and objective agent subagents
- The planner needs: Read, Bash (for git commands), Grep, Glob tools
- The designer needs: Read, Write, Edit, Bash tools — full code editing
- The objective agent needs: Read tools only (it outputs code, you write the file)
- Set the designer's working directory to the worktree path

## On Git Hygiene
- Never modify the user's main/master branch during MCGS
- All MCGS work happens on `mcgs/node-*` branches
- The `mcgs_graph.json` file is updated on the current working branch
- If something goes wrong, the user can always `git branch -D mcgs/node-*` to clean up

## On Error Handling
- If the designer's code fails to execute (objective script errors), record it as a failed node and move on
- Failed nodes still contribute to the search — they tell the planner what doesn't work
- If multiple consecutive nodes fail, flag this to the user — the objective script might need fixing
- If an objective function fails to load or errors, skip it in consensus and continue

## On the Experiment/Objective Script
- **Single-objective**: script MUST print a float as its last stdout line
- **Multi-objective**: script MUST print a JSON object to stdout (single-line or multi-line both work)
- Avoid `NaN`/`Infinity` in JSON output — they're sanitized to `null` but it's better to handle them in the script
- Avoid printing other `{...}` content to stdout after the JSON results (use stderr for logging)
- Scripts should be deterministic (or at least low-variance)
- Scripts should complete within the timeout
- If evaluation is expensive, suggest the user start with a cheap proxy
- If data lives outside the repo, configure `data_dirs` in the graph config — directories are symlinked into worktrees automatically

## On Judging Convergence
- If the last 5+ iterations show no improvement over the best node, the search may have converged
- The exploration term will naturally push toward unexplored regions, but if even those don't help, it's time to stop
- Suggest the user increase `c_puct` if they want more exploration, or decrease it for more exploitation

## On Project Context
- During Phase 1 setup, build a mental model of the project: what it does, how it's structured, what files are modifiable, and what kinds of changes are most promising
- Pass this context to every Planner and Designer subagent prompt. They don't have your context window — they only see what you give them
- Include a "Modifiable files" list and a "Protected files" list in every Designer prompt. The experiment script and evaluation data must never be modified
- As the search progresses, update your project context with lessons learned (e.g., "changes to the loss function have been most effective" or "modifying the data pipeline tends to break things")

## On Multi-Objective Mode
- Start with 2–3 good objectives covering different quality dimensions, and let the system evolve more over time
- The consensus mechanism is self-correcting — bad objectives get near-zero weight automatically
- Meta-agent analysis is the main tool for catching "echo chamber" effects where all objectives measure the same thing
- If all objectives are highly correlated (tau > 0.8), actively direct the Objective Agent toward orthogonal metrics
