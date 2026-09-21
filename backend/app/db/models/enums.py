"""
String enums for status/role columns.

The database stores plain TEXT (easy to read in psql, easy to extend) and a CHECK
constraint rejects unknown values. In Python we use StrEnum so typos become
errors at import time instead of silently bad rows.
"""

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"  # created the tenant; full control
    ADMIN = "admin"  # manages users, collections, documents
    MEMBER = "member"  # uploads + asks questions
    VIEWER = "viewer"  # asks questions only


class CollectionVisibility(StrEnum):
    # Everyone in the tenant can read it (e.g. "Public Policies").
    TENANT_WIDE = "tenant_wide"
    # Only explicitly granted groups can read it (groups arrive in Week 4; until
    # then only owners/admins can see restricted collections — deny by default).
    RESTRICTED = "restricted"


class CollectionPermission(StrEnum):
    READ = "read"  # search and read documents in the collection
    WRITE = "write"  # ...and upload/edit/delete them


class IngestionJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class DocumentStatus(StrEnum):
    PENDING = "pending"  # row created, file stored, not processed yet
    PROCESSING = "processing"  # parse -> chunk -> embed in progress
    READY = "ready"  # chunks stored, searchable
    FAILED = "failed"  # see documents.error_message


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class UsageEventType(StrEnum):
    EMBED = "embed"  # embedding a search query
    CHAT = "chat"  # LLM answer generation
    RERANK = "rerank"  # Week 3
    INGEST = "ingest"  # embedding document chunks at upload time
    REWRITE = "rewrite"  # Week 6: rewriting a follow-up into a standalone query


def sql_in(enum_cls: type[StrEnum]) -> str:
    """Build the value list for a CHECK constraint, e.g. "'owner', 'admin', ..."."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)
