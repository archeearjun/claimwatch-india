PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS speakers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  party TEXT,
  office TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  publisher TEXT,
  url TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN (
    'speech','manifesto','government_dataset','parliament','audit','law','court','research','fact_check','news','other'
  )),
  published_at TEXT,
  accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archive_url TEXT,
  content_hash TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  speaker_id TEXT REFERENCES speakers(id),
  exact_text TEXT NOT NULL,
  normalized_claim TEXT,
  statement_at TEXT,
  source_id TEXT REFERENCES sources(id),
  source_timestamp_seconds INTEGER,
  checkability TEXT NOT NULL DEFAULT 'pending' CHECK (checkability IN ('checkable','not_checkable','pending')),
  claim_type TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','in_review','reviewed')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  evidence_text TEXT NOT NULL,
  supports_direction TEXT NOT NULL CHECK (supports_direction IN ('supports','contradicts','context','neutral')),
  population TEXT,
  period_start TEXT,
  period_end TEXT,
  unit TEXT,
  extracted_value TEXT,
  methodology_notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS verdicts (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  verdict TEXT NOT NULL CHECK (verdict IN ('supported','contradicted','context','insufficient','not_checkable','pending')),
  confidence TEXT CHECK (confidence IN ('high','medium','low')),
  explanation TEXT NOT NULL,
  calculation TEXT,
  reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  supersedes_verdict_id TEXT REFERENCES verdicts(id)
);

CREATE TABLE IF NOT EXISTS promises (
  id TEXT PRIMARY KEY,
  speaker_id TEXT REFERENCES speakers(id),
  party TEXT,
  election_year INTEGER NOT NULL,
  exact_text TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(id),
  page_or_section TEXT,
  category TEXT,
  target TEXT,
  target_value TEXT,
  baseline TEXT,
  deadline TEXT,
  jurisdiction TEXT,
  measurable INTEGER NOT NULL DEFAULT 0 CHECK (measurable IN (0,1)),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('complete','partial','no_documented_implementation','insufficient','not_measurable','deadline_not_reached','pending')),
  status_explanation TEXT,
  last_reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS promise_evidence (
  promise_id TEXT NOT NULL REFERENCES promises(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  PRIMARY KEY (promise_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS claim_links (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  related_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK (relation IN ('same_claim','similar_claim','updates','contradicts_previous','repeats_promise','other')),
  similarity REAL,
  PRIMARY KEY (claim_id, related_claim_id)
);

CREATE INDEX IF NOT EXISTS idx_claims_speaker_date ON claims(speaker_id, statement_at);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_promises_year ON promises(election_year);
CREATE INDEX IF NOT EXISTS idx_verdicts_claim ON verdicts(claim_id, reviewed_at);
