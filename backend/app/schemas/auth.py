from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
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



class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class GoogleAuthUrlResponse(BaseModel):
    url: str


class MessageResponse(BaseModel):
    message: str
