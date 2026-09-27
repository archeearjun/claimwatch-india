# ClaimWatch India

Evidence-first political claim verification and election-promise tracking.

## Initial scope

The first corpus is intended to cover public statements and election commitments by Narendra Modi and the BJP. The verification engine itself is speaker-agnostic: every verdict must be derived from cited evidence and the same methodology regardless of speaker or party.

## What V0.1 already does

- Static public dashboard that can be deployed for free.
- Paste a speech/transcript and extract candidate factual claims locally in the browser.
- Review claims with auditable states: supported, contradicted, missing context, insufficient evidence, pending, or not checkable.
- Promise-tracker UI and structured data model.
- D1-compatible SQL schema for claims, evidence, promises, sources, and verdict history.
- Cloudflare Worker API skeleton.
- No paid AI API required.

## Repository layout

- `site/` — zero-build public web app.
- `worker/` — Cloudflare Worker API skeleton.
- `db/schema.sql` — Cloudflare D1 / SQLite schema.
- `data/` — curated source data and normalized claim/promise records.
- `docs/METHODOLOGY.md` — verification rules.
- `docs/ROADMAP.md` — build sequence toward live transcription.

## Run locally

```bash
cd site
python3 -m http.server 8000
```

Open `http://localhost:8000`.

## Deploy free on Cloudflare Pages

For V0.1, point Cloudflare Pages at this repository and set the output directory to `site`. No build command is required.

The Worker/D1 backend is intentionally separate so the static site works before any Cloudflare database is configured.

## Core principle

The system must not infer deception or intent. It classifies the relationship between a checkable claim and available evidence. Every non-pending verdict should expose the evidence, time period, population, definitions, and calculation used.
