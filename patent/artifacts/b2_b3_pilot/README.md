# B2/B3 model-assisted pilot

This directory contains the release-approved records from a small held-out diagnostic. It compares one-shot B2 with a governed B3 configuration.

## Conditions

B2 used `gpt-5.6-luna` with medium reasoning effort. It made one independent generation pass for each packet. It had no critic, repair, follow-up, or revision pass.

B3 used `gpt-5.6-sol` with ultra reasoning effort. It created an initial draft and then made exactly three governed refinement passes. The diagnostic used versioned, bounded prompt-policy arms. At each pass, the selected strategy was combined with the current evidence and refinement state. Learning updated the Beta posterior used for later selection.

Nine arms were audited on 11 non-held-out packets. This produced 99 critic observations. A governance and relevance filter formed a safe candidate set for each held-out packet. B3 then selected the highest posterior means with exploration set to zero.

## Result

Three held-out packets were assessed by two blinded model evaluators. B3 was preferred in all six paired evaluator–packet judgments. The combined score was 187/192 for B3 and 148/192 for B2. The observed difference was 20.3 percentage points. No significance test was performed because the sample is small.

The evaluators identified 13 missing source requirements in B2 and none in B3. They also suggested 50 follow-up prompts for B2 and 40 for B3. These counts are sums across both evaluators. They are descriptive model judgments, not observed human effort.

## Files

- `aggregate_results.json` contains the blinded aggregate comparison.
- `critic_learning_summary.json` contains the final posterior values for all nine arms.
- `exploitation_summary.json` contains the held-out arm sequences.
- `arm_learning_curve.csv` contains a sanitized reconstruction of the 99 posterior updates. It excludes input identifiers and source text.

The figure script reads the sanitized CSV and the two public summary files. It produces the posterior curve and the critic-reward heatmap. It does not read private packets or generated text.

## Interpretation boundary

This diagnostic does not isolate the effect of learned selection. The model, reasoning effort, number of passes, and output length also differed. B3 was 67% longer. The evaluators were models rather than independent practitioners. The same model family performed the offline arm audit and B3 generation.

Source packets, generated text, evaluator narratives, private condition mapping, and detailed invocation logs are not released. The files support inspection and figure reproduction. They do not provide an end-to-end model replay.
