"""Stored Europe PMC replies.

A cached reply is a record of what the service was told, not a scientific
claim. Freshness is decided on read, so an entry is kept rather than deleted
when it ages out: it still says what the source answered and when.
"""

from __future__ import annotations

from django.db import models


class LiteratureSearchCacheEntry(models.Model):
    query = models.TextField(unique=True)
    stored_at = models.DateTimeField()
    payload = models.JSONField()

    class Meta:
        verbose_name = "literature search reply"
        verbose_name_plural = "literature search replies"
        ordering = ("-stored_at",)

    def __str__(self) -> str:
        return self.query
