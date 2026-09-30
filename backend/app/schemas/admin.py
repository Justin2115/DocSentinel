from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FolderPermissionMatrixItem(BaseModel):
    folder: str
    admin: bool = True
    upload_maker: bool = False
    upload_checker: bool = False


class FolderPermissionsUpdateRequest(BaseModel):
    permissions: List[FolderPermissionMatrixItem]


class WorkflowRuleItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    type: str = Field(default="warning", pattern="^(warning|security|claims)$", description="warning, security, or claims")
    title: str = Field(min_length=1, max_length=255)
    route: str = Field(min_length=1, max_length=150)
    document_type: Optional[str] = None
    threshold: float = Field(ge=50.0, le=100.0, default=80.0)

    @field_validator("title", "route")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Value cannot be blank")
        return clean


class WorkflowConfigRequest(BaseModel):
    threshold: float = Field(ge=50.0, le=100.0, default=80.0)
    rules: Optional[List[WorkflowRuleItem]] = None


class WorkflowConfigResponse(BaseModel):
    threshold: float
    rules: List[WorkflowRuleItem]


class AddWorkflowRuleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    route: str = Field(min_length=1, max_length=150)
    type: str = Field(default="warning", pattern="^(warning|security|claims)$")
    threshold: float = Field(ge=50.0, le=100.0, default=80.0)
    document_type: Optional[str] = None

    @field_validator("title", "route")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Value cannot be blank")
        return clean
