# ClaimWatch India

Evidence-first political claim verification and election-promise tracking.

## Initial scope

The first corpus is intended to cover public statements and election commitments by Narendra Modi and the BJP. The verification engine itself is speaker-agnostic: every verdict must be derived from cited evidence and the same methodology regardless of speaker or party.

## V1 requirement

ClaimWatch is not meant to be a paste-only fact checker. V1 is designed to actively:

- discover recent PMIndia speeches and public statements;
- scan recent news coverage through GDELT;
- discover recent YouTube videos through the official YouTube Data API when a free API key is configured;
- obtain publisher text when available and queue video/audio for local Whisper transcription when no lawful machine-readable transcript is available;
- extract atomic factual claims and promises;
- match new claims against historical statements and manifesto commitments;
- gather primary-source evidence about current implementation/results;
- classify only what the evidence supports: supported, contradicted, missing context, insufficient evidence, not checkable, or pending;
- preserve source URLs, dates, definitions, calculations and revision history.

See `docs/V1_SPEC.md` for the product specification.

## What is already in the repository

- Static public dashboard that can be deployed for free.
- Paste a speech/transcript and extract candidate factual claims locally in the browser.
- Hourly GitHub Actions discovery scanner for PMIndia + GDELT, with YouTube discovery enabled by a free API key.
- A structured discovery feed at `data/discovery/latest.json`.
- Promise-tracker UI and structured data model.
- D1-compatible SQL schema for claims, evidence, promises, sources, and verdict history.
- Cloudflare Worker API skeleton.
- Evidence-first verification methodology.
- No paid AI API required.

## Repository layout

- `site/` — zero-build public web app.
- `scanner/` — scheduled public-source discovery.
- `.github/workflows/discovery-scan.yml` — hourly scanner workflow.
- `worker/` — Cloudflare Worker API skeleton.
- `db/schema.sql` — Cloudflare D1 / SQLite schema.
- `data/` — discovery feed plus curated claim/promise records.
- `docs/METHODOLOGY.md` — verification rules.
- `docs/V1_SPEC.md` — minimum autonomous V1 behavior.
- `docs/ROADMAP.md` — build sequence toward live transcription and evidence matching.

## Run locally

```bash
cd site
python3 -m http.server 8000
```

Open `http://localhost:8000`.

Run the discovery scanner:

```bash
python3 -m pip install -r scanner/requirements.txt
python3 scanner/scan.py
```

## Free external keys

The scanner runs PMIndia and GDELT discovery without a paid service.

To add YouTube discovery, create a Google Cloud API key with the YouTube Data API v3 enabled and store it in the repository secret `YOUTUBE_API_KEY`. No key is committed to the repository.

## Deploy free on Cloudflare Pages

Point Cloudflare Pages at this repository and set the output directory to `site`. No framework is required.

To avoid data-only scanner commits consuming unnecessary Pages builds, configure Cloudflare Pages build watch paths so frontend builds are triggered by `site/**` (and any later frontend build files), not by `data/**`.

## Core principle

The system must not infer deception or intent. It classifies the relationship between a checkable claim and available evidence. Every non-pending verdict should expose the evidence, time period, population, definitions, and calculation used.
