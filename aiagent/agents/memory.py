from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

MEMORY_NAMESPACE = ("delior_rh", "recruiter_memory")


async def load_long_term_memory(store, user_id: str) -> str:
    try:
        item = await store.aget(MEMORY_NAMESPACE, user_id)
        if item and item.value:
            facts = item.value.get("facts", [])
            if facts:
                bullets = "\n".join(f"- {f}" for f in facts)
                return (
                    "\n\n## CE QUE TU SAIS SUR CE RECRUTEUR (mémoire long terme)\n"
                    + bullets
                )
    except Exception as e:
        logger.warning(f"[load_long_term_memory] {e}")
    return ""


async def save_long_term_memory(store, user_id: str, new_fact: str) -> None:
    try:
        item  = await store.aget(MEMORY_NAMESPACE, user_id)
        facts: list[str] = item.value.get("facts", []) if item else []
        if new_fact not in facts:
            facts = (facts + [new_fact])[-50:]
            await store.aput(MEMORY_NAMESPACE, user_id, {"facts": facts})
            logger.debug(f"[memory] Mémorisé : {new_fact!r}")
    except Exception as e:
        logger.warning(f"[save_long_term_memory] {e}")