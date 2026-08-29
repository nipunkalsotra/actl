"""§17.1 U3: audit narration. LLM output is advisory only -- §17.1's own
words: "Show the raw entries. Nothing is lost -- narration is cosmetic by
construction." A failure here must never affect the underlying audit
chain or the transaction it describes: `narrate_entry` never raises, and
the write `narrate_and_store` performs
(`AuditLogRepository.update_narration`) touches only the narration
column, which the database's own append-only trigger allows to change in
isolation -- `entry_hash`/`prev_hash`/every other field is untouched no
matter what.

Callers must run this in a UnitOfWork *separate from, and after*, the one
that appended the entry being narrated -- narration is never part of the
money-moving transaction itself, so nothing about its own success or
failure can affect that transaction's outcome.
"""

from __future__ import annotations

from actl.application.ports import LLMClient, LLMUnavailable
from actl.infrastructure.db.repositories.audit_log import AuditLogRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.prompts.narration import SYSTEM_PROMPT, build_user_prompt

MAX_TOKENS = 200


async def narrate_entry(llm: LLMClient, entry: AuditLogRecord) -> str | None:
    """Plain-English prose describing one audit entry, or None if the LLM
    is unavailable. Never raises."""
    try:
        return await llm.complete_text(
            system=SYSTEM_PROMPT, user=build_user_prompt(entry), max_tokens=MAX_TOKENS
        )
    except LLMUnavailable:
        return None


async def narrate_and_store(llm: LLMClient, uow: UnitOfWork, entry: AuditLogRecord) -> bool:
    """Best-effort. Returns whether a narration was actually written --
    callers must not treat a False return as an error, only as "no
    narration this time," per §17.1's "nothing is lost" guarantee."""
    narration = await narrate_entry(llm, entry)
    if narration is None:
        return False
    assert entry.seq is not None
    await uow.audit_log.update_narration(entry.seq, narration)
    return True
