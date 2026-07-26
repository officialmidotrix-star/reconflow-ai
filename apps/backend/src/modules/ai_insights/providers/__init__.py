"""
Concrete AIProvider adapter implementations - cloud today, self-hosted
later, per the Phase 2 folder structure's own reservation for this
subpackage. Each implementation satisfies the AIProvider protocol from
../dependencies.py; nothing in the calling service imports a specific
implementation directly - which one is used is a configuration choice at
application start-up.
"""
