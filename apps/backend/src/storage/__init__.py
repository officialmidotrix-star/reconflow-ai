"""
Shared file storage layer, used by any module that needs to persist
encrypted bytes to disk - not just Data Import. Promoted out of
modules/imports/storage.py once a second module (Reporting & Export)
needed the same generic capability, same reasoning as db/base.py being
promoted out of modules/imports/models.py for the same kind of reason.
"""
