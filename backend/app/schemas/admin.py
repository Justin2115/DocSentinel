from datetime import datetime
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
    department: Optional[str] = None
    threshold: float = Field(ge=50.0, le=100.0, default=80.0)
    condition: str = Field(default="confidence_below_threshold")

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
    can_edit_threshold: bool = True


class WorkflowOptionItem(BaseModel):
    value: str
    label: str
    department: Optional[str] = None
    kind: Optional[str] = None


class WorkflowOptionsResponse(BaseModel):
    departments: List[WorkflowOptionItem]
    document_types: List[WorkflowOptionItem]
    routes: List[WorkflowOptionItem]
    rule_types: List[WorkflowOptionItem]
    conditions: List[WorkflowOptionItem]
    priority_supported: bool = False
    can_edit_threshold: bool = True
    selected_department: Optional[str] = None


class AddWorkflowRuleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    route: str = Field(min_length=1, max_length=150)
    type: str = Field(default="warning", pattern="^(warning|security|claims)$")
    threshold: float = Field(ge=50.0, le=100.0, default=80.0)
    document_type: Optional[str] = None
    department: Optional[str] = None
    condition: Optional[str] = None

    @field_validator("title", "route")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Value cannot be blank")
        return clean


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    actor_id: Optional[int] = None
    username: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    action: str
    target_resource: Optional[str] = None
    status: str
    failure_reason: Optional[str] = None
    correlation_id: Optional[str] = None
    ip_address: Optional[str] = None
    details: Optional[str] = None


class ActivityLogListResponse(BaseModel):
    items: List[ActivityLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
