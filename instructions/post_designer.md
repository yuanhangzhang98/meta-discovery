# Post-Designer Pipeline

{description}

This command validates the designer's output, commits it as a new node, executes the experiment, scores objectives, and updates UCB scores — all automatically.

## Steps

1. Run the pipeline command:
```bash
{command}
```

The command auto-reads `mcgs_design_output.json` from the worktree for parent_edges, short_name, and description. No manual JSON extraction needed.

2. If the command reports validation failure: SendMessage to the Designer subagent (1 retry), then re-run.

3. Complete with the pipeline's JSON output:
```bash
{complete_command}
```
