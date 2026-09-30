import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, require_admin
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AdminLoginRequest,
    GoogleAuthUrlResponse,
    MessageResponse,
    TokenResponse,
    UserResponse,
)
from app.core.rbac import Department, UserRole
from app.schemas.user import UserRoleAssignRequest, UserUpdateRequest
from app.services.auth_service import (
    authenticate_user_credentials,
    upsert_google_user,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get(
    "/google",
    response_model=GoogleAuthUrlResponse,
    summary="Get Google OAuth authorization URL",
)
async def get_google_auth_url(redirect: bool = Query(False)):
    """Generate the Google OAuth consent screen URL."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth is not configured on the server (missing GOOGLE_CLIENT_ID)",
        )

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    if redirect:
        return RedirectResponse(url=url)
    return GoogleAuthUrlResponse(url=url)


@router.get(
    "/google/callback",
    summary="Google OAuth redirect callback endpoint",
)
async def google_callback(
    code: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Handle the authorization code callback from Google OAuth."""
    frontend_url = settings.FRONTEND_URL.rstrip("/")

    if error or not code:
        err_msg = error or "Authorization cancelled or failed"
        logger.warning("Google OAuth error in callback: %s", err_msg)
        return RedirectResponse(
            url=f"{frontend_url}/login?error={err_msg}"
        )

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        logger.error("Missing Google OAuth credentials in backend configuration")
        return RedirectResponse(
            url=f"{frontend_url}/login?error=Google%20OAuth%20is%20not%20configured%20on%20the%20server"
        )

    # Exchange authorization code for tokens
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                },
                headers={"Accept": "application/json"},
            )

            if token_response.status_code != 200:
                logger.error(
                    "Google token exchange failed: %s - %s",
                    token_response.status_code,
                    token_response.text,
                )
                return RedirectResponse(
                    url=f"{frontend_url}/login?error=Failed%20to%20authenticate%20with%20Google"
                )

            tokens = token_response.json()
            access_token = tokens.get("access_token")
            if not access_token:
                logger.error("No access_token returned in Google response")
                return RedirectResponse(
                    url=f"{frontend_url}/login?error=Google%20did%20not%20return%20an%20access%20token"
                )

            # Retrieve user profile from Google
            userinfo_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if userinfo_response.status_code != 200:
                logger.error(
                    "Google userinfo request failed: %s - %s",
                    userinfo_response.status_code,
                    userinfo_response.text,
                )
                return RedirectResponse(
                    url=f"{frontend_url}/login?error=Failed%20to%20retrieve%20Google%20profile"
                )

            google_user_info = userinfo_response.json()
    except Exception as e:
        logger.exception("Error communicating with Google OAuth APIs: %s", e)
        return RedirectResponse(
            url=f"{frontend_url}/login?error=Network%20error%20during%20Google%20login"
        )

    if not google_user_info.get("email_verified"):
        return RedirectResponse(
            url=f"{frontend_url}/login?error=Google%20did%20not%20verify%20the%20account%20email"
        )

    # Upsert user in database
    try:
        user = upsert_google_user(db, google_user_info)
    except Exception as e:
        logger.exception("Failed to create or update Google user in DB: %s", e)
        return RedirectResponse(
            url=f"{frontend_url}/login?error=Database%20error%20creating%20user%20session"
        )

    # Create application JWT token
    jwt_token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "name": user.name,
    })

    # Redirect user to frontend callback page with the token
    return RedirectResponse(
        url=f"{frontend_url}/auth/callback?token={jwt_token}"
    )


@router.post(
    "/admin/login",
    response_model=TokenResponse,
    summary="Login with admin credentials",
)
async def admin_login(
    payload: AdminLoginRequest,
    db: Session = Depends(get_db),
):
    """Authenticate administrator using email and password."""
    user = authenticate_user_credentials(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_admin:
        logger.warning("Non-admin user %s attempted admin login.", payload.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Administrator privileges required",
        )


    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "name": user.name,
    })

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="General credentials login",
)
async def login_credentials(
    payload: AdminLoginRequest,
    db: Session = Depends(get_db),
):
    """General login for users with email and password."""
    user = authenticate_user_credentials(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "name": user.name,
    })

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="User logout",
)
async def logout(
    current_user: User = Depends(get_current_user),
):
    """Logout current user session."""
    return MessageResponse(message="Logged out successfully")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user profile",
)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
):
    """Return the profile information of the currently authenticated user."""
    return UserResponse.model_validate(current_user)


@router.get(
    "/admin/users",
    response_model=list[UserResponse],
    summary="Admin only: List all users in the system",
)
async def list_users_admin(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all registered users. Restricted to admin role."""
    users = db.query(User).order_by(User.id.asc()).all()
    return [UserResponse.model_validate(u) for u in users]


@router.patch(

    "/admin/users/{user_id}",
    response_model=UserResponse,
    summary="Admin only: Update a user's role, department, or active status",
)
async def update_user_admin(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found",
        )

    # Prevent admin from changing their own role (prevent lockout)
    if payload.role is not None and current_user.id == target_user.id:
        normalized_requested = UserRole.normalize(payload.role)
        if normalized_requested != UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrators cannot demote their own account role",
            )

    if payload.role is not None:
        normalized_role = UserRole.parse_assignment(payload.role)
        if normalized_role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role '{payload.role}'. Must be one of: {', '.join(valid_roles)}",
            )
        target_user.role = normalized_role

    if payload.department is not None:
        if payload.department.strip() == "":
            target_user.department = None
        else:
            normalized_dept = Department.normalize(payload.department)
            valid_depts = [d.value for d in Department]
            if normalized_dept not in valid_depts:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid department '{payload.department}'. Must be one of: {', '.join(valid_depts)}",
                )
            target_user.department = normalized_dept

    if payload.is_active is not None:
        if current_user.id == target_user.id and not payload.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrators cannot deactivate their own account",
            )
        target_user.is_active = payload.is_active

    if payload.name is not None and payload.name.strip():
        target_user.name = payload.name.strip()

    db.commit()
    db.refresh(target_user)
    return UserResponse.model_validate(target_user)


@router.patch(
    "/admin/users/{user_id}/role",
    response_model=UserResponse,
    summary="Admin only: Update a user's role",
)
async def update_user_role_admin(
    user_id: int,
    payload: UserRoleAssignRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return await update_user_admin(
        user_id=user_id,
        payload=UserUpdateRequest(role=payload.role),
        current_user=current_user,
        db=db,
    )


from app.api.admin import (
    create_workflow_rule,
    delete_user,
    get_permissions,
    get_workflow,
    invite_user,
    remove_workflow_rule,
    router as admin_router,
    update_permissions,
    update_workflow,
)

# Register admin routes under /api/auth/admin as well for full compatibility
router.add_api_route(
    "/admin/users/invite",
    invite_user,
    methods=["POST"],
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin only: Invite or create a new team member",
)

router.add_api_route(
    "/admin/users/{user_id}",
    delete_user,
    methods=["DELETE"],
    response_model=MessageResponse,
    summary="Admin only: Deactivate a member",
)

router.add_api_route(
    "/admin/permissions",
    get_permissions,
    methods=["GET"],
    summary="Admin only: Get permissions matrix",
)

router.add_api_route(
    "/admin/permissions",
    update_permissions,
    methods=["PUT"],
    summary="Admin only: Update permissions matrix",
)

router.add_api_route(
    "/admin/workflow",
    get_workflow,
    methods=["GET"],
    summary="Admin only: Get workflow rules",
)

router.add_api_route(
    "/admin/workflow",
    update_workflow,
    methods=["PUT"],
    summary="Admin only: Update workflow rules",
)

router.add_api_route(
    "/admin/workflow/rules",
    create_workflow_rule,
    methods=["POST"],
    summary="Admin only: Add workflow rule",
)

router.add_api_route(
    "/admin/workflow/rules/{rule_id}",
    remove_workflow_rule,
    methods=["DELETE"],
    summary="Admin only: Remove workflow rule",
)


