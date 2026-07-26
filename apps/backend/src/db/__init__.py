"""Shared database layer used by every module's models.

Exists so all modules' tables land in one MetaData/migration set, per the
Phase 2 folder structure. Modules still only ever write to the tables they
themselves declare - sharing this Base is purely about schema/FK
resolution and migrations, not about shared ownership of data.
"""
