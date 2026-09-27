# ClaimWatch India

Evidence-first political claim verification and election-promise tracking.

## Initial research scope

The first corpus covers public statements and election commitments by Narendra Modi and the Bharatiya Janata Party (BJP). The architecture and verification rules are speaker-agnostic: the same evidence rules can be applied to other political actors later.

## What the current build does

### Autonomous discovery

An hourly GitHub Actions job:

- reads full publisher speech text from PMIndia when available;
- discovers recent news through Google News RSS;
- resolves a bounded set of news headlines to publisher pages and reads article bodies only where access/robots rules permit it;
- monitors the public YouTube feeds of the Narendra Modi, PMO India and BJP channels without requiring a paid API;
- optionally expands YouTube search when a free `YOUTUBE_API_KEY` is configured;
- extracts candidate factual claims and policy commitments;
- preserves URLs, publication times, transcript status and local claim context.

The public feed is `data/discovery/latest.json`.

### Live local transcription

The public site can capture audio from a browser tab after the user explicitly shares that tab and enables audio.

- Whisper runs locally in the browser through Transformers.js.
- Captured audio is not uploaded to ClaimWatch's server.
- Hindi/English/Hinglish can be transcribed.
- Newly detected factual-claim candidates are sent automatically to the same-origin evidence API.
- The live screen separates supported, contradicted and still-pending checks; contradiction is not treated as proof of deliberate deception.

### Manifesto memory

`scanner/manifestos.py` builds a structured corpus from registered 2014, 2019 and 2024 manifesto sources.

Each candidate keeps:

- election year and page;
- full extracted source text plus a short anchor;
- content fingerprint;
- category and key terms;
- numeric/deadline signals;
- links to related commitments from other election years;
- canonical manifesto provenance.

Generated corpus: `data/promises/candidates.json`.

### Evidence engine

`scanner/evidence.py` runs on a schedule and after discovery/corpus refreshes.

It:

- searches official/statistical/audit/parliamentary sources first;
- reads current reporting as context, not as proof by itself;
- optionally searches Google Fact Check Tools when `FACTCHECK_API_KEY` is configured;
- keeps PMIndia/party material as statement provenance rather than self-verification;
- normalizes numbers before comparison;
- uses a small local CPU NLI model with strict thresholds for automated supported/contradicted states;
- leaves uncertain or conflicting cases pending;
- rotates through manifesto commitments and preserves previous promise checks so implementation-evidence coverage accumulates over time.

Generated feed: `data/evidence/latest.json`.

### Instant check API

Cloudflare Pages Function `functions/api/check.js`:

- accepts a factual claim from the manual or live-transcript UI;
- immediately reuses a sufficiently similar cached strict assessment when one exists;
- otherwise retrieves current evidence leads and historical promise matches;
- does not manufacture a final verdict when the evidence gate has not been met.

## Zero-cost architecture

- GitHub repository — source, corpus registry and generated public feeds.
- GitHub Actions — scheduled discovery, manifesto extraction, evidence scans and CI.
- Cloudflare Pages — frontend.
- Cloudflare Pages Functions — instant evidence API.
- Browser Whisper/Transformers.js — transcription with no paid speech API.
- Open web/RSS + official public sources — discovery and evidence.
- Optional free API keys — YouTube Data API expansion and Google Fact Check Tools.

No paid LLM or transcription API is required for the core pipeline.

## Repository layout

- `site/` — public web app.
- `functions/api/check.js` — instant evidence endpoint.
- `scanner/scan.py` — discovery + publisher-text extraction.
- `scanner/manifestos.py` — primary manifesto corpus builder.
- `scanner/evidence.py` — scheduled evidence/promise-action engine.
- `scanner/test_core.py` — regression tests for extraction and evidence hygiene.
- `corpus/manifesto_sources.json` — registered manifesto provenance.
- `data/discovery/latest.json` — current discovery feed.
- `data/promises/candidates.json` — generated promise memory.
- `data/evidence/latest.json` — current evidence packets.
- `.github/workflows/` — discovery, corpus, evidence and CI automation.
- `docs/METHODOLOGY.md` — evidence rules.
- `docs/V1_SPEC.md` — product specification.

## Run locally

```bash
python3 -m pip install -r scanner/requirements.txt
python3 scanner/scan.py
python3 scanner/manifestos.py
python3 -m unittest scanner/test_core.py

cd site
python3 -m http.server 8000
```

The static UI will load the generated public feeds from GitHub. Cloudflare Pages is needed to exercise the same-origin `/api/check` Function exactly as deployed.

## Cloudflare Pages

Use:

- production branch: `main`
- framework preset: none
- build command: `exit 0`
- build output directory: `site`
- root directory: blank

If Build Watch Paths are enabled, include **both**:

```text
site/**
functions/**
```

Data/scanner-only commits do not need a frontend deployment.

## Evidence principle

Extraction, retrieval and verdicting are separate stages. A statement is never classified merely because a language model "knows" something. A substantive automated verdict requires traceable evidence and the strict evidence gate; otherwise the state remains pending or insufficient.
