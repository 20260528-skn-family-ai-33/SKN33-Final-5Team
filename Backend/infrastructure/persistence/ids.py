"""Identifier and timestamp helpers shared by every persisted record."""
import uuid
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat()

def uid(): return str(uuid.uuid4())
