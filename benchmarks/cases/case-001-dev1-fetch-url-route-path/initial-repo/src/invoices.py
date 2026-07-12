from fastapi import APIRouter

invoices_router = APIRouter(prefix="/invoices")


@invoices_router.get("/clients/search")
async def search_clients(q: str):
    return {"matches": [], "query": q}
