-- Enable pgvector extension
-- Run once on first postgres container initialization.
CREATE EXTENSION IF NOT EXISTS vector;
