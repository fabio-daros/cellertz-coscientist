"""Concrete read-only adapters for conversational access."""

from cellertz.conversation.episodic import EpisodicReadOnlyQuery, build_readonly_service

__all__ = ["EpisodicReadOnlyQuery", "build_readonly_service"]
