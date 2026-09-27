# ClaimWatch proof rules

ClaimWatch separates **discovery**, **machine inference**, and **proof**.

Machine learning may locate, cluster and interpret candidate evidence. A public
counter must be backed by a proof packet whose decisive comparison is
inspectable and reproducible.

## 1. Factual claim proof

A publishable numerical claim proof records:

- exact claim text;
- original statement source and date;
- claimed value;
- comparison operator (equal / at least / at most / approximate);
- metric/context terms;
- time period when the claim specifies one;
- verified primary evidence URL;
- observed value from that source;
- compatible numeric unit;
- deterministic comparison result.

The engine currently requires strong subject overlap with a verified primary
source before a numeric comparison can publish a supported or contradicted
state. Ambiguous multi-number passages remain pending.

For non-numeric claims, multilingual NLI may produce a verdict only behind the
strict verified-primary gate. NLI is evidence interpretation, not the evidence
itself; the supporting source remains visible.

## 2. Promise proof packet

Each manifesto commitment keeps:

- election year;
- full extracted manifesto text;
- page number;
- source fingerprint;
- measurable target(s), when present;
- deadline phrase, when present;
- derived deadline year;
- current verified-primary evidence candidates;
- structured outcome proof, when available.

### Deadline derivation

An explicit date such as "by 2022" is used directly.

A relative period is derived from the election year. Example:

```text
Promise year: 2014
Phrase:       next five years
Deadline:     2019
```

The derived deadline is always visible in the proof packet.

## 3. Outcome states

### Fulfilled by deadline

Requires a directly comparable observed result that meets the target and is
documented at or before the deadline.

### Target reached — deadline timing unresolved

Verified primary evidence shows the target was reached, but the available
evidence does not prove it was reached by the promised deadline.

### Proven not fulfilled by deadline

This is the strict red-counter state.

It requires all of the following:

1. a measurable promise with a derived or explicit deadline;
2. one unambiguous target value;
3. verified-primary evidence about the same subject/metric;
4. a directly comparable observed value;
5. evidence dated at or just after the deadline;
6. compatible units and comparison direction;
7. deterministic comparison showing the target was missed.

A passed deadline **without** the observed value is not enough.

### Progress documented

Verified primary evidence overlaps the object of the promise and documents
implementation activity, but does not prove the promised outcome.

### Deadline passed — unresolved

The deadline has passed but ClaimWatch does not yet have evidence sufficient to
prove either fulfilment or non-fulfilment.

### Deadline not reached

The promise is not yet overdue.

### Insufficient evidence

The promise is potentially checkable, but the evidence packet is incomplete.

### Not machine-measurable

The extracted promise does not currently contain a sufficiently specific
numeric/time-bound outcome for automatic proof.

## 4. Repeated claim memory

Repeated statements are grouped into semantic families.

The matcher uses:

- lexical pre-filtering;
- numeric compatibility;
- bidirectional multilingual entailment.

Repetition and truth are separate dimensions. A family can be repeated while
its evidence state is supported, contradicted or pending.

The site may therefore show a count such as **Repeated contradicted claims**
only when the underlying claim family has both:

- multiple independently recorded occurrences; and
- a publishable contradicted evidence verdict.

## 5. Source rules

Material from a speaker, party, manifesto or government publicity page may
establish **what was said or promised**.

That material does not automatically prove that the underlying factual claim or
implementation outcome is true.

Primary evidence used for outcome proof must resolve to a verified
government/statistical/audit/parliamentary source and pass the relevance gate.
Search-engine result placement is never treated as source provenance.

## 6. ML role

ML is used to:

- transcribe speech;
- detect candidate claims;
- match paraphrases;
- find related historical promises;
- rank evidence;
- test semantic entailment/contradiction.

ML alone does not create a red promise counter. The decisive red-counter
comparison is structured evidence.
