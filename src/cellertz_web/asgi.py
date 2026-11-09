"""ASGI entry point for the Cellertz web layer."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cellertz_web.settings")

application = get_asgi_application()
