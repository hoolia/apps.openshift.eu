-- Ledger, audit trail and documents of the accounting agent.
-- Run once by CloudNativePG when the cluster is created (postInitApplicationSQLRefs), as
-- the superuser in the application database. The tables belong to the superuser, so the
-- application role cannot drop the guards below; it gets only the rights it needs.
--
-- Retention: fiscal records are kept for RETENTION_YEARS years (ledger.retentionYears;
-- the Dutch fiscal retention period, art. 52 AWR, is 7). Every row carries retain_until;
-- a row is deleted only after that date, and the audit trail is append-only.

CREATE TABLE documents (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  administration text NOT NULL,
  kind          text NOT NULL,
  filename      text,
  media_type    text,
  size_bytes    bigint,
  sha256        text NOT NULL,
  storage_uri   text,
  content       bytea,
  source        text,
  external_id   text,
  received_at   timestamptz NOT NULL DEFAULT now(),
  retain_until  date NOT NULL DEFAULT (current_date + interval 'RETENTION_YEARS years'),
  UNIQUE (administration, sha256)
);
COMMENT ON TABLE documents IS 'Source documents (invoices, receipts, statements); kept RETENTION_YEARS years';

CREATE TABLE ledger (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  administration text NOT NULL,
  draft_id      text NOT NULL,
  entry_date    date NOT NULL,
  journal       text,
  account       text,
  description   text,
  amount        numeric(18, 2) NOT NULL,
  currency      char(3) NOT NULL DEFAULT 'EUR',
  vat_code      text,
  document_id   bigint REFERENCES documents (id),
  status        text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'approved', 'booked', 'rejected')),
  external_id   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL,
  decided_by    text,
  decided_at    timestamptz,
  decision_note text,
  retain_until  date NOT NULL DEFAULT (current_date + interval 'RETENTION_YEARS years')
);
CREATE INDEX ledger_draft ON ledger (administration, draft_id);
CREATE INDEX ledger_status ON ledger (status, created_at);
COMMENT ON TABLE ledger IS 'Drafted, approved and booked entries; kept RETENTION_YEARS years';

CREATE TABLE audit (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL,
  action        text NOT NULL,
  administration text,
  subject_type  text,
  subject_id    text,
  draft_id      text,
  details       jsonb NOT NULL DEFAULT '{}'::jsonb,
  retain_until  date NOT NULL DEFAULT (current_date + interval 'RETENTION_YEARS years')
);
CREATE INDEX audit_subject ON audit (subject_type, subject_id);
CREATE INDEX audit_draft ON audit (draft_id);
COMMENT ON TABLE audit IS 'Append-only trail of every draft, approval, rejection and booking; kept RETENTION_YEARS years';

CREATE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit is append-only';
END $$;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit
  FOR EACH ROW EXECUTE FUNCTION audit_append_only();

CREATE FUNCTION keep_until_retained() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.retain_until > current_date THEN
    RAISE EXCEPTION '% row % is retained until %', TG_TABLE_NAME, OLD.id, OLD.retain_until;
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER ledger_retained BEFORE DELETE ON ledger
  FOR EACH ROW EXECUTE FUNCTION keep_until_retained();
CREATE TRIGGER documents_retained BEFORE DELETE ON documents
  FOR EACH ROW EXECUTE FUNCTION keep_until_retained();

CREATE FUNCTION keep_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.retain_until < OLD.retain_until THEN
    RAISE EXCEPTION '% row %: retain_until cannot be shortened', TG_TABLE_NAME, OLD.id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_keep_retention BEFORE UPDATE ON ledger
  FOR EACH ROW EXECUTE FUNCTION keep_retention();
CREATE TRIGGER documents_keep_retention BEFORE UPDATE ON documents
  FOR EACH ROW EXECUTE FUNCTION keep_retention();

GRANT SELECT, INSERT ON audit TO OWNER_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger, documents TO OWNER_ROLE;
