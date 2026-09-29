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

    if user.role != "admin":
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
