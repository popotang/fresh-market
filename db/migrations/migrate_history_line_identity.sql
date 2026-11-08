-- The report's id is a document ID repeated on every product row.
-- Sequence plus item distinguishes report rows within a document.
-- Remove the two exploratory rows, which were keyed with the wrong grain.
DELETE FROM purchase_receipt_history_lines;
DELETE FROM source_capture WHERE tenant_id='205149' AND stream='purchase-receive-history';

ALTER TABLE purchase_receipt_history_lines
  DROP INDEX uq_receipt_history_line,
  CHANGE COLUMN source_line_id source_document_id VARCHAR(32) NOT NULL,
  ADD COLUMN source_line_key CHAR(64) NOT NULL AFTER source_document_id,
  ADD UNIQUE KEY uq_receipt_history_line (tenant_id,source_line_key),
  ADD KEY idx_receipt_history_document (tenant_id,source_document_id);
