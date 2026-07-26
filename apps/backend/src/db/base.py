from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The one declarative base every module's models.py imports.

    Promoted out of the Data Import module now that a second module
    (Data Validation) needs to declare a real foreign key to one of Data
    Import's tables. Previously each module would have needed its own
    stand-in tables to test in isolation (as Data Import's own test suite
    still does, for `analyses` and `users`, since those modules don't exist
    yet) - sharing one Base removes that need for any two modules that
    both already exist.
    """
