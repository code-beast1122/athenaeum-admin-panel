"""Vercel serverless entry point.

The Python runtime imports this module and serves the WSGI callable named
`app`. Everything else lives at the project root, so put that on the path
first - the function's working directory is not guaranteed to be the root.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app  # noqa: E402  (path setup has to come first)

# Vercel terminates TLS at the edge and forwards over HTTP, so honour the
# X-Forwarded-* headers - otherwise url_for(_external=True) and the secure
# session cookie see a plain-http request.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

__all__ = ["app"]
