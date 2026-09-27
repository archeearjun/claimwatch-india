# Verification methodology

## 1. Separate extraction from verification

A detected sentence is only a candidate claim. Detection must never automatically produce a truth verdict.

## 2. Checkability gate

A claim is checkable when a reasonable reviewer can identify objective evidence that could support or contradict the material proposition. Opinions, predictions, slogans, moral judgments and ambiguous rhetoric are marked `not_checkable` unless a narrower factual proposition can be isolated without changing the speaker's meaning.

## 3. Evidence hierarchy

Prefer primary and directly auditable material:

1. Laws, gazette notifications, court records, parliamentary records and audit reports.
2. Official statistical datasets and administrative records.
3. Original manifesto, speech, interview or press-release text when establishing what was promised or said.
4. High-quality independent research when the primary record cannot resolve the question.
5. Reputable fact-checking and news reporting as discovery/context sources, with links back to primary evidence where possible.

No party, government, media outlet or fact-checker is treated as universally authoritative.

## 4. Comparability test

Before comparing numbers, record:

- population / denominator;
- geographic scope;
- time period;
- nominal vs real values;
- definition and methodology;
- unit;
- revisions or breaks in series.

If those are materially different, the system should not present a direct comparison without explaining the mismatch.

## 5. Verdict states

- `supported` — evidence supports the material factual proposition.
- `contradicted` — evidence directly conflicts with the proposition.
- `context` — the statement omits material context or relies on a misleading comparison.
- `insufficient` — available evidence does not justify a stronger conclusion.
- `not_checkable` — the proposition is not objectively verifiable as stated.
- `pending` — review is incomplete.

## 6. Intent

The system evaluates claims, not a speaker's private state of mind. A contradicted claim should not automatically be described as a lie.

## 7. Promise tracking

Store the exact promise text and primary-source location first. Then separately normalize target, deadline, jurisdiction and measurable outcome. Do not invent a deadline that the original commitment did not contain.

## 8. Revisions

Verdicts are append-only. New evidence creates a new verdict that supersedes the old one, preserving the historical review trail.
