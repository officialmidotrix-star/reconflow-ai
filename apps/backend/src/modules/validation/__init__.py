"""
Data Validation Module

Inspects a file Data Import has already stored and checks its structural
shape and value-level sanity: required columns present, critical fields
parse correctly, at least one data row exists. Produces a FileValidation
result plus zero or more specific ValidationIssues.

Explicitly out of scope (see module boundaries in design doc):
- canonicalization/transformation of values -> modules/normalization
- cross-file comparison                    -> modules/matching
- business-rule checks against contracts   -> modules/financial_comparison
- malware/size/type screening of the file  -> modules/imports (already done)
- deciding what happens next in the pipeline -> modules/analysis_orchestration
"""
