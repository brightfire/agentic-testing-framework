## Eval Results: <skill-name> — <dataset-name>

### Variants
| Variant | Composite | <dim-1> | <dim-2> | <dim-3> | Pass% |
|---------|-----------|---------|---------|---------|-------|
| <label-a> | <avg> | <avg> | <avg> | <avg> | <pass%> |
| <label-b> | <avg> | <avg> | <avg> | <avg> | <pass%> |
| Delta | <+/-delta> | <+/-delta> | <+/-delta> | <+/-delta> | |

### Per-Item Deltas
| Item | <label-a> | <label-b> | Delta | Notes |
|------|-----------|-----------|-------|-------|
| <item-id> | <avg> | <avg> | <+/-delta> | ⚠️ Regression in <dimension> |

### Verdict: <Improvement | Regression | Neutral>

<Explanation>

### Improvement Suggestions
- **<item-id>** (<dimension>): <suggestion>
  - Related change: <diff reference>

**Pass% values come from `composite.pass_rate` in the `eval_report.py --json` output. Do not compute manually.**
