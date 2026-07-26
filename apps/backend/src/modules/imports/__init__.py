"""
Data Import Module

Single entry point for raw file uploads (POS exports, delivery-platform
settlement reports) into ReconFlow AI. Owns file-level guardrails, storage,
checksum-based duplicate detection, and versioning of re-uploads.

Explicitly out of scope (see module boundaries in design doc):
- content/schema validation      -> modules/validation
- parsing rows into Transactions -> modules/normalization
- cross-file comparison          -> modules/matching, modules/financial_comparison
- triggering the n8n pipeline    -> modules/analysis_orchestration
"""
