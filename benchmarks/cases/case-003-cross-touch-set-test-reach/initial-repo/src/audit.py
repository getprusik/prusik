from fastapi import APIRouter

audit_router = APIRouter(prefix="/audit")


@audit_router.get("/skips")
async def list_skips():
    return {"skips": []}
