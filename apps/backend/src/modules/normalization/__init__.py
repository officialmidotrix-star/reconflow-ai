"""
Data Normalization Module

Turns a file Data Validation has already confirmed is structurally sound
into canonical Transaction rows: timestamps localized to the analysis's
branch timezone and converted to UTC, amounts parsed and quantized to a
fixed precision, references trimmed. Adds no new judgment about whether
the data is correct - only makes already-valid data uniform.

Precondition: the referenced uploaded file must have a PASSED
FileValidation. This module does not re-validate structure - it trusts
Data Validation's result and reuses its schema registry directly rather
than re-implementing column matching.

Explicitly out of scope (see module boundaries in design doc):
- cross-file pairing                    -> modules/matching
- business-rule comparison vs contracts -> modules/financial_comparison
- owning branch/timezone/currency data  -> modules/organizations (not built yet)
- deciding what happens next in the pipeline -> modules/analysis_orchestration
"""
