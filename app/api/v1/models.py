"""Models API - 获取可用模型列表"""

from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import auth_manager


router = APIRouter()


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "google"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


# 可用模型列表
AVAILABLE_MODELS = [
    ModelInfo(id="gemini-3.0-flash", created=1704067200, owned_by="google"),
    ModelInfo(id="gemini-3.0-flash-thinking", created=1704067200, owned_by="google"),
    ModelInfo(id="gemini-3.0-pro", created=1704067200, owned_by="google"),
]


@router.get("/models", response_model=ModelsResponse)
async def list_models(_: str = Depends(auth_manager.verify)):
    """获取可用模型列表"""
    return ModelsResponse(data=AVAILABLE_MODELS)


@router.get("/models/{model_id}")
async def get_model(model_id: str, _: str = Depends(auth_manager.verify)):
    """获取指定模型信息"""
    for model in AVAILABLE_MODELS:
        if model.id == model_id:
            return model
    return {"error": {"message": f"Model '{model_id}' not found", "type": "invalid_request_error"}}
