"""Compatibility entrypoint for ``uvicorn main:app``.

The application lives in ``app.main``. Keeping this thin module lets local
commands and IDE run configurations that still point at ``main:app`` continue
to work from the ``backend`` directory.
"""

from app.main import app, create_app

__all__ = ["app", "create_app"]
