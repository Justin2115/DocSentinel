from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None


class UserInviteRequest(BaseModel):
    name: str
    email: str
    role: str = "UPLOAD_MAKER"
    department: Optional[str] = None
    password: Optional[str] = None


class UserRoleAssignRequest(BaseModel):
    role: str



class UserDepartmentAssignRequest(BaseModel):
    department: Optional[str] = None


class UserDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    role: str
    department: Optional[str] = None
    profile_picture: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
