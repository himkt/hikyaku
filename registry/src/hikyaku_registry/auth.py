from fastapi import HTTPException, Request


async def get_authenticated_agent(request: Request = None, store=None) -> str:
    if request is None or store is None:
        raise HTTPException(status_code=401)

    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401)

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
        raise HTTPException(status_code=401)

    token = parts[1].strip()
    agent_id = await store.lookup_by_api_key(token)
    if agent_id is None:
        raise HTTPException(status_code=401)

    return agent_id
