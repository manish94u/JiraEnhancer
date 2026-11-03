# B2/B3 model-assisted pilot

This directory contains the aggregate results and posterior-update trace for a three-packet diagnostic comparing one-shot B2 with governed B3 refinement. It supplies the inputs for the learning plot and critic-reward heatmap.

## Conditions

B2 used `gpt-5.6-luna` with medium reasoning effort and one generation pass per packet.

B3 used `gpt-5.6-sol` with ultra reasoning effort. It created an initial draft and then made exactly three governed refinement passes. The diagnostic used versioned, bounded prompt-policy arms. At each pass, the selected strategy was combined with the current evidence and refinement state. Learning updated the Beta posterior used for later selection.

Nine arms were audited on 11 training packets, producing 99 critic observations. The three evaluation packets were held out from this stage. A governance and relevance filter formed a safe candidate set for each held-out packet; B3 selected the highest posterior means with exploration set to zero. The same model family performed the offline arm audit and B3 generation.

The comparison treats each packet as an evaluation unit, with two blinded model assessments per packet. The conditions differ in model, reasoning effort, and refinement depth; B3's outputs were 67% longer. The aggregate comparison therefore describes these complete configurations.

## Result

The two blinded model evaluators preferred B3 in all six paired evaluator–packet judgments. Their combined score was 187/192 for B3 and 148/192 for B2, a descriptive difference of 20.3 percentage points across the three packets.

The evaluators identified 13 missing source requirements for B2 and zero for B3, and suggested 50 follow-up prompts for B2 and 40 for B3. These descriptive counts sum both model evaluators' findings.

## Files

- [aggregate_results.json](aggregate_results.json): blinded aggregate scores and evaluator findings.
- [critic_learning_summary.json](critic_learning_summary.json): final posterior values for all nine arms.
- [exploitation_summary.json](exploitation_summary.json): held-out arm-selection sequences.
- [arm_learning_curve.csv](arm_learning_curve.csv): 99 posterior updates, with sequence, arm, reward, alpha, beta, and posterior mean.

## Reproduce the figures

From the repository root, install the dependencies with `make install-reviewer`, then run:

```bash
python3 patent/generate_b2_b3_learning_figure.py \
  --output outputs/reproduced/public_figures/b2_b3_arm_learning.png \
  --reward-output outputs/reproduced/public_figures/b2_b3_critic_reward_heatmap.png
```

The script reads the CSV, critic summary, and exploitation summary in this directory. The learning figure shows posterior means across the 99 updates and final means after 11 observations per arm. The heatmap shows rewards for the nine arms across the 11 training packets.

To regenerate these two figures together with the other public artifact figures, use:

```bash
make public-figures
```

Both commands place the pilot figures in `outputs/reproduced/public_figures/`.
