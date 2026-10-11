from fastapi import APIRouter

from app.api.v1 import analysis, auth, compliance, controls, documents, evaluation, governance, health, regulations

api_router = APIRouter()
for module in (health, auth, documents, regulations, controls, compliance, analysis, governance, evaluation):
    api_router.include_router(module.router)

__all__ = ["api_router"]
