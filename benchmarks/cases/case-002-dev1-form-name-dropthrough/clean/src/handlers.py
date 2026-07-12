from fastapi import APIRouter, Request

clients_router = APIRouter(prefix="/clients")


@clients_router.post("/inline")
async def create_inline_client(request: Request):
    form = await request.form()
    legal_name = form.get("new_client_legal_name")
    return {"legal_name": legal_name, "ok": legal_name is not None}
