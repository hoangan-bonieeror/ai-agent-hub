"""Authentication for the API gateway (plan section 2.1).

Placeholder: no auth yet. Phase 4 adds RBAC; until then every request is anonymous.
"""


async def get_current_actor() -> str:
    return "anonymous"
