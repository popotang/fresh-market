-- Safe additive migration: the existing payload/payload_hash may hold a full
-- purchase detail. List capture must have its own comparison hash.
ALTER TABLE purchase_documents
  ADD COLUMN source_list_hash CHAR(64) NULL
  COMMENT 'Last normalized list response hash; detail payload hash remains separate',
  ALGORITHM=INPLACE, LOCK=NONE;
