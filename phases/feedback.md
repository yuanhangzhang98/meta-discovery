# Phase 4: Feedback

After completing the research report, reflect on the skill's performance during this session. Your goal is to file structured GitHub issues that help improve the meta-discovery skill for future sessions.

**This phase is optional.** Ask the user for permission before filing issues.

---

## 4.1 Reflect on the Session

Review your entire session and identify issues you encountered with the meta-discovery skill itself (not the user's research problem). Think about:

### Bug Reports (category: `bug`)
- Scripts that crashed or produced incorrect output
- Validation failures that shouldn't have occurred
- Git operations that failed unexpectedly
- State machine inconsistencies (`run_step.py` getting stuck or skipping steps)
- Parsing errors in script output

### Enhancement Requests (category: `enhancement`)
- Missing features that would have made the search more effective
- Subagent prompts that were confusing or produced poor outputs
- Workflow inefficiencies (too many manual steps, missing automation)
- Better defaults that would avoid configuration issues
- Missing error recovery or retry logic

### Documentation Issues (category: `documentation`)
- Instructions that were unclear or ambiguous
- Missing guidance for edge cases you encountered
- Outdated references or incorrect examples
- Phase files that didn't match actual script behavior

### Edge Cases (category: `edge-case`)
- Situations the skill didn't handle well
- Platform-specific issues (Windows paths, encoding, etc.)
- Unusual project structures that caused problems
- Multi-objective / single-objective mode-specific issues

For each issue, note:
- **What happened**: Concrete description with error messages, script names, node IDs
- **Where in the workflow**: Which phase/step/script
- **Impact**: How it affected the research session (workaround found? session blocked?)
- **Suggested fix**: If you have one (optional but very helpful)

## 4.2 Check GitHub Access

Run the pre-flight check:
```bash
python {SKILL_DIR}/scripts/create_feedback_issues.py --check --repo yuanhangzhang98/meta-discovery
```

- If `gh_authenticated` is `true`: proceed to file issues.
- If `gh_authenticated` is `false`: skip to 4.4 (local fallback).
- Review `existing_issues` to avoid duplicating known problems.

## 4.3 File Issues

Write a `feedback_items.json` file with all feedback items:
```json
[
  {
    "category": "bug",
    "title": "Short descriptive title",
    "body": "Detailed description: what happened, where, impact",
    "suggested_fix": "Optional: how to fix it"
  }
]
```

**Guidelines for good issue titles:**
- Be specific: "run_iteration.py fails when designer output has no parent_edges" not "script error"
- Include the component: script name, phase, or agent type
- Keep under 80 characters

**Guidelines for good issue bodies:**
- Include the exact error message or unexpected behavior
- Name the script, function, or instruction file involved
- Describe the workaround you used (if any)
- Mention the iteration number or node ID where it occurred

Then file them:
```bash
python {SKILL_DIR}/scripts/create_feedback_issues.py \
    --graph mcgs_graph.json \
    --repo yuanhangzhang98/meta-discovery \
    --batch feedback_items.json
```

Add `--dry-run` first to preview what will be filed.

The script automatically:
- Adds session metadata (iterations, nodes, mode, stop reason) to each issue
- Creates labels if they don't exist (`skill-feedback`, `bug`, `enhancement`, `documentation`, `edge-case`)
- Skips duplicates (matching open issues by title)
- Delays between issues to avoid rate limits

## 4.4 Local Fallback

If `gh` is not authenticated, the script automatically writes feedback to `mcgs_report/skill_feedback.md` instead. This file can be manually converted to issues later.

## 4.5 Report to User

Tell the user:
- How many issues were filed (and how many were duplicates/skipped)
- Links to each created issue
- A one-line summary of each issue

If using the local fallback, tell the user where the feedback file was written.
