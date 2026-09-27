# ClaimWatch India — V1 product specification

## Goal

ClaimWatch India should actively discover public political statements and evidence rather than depend on users pasting text.

The initial research scope is public statements and election commitments by Narendra Modi and the Bharatiya Janata Party (BJP). The data model and verification methodology remain speaker-agnostic.

## Minimum V1 behavior

1. **Discover**
   - Scan official PMIndia speech/video pages.
   - Discover recent reporting through RSS/search and read a bounded set of publisher article bodies where access rules permit.
   - Monitor the public feeds of core YouTube channels with no API key; optionally expand discovery with the YouTube Data API when a free key is configured.
   - Preserve canonical source URLs, timestamps and publisher/channel metadata.

2. **Transcribe / extract text**
   - Prefer an official publisher transcript when one exists.
   - For live/user-selected audio, run Whisper locally in the browser.
   - Do not rely on YouTube's official caption API for arbitrary public videos because caption download requires video-edit permission.
   - Videos with no lawful machine-readable transcript are queued for browser-assisted/local transcription.

3. **Extract claims**
   - Split source text into atomic, checkable propositions.
   - Preserve the original source location/timestamp.
   - Separate claim extraction from truth verification.

4. **Verify factual statements**
   - Search primary evidence first: legislation, parliamentary records, CAG, MoSPI, RBI, ministry datasets, government dashboards and other directly auditable records.
   - Search Google's Fact Check Tools API as a secondary discovery source when configured.
   - Read current news/reporting for additional context, but do not treat news headlines as proof by themselves.
   - Check comparability: dates, geography, denominator, unit, methodology and revisions.
   - Output only: supported, contradicted, missing_context, insufficient, not_checkable, or pending.

5. **Track promises**
   - Build a primary-source corpus from election manifestos and dated public commitments.
   - Normalize each promise into target, deadline, geography, metric and baseline where the source actually specifies them.
   - Search current official records and recent reporting for implementation evidence.
   - Match new evidence to old promises.
   - Output implementation status only when evidence supports it: complete, partial, no_documented_implementation, insufficient, not_measurable, deadline_not_reached, pending.

6. **Historical memory**
   - Match new claims against earlier statements and promises.
   - Show "said before" / repeated-commitment timelines without using semantic similarity itself as a truth verdict.

## Evidence rules

- Every substantive verdict must expose its evidence URLs and dates.
- Party/government claims can establish what was said; they do not prove implementation by themselves.
- A contradiction does not establish deliberate deception or intent.
- When evidence is conflicting or incomparable, the system must say so.
- Do not bypass paywalls, authentication, robots exclusions, or technical access controls.

## Zero-cost architecture

- GitHub: source + scheduled discovery jobs.
- GitHub Actions: public-repository scheduled scanner.
- Cloudflare Pages: public frontend.
- Cloudflare Worker + D1: API and structured evidence database.
- RSS/open web: recent news discovery plus bounded publisher-page reading.
- Public YouTube channel feeds: core video discovery without an API key.
- YouTube Data API: optional broader discovery within free quota.
- Google Fact Check Tools API: prior fact-check discovery when configured.
- whisper.cpp / Transformers.js: client-side transcription and model inference.

## YouTube limitation

The official YouTube API supports search/discovery, but downloading arbitrary public caption tracks is not an available general-purpose capability: caption listing requires OAuth and caption download requires permission to edit the video. ClaimWatch therefore uses official/publisher transcripts where available and local browser transcription for audio the user is permitted to play/capture.
