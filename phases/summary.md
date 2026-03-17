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
