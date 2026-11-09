"""URL routes for the Cellertz web layer.

The conversation service still serves /api on its own port. Routes move here
one at a time, each with the tests that hold its governance invariants.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
