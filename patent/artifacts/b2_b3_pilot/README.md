# B2/B3 model-assisted pilot

This directory contains aggregate outputs from a small held-out diagnostic. B2 used one model call. B3 used a learned, governance-filtered three-pass workflow. Three held-out inputs were assessed by two blind model evaluators.

The aggregate result favored B3 in all six paired evaluator–input judgments. The combined score was 187/192 for B3 and 148/192 for B2. No significance test was performed because the sample is too small.

This diagnostic does not isolate the effect of reinforcement learning. The base model, reasoning effort, number of passes, and output length also differed. The evaluators were models rather than independent practitioners. The same model family performed the offline arm audit and B3 generation. These factors can favor B3.

`critic_learning_summary.json` records the 99 non-held-out arm audits and learned posterior summaries. `aggregate_results.json` records the blinded aggregate comparison. Source packets, generated text, evaluator narratives, private condition mapping, and model invocation logs are not released because they contain restricted technical material or detailed model traces.

The files support inspection of the archived diagnostic only. They do not provide an end-to-end model replay.
