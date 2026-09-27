# Roadmap

## V0.1 — repository + inspectable prototype

- Static Cloudflare Pages-ready site.
- Browser-side candidate claim extraction.
- Promise and claim schemas.
- Evidence-first methodology.
- Worker + D1 skeleton.

## V0.2 — primary-source ingestion

- Import BJP 2014, 2019 and 2024 manifesto text from archived primary documents.
- Preserve exact wording, page/section, URL, archived URL and content hash.
- Add PM speech ingestion with timestamps where available.
- Add source deduplication and immutable content hashes.

## V0.3 — local semantic matching

- Generate local embeddings in-browser or during an offline indexing job using an open model.
- Match new claims to old claims/promises.
- Show “said before” timeline without assigning a verdict from similarity alone.

## V0.4 — live transcription

- Integrate Whisper-compatible WebAssembly/WebGPU transcription in the browser.
- Capture user-selected tab/audio only after explicit browser permission.
- Segment Hindi/English/Hinglish audio and attach exact timestamps.
- Keep raw audio local by default.

## V0.5 — evidence adapters

- Structured adapters for parliamentary records, RBI/MoSPI datasets, CAG reports and selected ministry data.
- Unit/date/population comparability checks.
- Reproducible numerical calculations.

## V0.6 — review workflow

- Human review queue for high-impact verdicts.
- Evidence packet page.
- Revision history.
- Source archiving and integrity hashes.
