from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic_core import ValidationError
from sqlmodel import or_, select

from app.web.api.dependencies import CurrentUser, SessionDep, CurrentActiveSuperuser
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from app.web.utils.logger import get_logger
from app.web.api.models import (
    AccessToken,
    NewPassword,
    NewPasswordWithCode,
    PasswordRecoveryCodeChallenge,
    PasswordRecoveryCodeRequest,
    PasswordRecoveryCodeVerified,
    PasswordRecoveryCodeVerify,
    Token,
    TokenPayload,
    User,
    UserOut,
    Message
)
from app.web.utils.template import (
    generate_password_reset_token,
    generate_password_verification_email,
    generate_reset_password_email,
    send_email,
    verify_password_reset_token,
)
from app.web.core.security import get_password_hash, verify_password, generate_token, ALGORITHM


router = APIRouter(tags=["login"])

logger = get_logger(__name__)

PASSWORD_RECOVERY_CODE_EXPIRE_MINUTES = 10
PASSWORD_RECOVERY_PURPOSE = "password-recovery-code"
PASSWORD_RESET_PURPOSE = "password-reset-code"


def _recovery_digest(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def _encode_recovery_token(payload: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            **payload,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=PASSWORD_RECOVERY_CODE_EXPIRE_MINUTES),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )


def _decode_recovery_token(token: str, purpose: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if payload.get("purpose") != purpose or not payload.get("sub"):
        return None
    return payload


@router.post("/login/token")
async def login_token(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    statement = select(User).where(
        or_(
            User.email == form_data.username,
            User.phone == form_data.username
        )
    )
    user = await session.scalar(statement)

    if not (user and verify_password(form_data.password, user.hashed_password)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    elif user.status != StatusTypes.ENABLE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    token = generate_token(user.id)

    return Token(access_token=token.access_token, refresh_token=token.refresh_token)


@router.post("/login/refresh-token")
async def login_refresh(refresh_token: str, session: SessionDep) -> AccessToken:
    """
    Refresh access token using the refresh token.
    """
    try:
        payload = jwt.decode(
            refresh_token, settings.SECRET_KEY, algorithms=[ALGORITHM]
        )

        token_data = TokenPayload(**payload)
    except (jwt.InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials"
        )
    
    if datetime.fromtimestamp(token_data.exp, tz=timezone.utc) < datetime.now(
        timezone.utc
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired"
        )

    user = await session.get(User, token_data.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user"
        )

    # 使用已验证的用户信息生成新的访问令牌
    token = generate_token(user.id)
    return AccessToken(access_token=token.access_token)


@router.post("/login/test-token", response_model=UserOut)
def test_token(current_user: CurrentUser) -> Any:
    """
    Test access token
    """
    return current_user


@router.post("/password-recovery/code", response_model=PasswordRecoveryCodeChallenge)
async def request_password_recovery_code(
    body: PasswordRecoveryCodeRequest,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> PasswordRecoveryCodeChallenge:
    """Send a short-lived password recovery verification code."""
    email = str(body.email).lower()
    user = await session.scalar(select(User).where(User.email == email))
    code = f"{secrets.randbelow(1_000_000):06d}"
    password_version = (
        _recovery_digest(user.hashed_password) if user else secrets.token_hex(32)
    )
    challenge_token = _encode_recovery_token(
        {
            "sub": email,
            "purpose": PASSWORD_RECOVERY_PURPOSE,
            "code": _recovery_digest(code),
            "password_version": password_version,
        }
    )

    if user and user.status == StatusTypes.ENABLE:
        email_data = generate_password_verification_email(
            email_to=user.email,
            code=code,
            valid_minutes=PASSWORD_RECOVERY_CODE_EXPIRE_MINUTES,
        )
        background_tasks.add_task(
            send_email,
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )

    return PasswordRecoveryCodeChallenge(
        message="If the account exists, a verification code has been sent.",
        challenge_token=challenge_token,
        expires_in=PASSWORD_RECOVERY_CODE_EXPIRE_MINUTES * 60,
    )


@router.post("/password-recovery/code/verify", response_model=PasswordRecoveryCodeVerified)
async def verify_password_recovery_code(
    body: PasswordRecoveryCodeVerify,
    session: SessionDep,
) -> PasswordRecoveryCodeVerified:
    """Verify a recovery code and exchange it for a password reset token."""
    payload = _decode_recovery_token(body.challenge_token, PASSWORD_RECOVERY_PURPOSE)
    invalid_code = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired verification code",
    )
    if not payload or not hmac.compare_digest(
        str(payload.get("code", "")), _recovery_digest(body.code)
    ):
        raise invalid_code

    user = await session.scalar(select(User).where(User.email == payload["sub"]))
    if (
        not user
        or user.status != StatusTypes.ENABLE
        or not hmac.compare_digest(
            str(payload.get("password_version", "")),
            _recovery_digest(user.hashed_password),
        )
    ):
        raise invalid_code

    reset_token = _encode_recovery_token(
        {
            "sub": user.email,
            "purpose": PASSWORD_RESET_PURPOSE,
            "password_version": _recovery_digest(user.hashed_password),
        }
    )
    return PasswordRecoveryCodeVerified(
        reset_token=reset_token,
        expires_in=PASSWORD_RECOVERY_CODE_EXPIRE_MINUTES * 60,
    )


@router.post("/reset-password/code")
async def reset_password_with_code(
    body: NewPasswordWithCode,
    session: SessionDep,
) -> Message:
    """Reset a password using a verified recovery-code token."""
    payload = _decode_recovery_token(body.reset_token, PASSWORD_RESET_PURPOSE)
    invalid_token = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired reset token",
    )
    if not payload:
        raise invalid_token

    user = await session.scalar(select(User).where(User.email == payload["sub"]))
    if (
        not user
        or user.status != StatusTypes.ENABLE
        or not hmac.compare_digest(
            str(payload.get("password_version", "")),
            _recovery_digest(user.hashed_password),
        )
    ):
        raise invalid_token

    user.hashed_password = get_password_hash(password=body.new_password)
    session.add(user)
    await session.commit()
    return Message(message="Password updated successfully")


@router.post("/password-recovery/{email}")
async def recover_password(email: str, session: SessionDep, background_tasks: BackgroundTasks) -> Message:
    """
    Password Recovery
    """
    statement = select(User).where(User.email == email)
    user = await session.scalar(statement)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The user with this email does not exist in the system."
        )
    
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )
    background_tasks.add_task(
        send_email,
        email_to=user.email,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Password recovery email sent")


@router.post("/reset-password/")
async def reset_password(session: SessionDep, body: NewPassword) -> Message:
    """
    Reset password
    """
    email = verify_password_reset_token(token=body.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token"
        )
    
    statement = select(User).where(
        or_(
            User.email == email,
            User.phone == email
        )
    )

    if not (user := await session.scalar(statement)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The user with this email does not exist in the system."
        )

    elif not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    
    hashed_password = get_password_hash(password=body.new_password)
    user.hashed_password = hashed_password
    session.add(user)
    await session.commit()
    return Message(message="Password updated successfully")


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(CurrentActiveSuperuser)],
    response_class=HTMLResponse,
)
async def recover_password_html_content(email: str, session: SessionDep) -> Any:
    """
    HTML Content for Password Recovery
    """
    statement = select(User).where(User.email == email)
    if not (user := await session.scalar(statement)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The user with this username does not exist in the system.",
        )
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )

    return HTMLResponse(
        content=email_data.html_content, headers={"subject:": email_data.subject}
    )
