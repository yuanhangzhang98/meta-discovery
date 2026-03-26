# Spawn Planner Agent

{description}

## Steps

1. Spawn a **READ-ONLY** subagent (Agent tool; needs Read, Bash for git, Grep, Glob) with this prompt:

```
You are the Planner Agent in a Monte-Carlo Graph Search system.

{guide}

## Research Goal
{research_goal}

## Project Context
<Describe the project: key source files, modifiable areas, promising change directions>

## Graph Summary
{graph_summary}

## Node Table (ranked by UCB)
{node_table}

## Lessons Learned
{lessons_learned}

## Available Commands
- `git diff mcgs/node-X..mcgs/node-Y` — see code diff between two nodes
- `git show mcgs/node-X:path/to/file` — read a file on a specific node's branch

Analyze the search history and output your decision as a JSON block.
```

2. Parse the planner's JSON output.

3. Validate:
```bash
python {scripts_dir}/validate_agent_output.py validate-planner --file /tmp/planner_output.json
```

4. If invalid: SendMessage to subagent (1 retry), then fallback to top-UCB node + generic direction.

5. Complete with the planner's JSON as the result:
```bash
{complete_command_with_result}
```

**LIGHTWEIGHT MODE**: If the strategy is obvious (e.g., parameter sweep), skip the subagent. Construct the planner JSON yourself and pass it directly to complete.
