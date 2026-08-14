"""Ingestion package — Speech-to-Text transcript processing and data normalization."""

from app.ingestion.transcript_ingestor import transcript_ingestor, TranscriptIngestor

__all__ = ["transcript_ingestor", "TranscriptIngestor"]
