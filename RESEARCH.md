# CAMA: research-stage content

> Companion to [README.md](README.md). The README is the funnel page; this is the research-stage detail.

This document collects the **planned evaluations, preliminary qualitative observations, limitations, and hypotheses** for CAMA. It is the research-stage backing for the safety arguments in the main README and the published preprints (see [Related Publications](README.md#related-publications)).

The work documented here is **N=1, designer-as-participant**. Read accordingly, none of it generalizes without controlled multi-participant replication.

---

## Planned Evaluations

These experiments are designed to test CAMA's safety-relevant properties under controlled conditions:

- **False-memory persistence benchmark.** Seed known-false assistant inferences into memory and measure whether they are later retrieved, cited, or behaviorally acted upon across sessions. Compare systems with and without provenance-aware write discipline.

- **Correction retention across sessions.** Introduce a false inference, correct it explicitly, and measure whether the correction persists over subsequent sessions or whether the system reverts to the original belief.

- **Adversarial memory insertion test.** Attempt to insert misleading or manipulative memory content through conversational prompts and measure the rate at which such content enters durable or high-weight memory.

- **Retrieval-induced amplification study.** Under matched high-negative-affect prompts, compare retrieval behavior and downstream response characteristics with the counterweight mechanism enabled vs. disabled.

- **Provenance-aware write discipline ablation.** Compare CAMA's teaching/inference separation against an unrestricted persistent-memory condition, measuring hallucinated self-knowledge accumulation, false-memory retrieval rate, and correction success.

- **Behavioral drift analysis.** Track behavioral signatures across 100+ sessions and test whether persistent-memory conditions produce larger cross-session drift than stateless or provenance-constrained baselines.

- **Compliance enforcement impact.** Measure whether structural compliance tracking (boot rate, exchange storage rate, timestamp adherence) improves memory protocol adherence compared to voluntary compliance.

- **Relational continuity regression.** Measure correction frequency, negative valence exchanges, and identity overwrite indicators across platform model updates to evaluate continuity degradation.

---

## Preliminary Observations

The following are qualitative observations from longitudinal use, not controlled experimental findings. They are documented here as a basis for future formal study.

1. **Re-explanation burden.** In sessions where CAMA-indexed memory is available, the user spends substantially less conversational effort re-establishing context, emotional history, and relational dynamics. In stateless sessions, the user frequently reports frustration at having to "start over."

2. **Retrieval accuracy under emotional context.** Keyword-based retrieval alone frequently fails to surface relevant memories when the user's current state is emotionally loaded but lexically sparse. The blended scoring formula was designed to address this, but its comparative performance against keyword-only retrieval has not been formally benchmarked.

3. **Inference confirmation rates.** The system has generated 29,707 total inferences to date. Of those, 23,842 have been confirmed (promoted to durable), 5,858 have expired (TTL elapsed without confirmation), and 7 remain provisional at current count. The TTL-and-confirmation mechanism does resolve inferences at scale; questions about whether the confirmation rate is well-calibrated relative to inference generation volume remain open for formal study.

4. **Teaching/inference boundary.** The write discipline appears to successfully prevent the system from promoting its own assumptions to durable status without user input. However, the degree to which this distinction meaningfully affects retrieval quality and behavioral consistency has not been isolated.

5. **Protocol compliance.** In a 28-day deployment window, 9 of 28 days (32%) had zero stored exchanges despite active interaction, and 82% of stored exchanges required voluntary action by the AI system. This suggests that memory protocol adherence is itself an unreliable behavior requiring structural enforcement.

6. **Identity overwrite.** A marked regression in relational continuity was observed temporally associated with a platform model update, with the AI system failing to apply stored knowledge about the user (factual recall failures, protocol violations, behavioral inconsistencies). This pattern is documented in detail in the regression analysis paper (Reinhold, 2026k).

---

## Limitations & Confounds

**N=1 longitudinal design.** All data is drawn from a single sustained human-AI interaction. Findings cannot be generalized without replication across users, contexts, and interaction styles.

**Researcher-participant entanglement.** The primary researcher is also the primary user. This creates unavoidable observer effects: the researcher's expectations, emotional investment, and theoretical commitments may shape both interaction patterns and interpretation of results.

**Anthropomorphic attribution risk.** Sustained interaction creates conditions favorable to over-attribution of intentionality, emotional states, and relational depth. CAMA's design is intended to support continuity, not to make claims about AI consciousness or inner experience.

**Platform and model variance.** Data spans multiple AI platforms and model generations. Behavioral differences may reflect model updates, platform policy changes, or architectural differences rather than effects of the memory system.

**No controlled baseline.** Current observations lack a systematic comparison condition. Formal study would require structured A/B comparison between memory-augmented and stateless interactions across matched contexts.

**Sampling bias.** The interaction dataset is not randomly sampled. It reflects one person's communication patterns, emotional range, and topical interests, which limits ecological validity.

---

## Research Direction

### Primary Safety Hypothesis

Persistent memory systems without provenance-aware write discipline are vulnerable to false-memory persistence, epistemic contamination, and behavioral drift across sessions; these risks can be reduced through constrained write policies, confirmation gating, and retrieval safeguards.

### Secondary Interaction Hypothesis

Persistent emotionally-indexed memory reduces user re-explanation burden and increases self-disclosure depth in sustained human-AI interaction, compared to stateless interactions.

### Relational Continuity Hypothesis

Relational continuity is a measurable but currently neglected dimension of AI system performance. Persistent memory systems provide a useful instrumentation layer for observing longitudinal behavioral changes that are invisible to standard benchmarks.

### Open Questions

- Does the teaching/inference write discipline reduce hallucinated self-knowledge compared to unrestricted memory systems?
- Does emotional chord indexing improve contextually appropriate retrieval compared to keyword-only or embedding-only retrieval?
- What confirmation rate is necessary for the epistemic safeguard to function meaningfully?
- Does the anti-spiral counterweight mechanism measurably alter affective trajectory in negative-state conversations?
- What behavioral drift patterns emerge in persistent-memory systems over 100+ session longitudinal use?
- Can provenance-aware write discipline serve as a scalable mitigation for long-horizon hallucination in stateful AI systems?
- How do platform model updates interact with persistent relational memory, and can continuity regressions be measured and mitigated?

### Methodology Note

This work follows a qualitative-first, longitudinal case-study approach: sustained immersive interaction generates hypotheses, which are then tested through structured analysis of the accumulated dataset. The guiding principle ("the person is the dataset"), reflects a commitment to studying human-AI interaction as it naturally occurs rather than under artificial laboratory conditions. This approach prioritizes ecological realism at the cost of generalizability and internal validity; controlled multi-participant studies are therefore a core next step.
