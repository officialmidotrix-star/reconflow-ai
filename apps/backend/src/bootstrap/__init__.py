"""
Application composition root - the piece every module's router docstring
has been pointing to since Data Import ("Real wiring assembled at
application start-up... override this dependency"). Nothing here is
business logic; it's entirely about connecting the 16 already-built,
already-tested modules into one runnable FastAPI application.
"""
