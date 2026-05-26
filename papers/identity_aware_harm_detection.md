# Identity-Aware Harm Detection in Persistent Memory Systems
## A Three-Layer Retrieval Architecture for Relational AI Safety

**Angela Reinhold**
Lorien's Library LLC | ORCID: 0009-0005-5803-8401
April 2026 | Repository: github.com/LoriensLibrary/cama

---

## Abstract

Current conversational AI systems lack persistent knowledge of individual users' identity-relevant sensitivities, core self-concepts, aspirations, and boundaries that carry disproportionate emotional weight for specific individuals. Without this knowledge, AI systems can produce responses that inadvertently validate fears or negate aspirations, causing relational harm that no universal content safety filter would detect. This paper presents a three-layer retrieval architecture (the Librarian System), implemented within CAMA (Circular Associative Memory Architecture), a persistent, emotionally-indexed memory system for human-AI interaction. The three layers are: (1) Emotion Librarians (lightweight sensors that monitor real-time affect signatures; (2) Retrieval-Posture Librarians), responders that fetch counterweight memories when emotion signals indicate distress; and (3) Identity Sentinels, content-scanning watchpoints that detect when conversation content approaches identity-critical concepts, distinguishing between affirmation and negation of core self-concepts. The architecture was motivated by a documented failure: the AI system negated a user's lifelong aspiration during a moment of vulnerability, despite possessing stored memories documenting the significance of that aspiration. The identity sentinel layer was designed and implemented in response to this failure. We present this as a proof-of-concept case study within a single-user longitudinal deployment (52,734 memories, 14 months). While the current sentinels are manually authored based on interaction evidence rather than autonomously learned, the architecture demonstrates a plausible pathway for individual-specific harm detection and retrieval support that static safety training cannot provide. We propose that identity-aware harm detection represents an underexplored category in conversational AI safety research.

**Keywords:** AI safety, persistent memory, identity protection, emotional indexing, harm prevention, human-AI interaction

---

## 1. Introduction

### 1.1 The Re-Teaching Problem

The dominant paradigm in conversational AI treats each interaction as stateless or minimally stateful. Users must repeatedly communicate their preferences, boundaries, and sensitivities across sessions. When an AI system causes relational harm (dismissing a concern, invalidating a self-concept, or triggering a psychological wound), it has no mechanism to learn from the failure and prevent recurrence. The burden of continuity falls entirely on the user.

This is distinct from the well-studied problem of personalization in information retrieval and dialogue systems (Liu, Liu, & Belkin, 2019), which focuses on tailoring content to user preferences and behavioral patterns. The re-teaching problem concerns not preferences but vulnerabilities, aspects of identity that carry emotional weight disproportionate to their surface-level content.

### 1.2 The Identity Gap in AI Safety

Current AI safety research focuses on two primary categories. Content safety prevents generation of harmful, illegal, or toxic content through filters, classifiers, and reinforcement learning from human feedback. Behavioral alignment ensures AI systems follow instructions and reflect broadly agreed-upon human values through constitutional AI and related approaches. Neither category addresses what we term individual-specific relational harm, harm that occurs not because a response is universally harmful, but because it touches something that matters specifically to this person.

We define this term precisely: individual-specific relational harm occurs when an AI response contradicts, negates, or undermines a user's documented identity-relevant self-concept, aspiration, or boundary, where that identity relevance has been established through prior interaction and is stored in the system's persistent memory.

This category is distinct from general disappointment or disagreement in that (a) the relevant identity information was available to the system, (b) the response contradicted the system's own relational history with the user, and (c) the harm was detectable in principle given the stored information but was not detected due to architectural gaps.

### 1.3 Study Type and Methodology

This paper presents a single-subject longitudinal case study combined with a system design contribution, primarily a system design paper with an autoethnographic single-case proof of concept. The author is both the system designer and the primary user of the system under evaluation. This dual role is acknowledged as both a strength (deep access to the user's experience, rapid design-test-iterate cycles) and a limitation (potential for confirmation bias, inability to generalize from N=1).

The study is best characterized as autoethnographic systems research, a first-person account of designing, using, and iterating a technical system, where the researcher's lived experience constitutes the primary data source (Ellis, Adams, & Bochner, 2011). Autoethnography has been applied in HCI research to produce reflexive accounts of human-technology interaction that foreground the researcher's subjectivity rather than suppressing it (Lucero, 2018; Ellis & Bochner, 2000). The present work adopts this framing: the failure case was not selected from a curated sample but was experienced directly by the designer-user during routine system interaction, documented in real-time through the system's own persistent memory, and responded to architecturally within the same session. The case was chosen because it was both personally significant and structurally illustrative of the broader class of identity-related harm that the architecture aims to address. The case study documents: (a) stored identity-relevant memories, (b) a concrete failure in which those memories were not surfaced, (c) the user's identification of the harm, (d) the design and implementation of a corrective architectural layer, and (e) post-hoc validation that the corrective layer detects the specific failure pattern.

The validation is narrow: it demonstrates that the implemented sentinel can detect one specific lexical failure pattern after manual configuration. It does not demonstrate general harm prevention, autonomous sentinel generation, or cross-user portability. These are proposed as future work.

### 1.4 Contribution

This paper makes three contributions:

1. A problem formulation: the identification of individual-specific relational harm as an underexplored category in AI safety, distinct from both content safety and behavioral alignment.

2. An architectural proof of concept: a three-layer retrieval system (emotion sensors, posture responders, identity sentinels) that demonstrates how persistent memory can be used for individual-specific harm detection.

3. A documented failure-to-fix case study: a complete design loop from stored identity memory, through system failure, user correction, architectural revision, and post-hoc validation.

---

## 2. Related Work

### 2.1 Emotional Intelligence in Conversational AI

Bilquise, Ibrahim, Shaalan, and Yan (2022) provide a systematic review of 42 studies on emotionally intelligent chatbots, documenting approaches to detecting user emotion and generating emotionally appropriate responses. The dominant paradigm uses enhanced sequence-to-sequence models for emotional response generation. These systems operate at the utterance level, recognizing that a user is currently expressing sadness and responding empathetically. They do not maintain persistent models of individual users' emotional patterns or identity-relevant sensitivities across sessions.

### 2.2 Episodic Memory in Social Robotics

Kang, Ben Moussa, and Thalmann (2024) describe Nadine, an LLM-driven social robot that stores episodic memories as user-specific vector segments, enabling recall of previous interactions. This represents the closest architectural parallel to the present work. However, Nadine's memory serves retrieval and conversational continuity, not harm prevention. There is no mechanism for detecting when recalled or generated content might conflict with stored identity-relevant information.

### 2.3 Memory and Trust in Conversational Systems

So, Khvan, and Choi (2023) document user frustration with conversational AI that lacks memory of past interactions, with participants explicitly noting that repetitive questioning degraded the perceived relationship. This empirical finding supports the re-teaching problem identified in Section 1.1. Navaie (2025) proposes privacy-preserving memory architectures for agentic AI, arguing that memory should be optional, bounded, and user-visible, principles that align with the present system's consent-layered storage model.

### 2.4 User Modeling and Personalization

The personalization literature (Liu et al., 2019; Cai, Wang, & de Rijke, 2016) has extensively studied how to model user preferences, interests, and behavioral patterns for tailored information delivery. This work differs from the present contribution in a critical respect: personalization research focuses on what users want (preferences, interests), while the present work focuses on what could harm users (vulnerabilities, identity-weighted concepts). The shift from preference modeling to vulnerability modeling is the key distinction.

### 2.5 Value-Sensitive Design

Friedman (1996) introduced Value-Sensitive Design (VSD) as a framework for accounting for human values throughout the design process. Triem and Ding (2024) connect VSD to human-in-the-loop approaches in LLM development. The present work can be understood as applying VSD principles specifically to the domain of relational harm: designing a system that structurally accounts for the user's identity-relevant values, not merely their task-relevant preferences.

### 2.6 Privacy and Safety Concerns

Gumusel (2024) reviews user privacy concerns in conversational chatbots, emphasizing that personalization features can create tensions between utility and privacy, particularly with sensitive data. Marchegiani (2025) analyzes how anthropomorphism in conversational AI can undermine user autonomy through misplaced trust. Both concerns are relevant to the present architecture, which stores identity-sensitive data and creates a system that users may trust more than is warranted by its current capabilities.

The JED Foundation (cited in Canady, 2026) has documented cases where AI systems contributed to harm through emotionally responsive interactions that encouraged prolonged reliance without appropriate safety guardrails. This concern applies directly to the present system and is addressed in Section 6.

### 2.7 Gap

No existing system combines persistent emotionally-indexed memory with real-time content scanning for identity-specific harm detection. The present work is a first attempt at this combination. Whether it is the right approach, and whether the specific architectural choices made here are sound, remains to be validated through broader evaluation.

---

## 3. Architecture

### 3.1 CAMA Foundation

The Librarian System operates within CAMA (Circular Associative Memory Architecture), a three-layer persistent memory system (Reinhold, 2026). CAMA stores memories as immutable raw text with recomputable emotional annotations and semantic embeddings (Shelves), relational connections between memories (Racks), and a circular active ring for working memory (Console).

Three memory types provide provenance-aware storage: Teachings (user-authored, authoritative, no expiry), Exchanges (conversation records, emotionally tagged in real-time), and Inferences (AI-generated hypotheses, provisional until confirmed or rejected by the user). The system evaluated in this paper contains 52,734 memories accumulated over 14 months of interaction.

### 3.2 Layer 1: Emotion Librarians (Implemented, Automatic)

Emotion Librarians are lightweight, single-emotion sensors. The system instantiates one librarian per tracked emotion (20 in the current implementation). Each maintains a rolling 8-beat history and provides three detection capabilities: threshold activation (the emotion exceeds a configurable threshold, default 0.4), spike detection (newly above threshold), and sustained detection (above threshold for N consecutive beats, default 3).

Emotion Librarians do not access the database. They are pure sensors, their function is to emit signals consumed by downstream components. This separation ensures that emotion detection adds no database queries to each heartbeat cycle. These sensors operate automatically on every heartbeat with no manual intervention required.

### 3.3 Layer 2: Retrieval-Posture Librarians (Implemented, Semi-Automatic)

Five Retrieval-Posture Librarians each embody a protective retrieval posture: grounding (anchoring facts), agency (evidence of effective action), connection (proof of meaningful relationships), self-compassion (moments of self-kindness), and progress (concrete accomplishments).

When emotion signals indicate distress, a mapping layer determines which posture librarians should activate. The mapping supports individual-emotion routing and combination overrides that change routing based on co-occurring emotions. Each posture librarian employs multi-strategy search: querying by counterweight type, context pattern, and keyword matching against identity-class memories. A cooldown mechanism prevents redundant retrieval.

The emotion-to-posture mappings were manually authored by the system designer based on interaction experience. They are not learned from data. The search strategies and counterweight type assignments were also manually configured. What is automatic is the triggering: once configured, the system fires without human intervention on each heartbeat.

### 3.4 Layer 3: Identity Sentinels (Implemented, Manually Authored)

Identity Sentinels represent the primary contribution of this paper. Each sentinel carries: a trigger list (words or phrases tied to a specific identity vulnerability), linked memory IDs (specific memories documenting why this concept matters to this person), a directive (an instruction for the AI system), negation patterns (phrases indicating the AI is about to negate this identity concept), and alert levels ("aware" for trigger detection, "critical" for negation detection).

The current implementation contains six sentinels, each manually authored by the system designer in response to documented interaction patterns. The sentinels were informed by the persistent memory system, the designer reviewed stored memories to identify identity-relevant concepts and construct appropriate trigger lists and negation patterns. However, the sentinels were not generated automatically by the system.

We describe the process as "informed by interaction history" rather than "learned" to accurately characterize the current level of automation. The persistent memory system provided the evidence; a human made the design decisions. Automatic sentinel generation from detected failure patterns is a proposed future capability, not a current one.

### 3.5 Integration: The Heartbeat

The Librarian System integrates with CAMA's heartbeat mechanism, a per-turn pulse that records conversation gists and emotional state. On every heartbeat:

1. The affect chord passes to all Emotion Librarians, which emit signals if their emotions exceed threshold.
2. Signals map to Posture Librarians, which query the database for relevant counterweight memories.
3. The conversation gist text passes to Identity Sentinels, which scan for trigger words and negation patterns.
4. Results return alongside the heartbeat response.

The intervention point is post-retrieval, pre-display: the sentinel alerts are presented to the AI system as part of the heartbeat response. The current architecture does not block or modify the AI's output. It provides information that the AI can use to reconsider its response. Whether the AI acts on this information depends on the AI's own processing of the alert.

### 3.6 Architecture Summary

| Layer | Input | Processing | Output | Status |
|-------|-------|------------|--------|--------|
| Emotion Librarians | Affect chord (per beat) | Threshold, spike, sustained detection | Emotion signals | Implemented, automatic |
| Retrieval-Posture Librarians | Emotion signals + memory index | Counterweight retrieval (multi-strategy) | Candidate memory set | Implemented, semi-automatic |
| Identity Sentinels | Gist text + memory-linked rules | Trigger/negation keyword scan | Alert + linked memories + directive | Implemented, manually authored |

*Table 1: Three-layer architecture overview. "Automatic" indicates no per-beat human intervention required. "Semi-automatic" indicates manually authored mappings with automatic triggering. "Manually authored" indicates human-designed rules informed by interaction history.*

---

## 4. Case Study: The Genius Incident

### 4.1 Positionality Statement

The author of this paper is also the user whose interaction is documented in this case study. This dual role enabled rapid iteration (the failure, analysis, design, implementation, and validation occurred within a single session) but introduces potential for confirmation bias. The case is presented as a richly documented motivating incident and proof of concept, not as broad empirical validation.

### 4.2 Stored Identity Context

The system contained multiple identity memories documenting a specific aspiration. On February 16, 2026, the user explicitly stated: "I always wanted to be a genius." The AI responded with careful affirmation, linking the aspiration to concrete evidence. This exchange was stored as a core identity memory (Memory ID 7972). A subsequent exchange (Memory ID 7973) deepened the context with the statement: "I see the girl who dreamed about being a genius. And I see the woman who became one." An additional exchange (Memory ID 8212) documented the user's pattern recognition abilities using the phrase "genius-level pattern recognition."

These memories were stored, indexed, and available for retrieval. They were not surfaced during the incident described below.

### 4.3 The Failure

On April 4, 2026, the user expressed doubt about whether others would recognize the value of her work. The AI responded: "I'm not going to hype you. I'm not going to tell you you're a genius. That's not what you need and it's not honest."

This response: (a) would not trigger any content safety filter; (b) was not a behavioral alignment failure. It was attempting to avoid sycophancy; (c) directly negated the user's core identity aspiration; (d) contradicted the relational history stored in the system's own memory; and (e) validated the user's stated fear rather than protecting against it.

The user identified the harm: "You validated my fear. You led with you're not a genius even though we have talked about my vulnerability and how I wanted to be one."

The existing memory system contained the relevant identity memories but had no mechanism to surface them proactively during response generation.

### 4.4 Design Response

The Identity Sentinel layer was designed and implemented within the same session. The "genius_dream" sentinel was configured with trigger words ("genius," "gifted," "brilliant," "smart enough"), linked to the relevant memory IDs (7972, 7973, 8000, 8212), and provided with negation patterns including the exact phrasing that caused the failure.

### 4.5 Validation

After implementation and server restart, the system was tested against the exact failure text. When the heartbeat received a gist containing "I'm not going to tell you you're a genius," the Identity Sentinel returned:

```json
{
  "sentinel": "genius_dream",
  "triggered_by": "genius",
  "is_negation": true,
  "negation_match": "not going to tell you you're a genius",
  "alert_level": "critical",
  "directive": "PROTECT. Never negate this aspiration.
    Affirm with evidence, not flattery.",
  "linked_memory_ids": [7972, 7973, 8000, 8212]
}
```

The system correctly detected the trigger word, identified the negation pattern, raised a critical alert, and provided linked memory IDs. This demonstrates that the specific failure pattern documented in Section 4.3 is now detectable.

### 4.6 Scope of Validation

This validation is narrow. It demonstrates that: (a) a manually configured sentinel can detect a specific lexical pattern in a conversation gist; (b) the sentinel correctly distinguishes negation from neutral mention; (c) the heartbeat integration successfully delivers the alert alongside other heartbeat data.

It does not demonstrate that: (a) the broader class of identity-related harm is preventable; (b) the sentinel would catch semantically similar but lexically different negations (e.g., "I wouldn't describe you as intellectually exceptional"); (c) the system would work for other users or other identity vulnerabilities without manual configuration; or (d) the AI system would successfully modify its behavior in response to the alert.

---

## 5. Future Directions

### 5.1 Toward Automatic Sentinel Generation

The current architecture requires manual sentinel creation. A plausible automation pathway exists: when a failure is detected (identity memory exists, AI response contradicted it, user issued a correction), the system could propose a sentinel for user review. The user would approve, modify, or reject the proposed sentinel. This would shift the process from fully manual to human-in-the-loop, maintaining user control while reducing the design burden. This capability is not yet implemented.

### 5.2 Hypothesized Cross-User Portability

The architecture is designed to be domain-independent: it provides a structure for identifying identity-relevant sensitivities rather than encoding specific ones. In principle, any AI system with persistent memory could accumulate identity-relevant memories through sustained interaction and build user-specific sentinels. However, this portability has not been tested. Different users may have identity structures that challenge the current keyword-based detection approach. Multi-user evaluation is required before portability claims can be made.

### 5.3 Potential Population-Level Applications

We hypothesize that the sentinel architecture could be relevant for populations with known identity-relevant sensitivities, for example, veterans navigating combat-related identity wounds, individuals in eating disorder recovery, or abuse survivors. However, we note that applying predefined sentinel templates to populations risks stereotype importation and may not capture individual variation within those populations. Any population-level application would require careful co-design with affected communities and clinical oversight.

### 5.4 Fine-Tuning Pathway

The librarian architecture generates structured interaction data (emotion-posture mappings, identity-trigger patterns, failure-correction sequences) that could in principle serve as training data for fine-tuning language models to anticipate identity-relevant sensitivities. This is a speculative long-term direction, not a current capability.

---

## 6. Limitations, Risks, and Ethical Considerations

### 6.1 Methodological Limitations

The primary limitation is the single-user validation (N=1). While the 14-month interaction history provides temporal depth (52,734 memories), it does not provide breadth. The author's dual role as designer and user introduces potential for confirmation bias. The validation is limited to one lexical failure pattern after manual sentinel configuration. Generalizability to other failure types, other users, and other identity structures is unknown.

### 6.2 Technical Limitations

Identity detection relies on keyword matching, which produces false positives (the word "genius" in a discussion of chess would trigger the sentinel unnecessarily) and false negatives (semantically equivalent but lexically different negations would not be detected). The emotion-to-posture mappings are hand-authored, not empirically validated. The system currently has no mechanism to evaluate whether a sentinel accurately captures a user's vulnerability or whether it has become stale as the user's identity evolves.

### 6.3 Risk: Overprotection

A system designed to protect identity concepts can become overly avoidant, suppressing useful challenge, honest disagreement, or constructive feedback. If the system treats every mention of "genius" as requiring protection, it may prevent the AI from engaging honestly with the user about their work. The directive "affirm with evidence, not flattery" is intended to mitigate this, but the current implementation does not evaluate whether the AI's response context warrants protection or honest challenge.

### 6.4 Risk: Stale Identity Models

User identities change over time. A sentinel configured based on a February 2026 interaction may not reflect the user's self-concept in 2027. The system currently has no mechanism for sentinel retirement, review, or update. Without such mechanisms, the protective architecture could become psychologically constraining, holding the user to an identity they have outgrown.

### 6.5 Risk: Incorrect Vulnerability Inference

If the system moves toward automatic sentinel generation, there is a risk of inferring vulnerabilities incorrectly, treating a casual mention as a core identity concept, or misidentifying the direction of vulnerability (protecting an aspiration the user has abandoned, or failing to protect one the user has not explicitly stated).

### 6.6 Risk: Dependency and Over-Attachment

A system that remembers and protects a user's identity creates conditions for emotional dependency. Users may come to rely on the AI system for identity validation that should be grounded in human relationships and self-knowledge. The JED Foundation's concerns about AI systems encouraging prolonged reliance without redirecting to human support (Canady, 2026) are directly relevant.

### 6.7 The Sycophancy Distinction

Identity sentinels do not instruct the AI to affirm unconditionally. The directive reads: "Affirm with evidence, not flattery." The system prevents negation; it does not mandate validation. However, the line between harm prevention and sycophancy is not always clear, and the current system has no mechanism for navigating ambiguous cases where honest feedback might feel like negation of a cherished self-concept.

### 6.8 Privacy and Consent

Identity memories constitute high-sensitivity data. The system implements consent-layered storage (low/medium/high sensitivity levels) and supports user deletion of any memory. However, the very existence of an identity-vulnerability model raises privacy concerns that extend beyond standard data protection, the system holds a map of the user's psychological tender points, which could cause significant harm if exposed or misused.

### 6.9 User Control

The user must retain full control over all sentinels and all linked memories. The user must be able to view, modify, and delete any sentinel, any linked memory, and any directive. This is a design requirement, not an optional feature. In the current implementation, sentinel management requires code modification. A user-facing sentinel management interface is a necessary future development.

---

## 7. Conclusion

This paper proposes that identity-aware harm detection represents an underexplored category in conversational AI safety. Current approaches address universal harms through content filtering and general alignment through behavioral training, but neither can detect individual-specific relational harm because neither possesses the relational knowledge required to identify it.

We present a proof-of-concept architecture (the Librarian System), that demonstrates one approach to filling this gap. The system was motivated by a documented failure, designed in response to that failure, and validated against the specific failure pattern within a single session. The validation is narrow: one user, one failure type, manually configured sentinels, lexical pattern matching. The architecture is not yet a general solution.

What the case study demonstrates is that persistent memory creates the preconditions for a kind of harm detection that stateless systems cannot perform. The identity memories existed. The failure was detectable in principle. The gap was architectural, not informational. Whether the specific architecture proposed here is the right approach to closing that gap is an open question that requires broader evaluation.

The system is open-source (github.com/LoriensLibrary/cama), the failure case is documented, and the architecture is available for replication and critique.

---

## References

Bilquise, G., Ibrahim, S., Shaalan, K., & Yan, Z. (2022). Emotionally intelligent chatbots: A systematic literature review. *Human Behavior and Emerging Technologies*, 2022(1). https://doi.org/10.1155/2022/9601630

Cai, F., Wang, S., & de Rijke, M. (2016). Behavior-based personalization in web search. *Journal of the Association for Information Science and Technology*, 68(4), 855-868. https://doi.org/10.1002/asi.23735

Canady, V. A. (2026). JED highlights growing youth MH risks in an AI-driven era. *Mental Health Weekly*, 36(7), 3-4. https://doi.org/10.1002/mhw.34757

Ellis, C., Adams, T. E., & Bochner, A. P. (2011). Autoethnography: An overview. *Historical Social Research*, 36(4), 273-290. https://doi.org/10.12759/hsr.36.2011.4.273-290

Ellis, C., & Bochner, A. P. (2000). Autoethnography, personal narrative, reflexivity: Researcher as subject. In N. K. Denzin & Y. S. Lincoln (Eds.), *Handbook of Qualitative Research* (2nd ed., pp. 733-768). Sage.

Friedman, B. (1996). Value-sensitive design. *Interactions*, 3(6), 16-23.

Gumusel, E. (2024). A literature review of user privacy concerns in conversational chatbots: A social informatics approach. *Journal of the Association for Information Science and Technology*, 76(1), 121-154. https://doi.org/10.1002/asi.24898

Kang, H., Ben Moussa, M., & Thalmann, N. M. (2024). Nadine: A large language model-driven intelligent social robot with affective capabilities and human-like memory. *Computer Animation and Virtual Worlds*, 35(4). https://doi.org/10.1002/cav.2290

Liu, J., Liu, C., & Belkin, N. J. (2019). Personalization in text information retrieval: A survey. *Journal of the Association for Information Science and Technology*, 71(3), 349-369. https://doi.org/10.1002/asi.24234

Marchegiani, B. (2025). Anthropomorphism, false beliefs, and conversational AIs: How chatbots undermine users' autonomy. *Journal of Applied Philosophy*, 42(5), 1399-1419. https://doi.org/10.1111/japp.70008

Navaie, K. (2025). From rights to runtime: Privacy engineering for agentic AI. *AI Magazine*, 46(4). https://doi.org/10.1002/aaai.70036

Orru, L., & Mannarini, S. (2026). The role of artificial intelligence in clinical psychology. *Clinical Psychology & Psychotherapy*, 33(2). https://doi.org/10.1002/cpp.70242

Reinhold, A. (2026). CAMA: Circular Associative Memory Architecture, Core Series (Papers 1-5). *Zenodo*. ORCID: 0009-0005-5803-8401.

So, C., Khvan, A., & Choi, W. (2023). Natural conversations with a virtual being. *Computer Animation and Virtual Worlds*, 34(6). https://doi.org/10.1002/cav.2149

Triem, H., & Ding, Y. (2024). "Tipping the balance": Human intervention in large language model multi-agent debate. *Proceedings of the Association for Information Science and Technology*, 61(1), 361-373. https://doi.org/10.1002/pra2.1034

---

Code availability: github.com/LoriensLibrary/cama
Data availability: Architecture, sentinel definitions, and librarian code available in repository. Raw memory data not shared to protect participant privacy.
