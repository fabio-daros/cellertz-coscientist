"""WSGI entry point for the Cellertz web layer."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cellertz_web.settings")

application = get_wsgi_application()
