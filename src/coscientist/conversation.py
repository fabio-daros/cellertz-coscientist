"""Entry point that runs the conversation service with Django-owned pieces.

`cellertz` is the science and imports no framework, so it cannot reach for a
Django-backed store on its own. This module is the composition root: it starts
Django, registers what Django owns, and then hands control to the service.

Running `python -m cellertz.conversation` instead still works and keeps the
sqlite cache. The difference is which pieces the process is given, not which
code answers a request.
"""

from __future__ import annotations

import os


def install() -> None:
    """Start Django and register what it owns. Separate from `main` so the
    wiring can be asserted without starting a server."""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coscientist.settings")

    import django

    django.setup()

    from cellertz.application.literature_search import set_literature_cache_factory
    from coscientist.literature.cache import DjangoLiteratureCache

    # A factory, not an instance: the service may build a cache per use, and a
    # model class must not be touched before django.setup() has run.
    set_literature_cache_factory(DjangoLiteratureCache)


def main() -> None:
    install()

    from cellertz.conversation.__main__ import main as serve_conversation_service

    serve_conversation_service()


if __name__ == "__main__":
    main()
