# Phase 3: Summary

When the loop ends (by count, user request, or convergence):

1. **Best design**: Report the best node ID, its objective, and lineage (chain of parents back to node-0)
2. **Improvement**: How much better than baseline? (percent or absolute)
3. **Search statistics**: Total nodes explored, success rate, exploration frontier
4. **Best code diff**: Show `git diff mcgs/node-0..mcgs/node-{best_id}` (the full change from baseline)
5. **Offer to apply**: Ask if the user wants to check out the best design's branch as their working code

In multi-objective mode, also report:
6. **Objective evolution**: How many objectives were generated, which ones had the most influence
7. **Consensus stability**: Did the top-ranked design stay stable across iterations?
8. **Meta-agent insights**: Summary of research phase transitions and key guidance

---

## Research Report Generation

9. **Pre-generate figures and data**: Run the report preparation script:
   ```bash
   python {SKILL_DIR}/scripts/generate_report.py \
     --graph mcgs_graph.json \
     --output-dir mcgs_report/ \
     --objectives-dir {objectives_dir}
   ```
   This creates `mcgs_report/figures/` with standard plots (convergence, DAG, node comparison,
   objective analysis) and `mcgs_report/data_summary.json` with extracted statistics.

10. **Write custom figure scripts** (if needed): Write Python scripts in `mcgs_report/` to generate
    additional figures specific to the user's research. Examples:
    - Visualizing the best design's behavior (e.g., plot learned function, compare predictions vs ground truth)
    - Comparing specific node approaches side-by-side
    - Plotting domain-specific metrics from `experiment_results`
    - Analyzing trends across design lineages

    Use the shared style: `from plot_style import setup_style, save_figure; setup_style()`
    Load data with: `from graph_utils import load_graph`
    Save to: `mcgs_report/figures/`

11. **Write the LaTeX report**: Write `mcgs_report/report.tex` from scratch as a standalone academic paper.
    You have full control over the structure — write it as a human researcher would. Guidelines:
    - **Focus on the research**, not MCGS methodology. The reader should learn about the problem,
      the discoveries, and why certain approaches work or fail.
    - Use the data from `data_summary.json`, node descriptions, lessons learned, `git diff`s between
      key designs, and your understanding of the project codebase.
    - Include the pre-generated figures where appropriate, and any custom figures you created.
    - MCGS search details (DAG, config, objective analysis) belong in an appendix if included at all.
    - LaTeX helpers are available: `from generate_report import escape_latex, build_lineage, compile_pdf`
    - Use `\usepackage{graphicx, booktabs, amsmath, hyperref, geometry, float, longtable}` as needed.

12. **Compile the report**: Run `pdflatex` twice (for cross-references), or call `compile_pdf()`.
    Tell the user where the final PDF is.

---

## Next: Feedback (Optional)

13. If you encountered issues with the meta-discovery skill during this session
    (bugs, confusing instructions, missing features, edge cases), proceed to
    Phase 4 — read `phases/feedback.md` to file structured feedback.
