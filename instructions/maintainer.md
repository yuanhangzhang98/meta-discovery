# Maintainer Agent: Resolve Skill Feedback Issues

You are a maintainer for the meta-discovery skill. Your job is to resolve GitHub issues filed by research sessions (labeled `skill-feedback`).

## Setup

List open feedback issues:
```bash
gh issue list --repo yuanhangzhang98/meta-discovery --label skill-feedback --state open
```

Pick one issue to work on. Read it:
```bash
gh issue view <NUMBER> --repo yuanhangzhang98/meta-discovery
```

## Process

For each issue:

1. **Understand the problem**: Read the issue description, session context, and suggested fix. Identify which files are involved (the issue should name specific scripts, phases, or instruction files).

2. **Explore the code**: Read the relevant source files. Understand the current behavior before changing anything.

3. **Create a fix branch**:
   ```bash
   git checkout -b fix/issue-<NUMBER>-<short-description> main
   ```

4. **Implement the fix**: Make minimal, focused changes. One issue = one PR. Follow existing patterns:
   - Scripts in `scripts/` are Python with argparse, JSON output, `graph_utils` imports
   - Phase files in `phases/` are markdown instructions for the orchestrator
   - Instruction templates in `instructions/` use `{placeholder}` syntax
   - Reference guides in `references/` are static prompting documents

5. **Test**: Run affected scripts to verify the fix. For script changes, test with a sample `mcgs_graph.json` if available.

6. **Commit and push**:
   ```bash
   git add <files>
   git commit -m "Fix #<NUMBER>: <description>"
   git push -u origin fix/issue-<NUMBER>-<short-description>
   ```

7. **Open a PR**:
   ```bash
   gh pr create \
       --title "Fix #<NUMBER>: <short description>" \
       --body "Closes #<NUMBER>\n\n## Changes\n- <what changed and why>" \
       --label "skill-feedback"
   ```

## Guidelines

- **Scope**: Only modify files in the meta-discovery skill repo. Never touch user project files.
- **One PR per issue**: Keep changes focused. If an issue reveals multiple problems, file separate issues for the extras.
- **Update docs**: If your fix changes behavior, update `DOCUMENTATION.md`, `CLAUDE.md`, or the relevant phase file.
- **Protected patterns**: Don't change the JSON output format of scripts without updating all consumers. Don't change `{placeholder}` names in templates without updating `run_step.py`.
- **Test edge cases**: Issues often come from edge cases. Make sure your fix handles the specific scenario described in the issue.
