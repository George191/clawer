from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import secrets
import math

import jwt
from cryptography.fernet import Fernet
from passlib.context import CryptContext

from app.web.core.config import settings


class TokenGenerator(ABC):
    
    @property
    @abstractmethod
    def access_token(self) -> str:
        pass

    @property
    @abstractmethod
    def refresh_token(self) -> str:
        pass


ALGORITHM = "HS256"
SESSION_LIFETIME = timedelta(hours=8)

class TokenType(Enum):
    ACCESS_TOKEN = "access"
    REFRESH_TOKEN = "refresh"


class JWTTokenGenerator(TokenGenerator):

    def __init__(self, payload: dict[str, Any], algorithm: str, session_expires_at: float | None = None):
        self.payload: dict[str, Any] = payload
        self.algorithm = algorithm
        self.issued_at = int(datetime.now(timezone.utc).timestamp())
        self.session_expires_at = (
            session_expires_at if session_expires_at is not None
            else self.issued_at + SESSION_LIFETIME.total_seconds()
        )

    @property
    def refresh_token(self):
        payload = self.payload.copy()
        payload.update({
            "iat": self.issued_at,
            "session_exp": self.session_expires_at,
            "exp": self.session_expires_at,
            "type": TokenType.REFRESH_TOKEN.value
        })

        return jwt.encode(payload, settings.SECRET_KEY, algorithm=self.algorithm)

    @property
    def access_token(self) -> str:
        payload = self.payload.copy()
        payload.update({
            "iat": self.issued_at,
            "session_exp": self.session_expires_at,
            "exp": min(self.issued_at + settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, self.session_expires_at),
            "type": TokenType.ACCESS_TOKEN.value
        })
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=self.algorithm)


def decode_session_token(token: str, token_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM],
                             options={"require": ["sub", "exp", "iat", "type", "session_exp"]})
    except (ValueError, TypeError, OverflowError) as error:
        raise jwt.InvalidTokenError("Invalid session claims") from error
    if payload["type"] != token_type.value or not isinstance(payload["sub"], str) or not payload["sub"]:
        raise jwt.InvalidTokenError("Invalid session token")
    if any(
        isinstance(payload[key], bool)
        or not isinstance(payload[key], (int, float))
        or not math.isfinite(payload[key])
        for key in ("exp", "iat", "session_exp")
    ):
        raise jwt.InvalidTokenError("Invalid session expiry")
    if (
        payload["exp"] > payload["session_exp"]
        or payload["session_exp"] > payload["iat"] + SESSION_LIFETIME.total_seconds()
        or payload["session_exp"] <= datetime.now(timezone.utc).timestamp()
    ):
        raise jwt.InvalidTokenError("Session has expired")
    return payload
    

class SecurityManager:

    def __init__(self):
        # 密码加密上下文
        self.pwd_context = CryptContext(
            schemes=["argon2", "bcrypt"], 
            deprecated="auto",
            argon2__time_cost=3,
            argon2__memory_cost=65536,
            argon2__parallelism=4,
            argon2__hash_len=32,
            argon2__salt_len=16
        )
        # Fernet加密配置（确保密钥持久化）
        self._fernet = Fernet(
            settings.MODEL_PROVIDER_ENCRYPTION_KEY or Fernet.generate_key().decode()
        )

    def create_token(
        self, subject: str | Any, token_generator: TokenGenerator | None = None, *, session_expires_at: float | None = None
    ) -> TokenGenerator:
        payload = {"sub": str(subject)}
        if token_generator is None:
            token_generator = JWTTokenGenerator(payload, ALGORITHM, session_expires_at)
        return token_generator
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        return self.pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        """获取密码哈希值"""
        return self.pwd_context.hash(password)

    def generate_apikey(self) -> str:
        """生成API密钥"""
        return secrets.token_urlsafe(32)

    def generate_short_apikey(self, key: str) -> str:
        """生成短格式API密钥"""
        return f"{key[:4]}...{key[-4:]}"

    def encrypt_api_key(self, data: str) -> str:
        """加密API密钥"""
        if not data:
            return data
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt_api_key(self, encrypted_data: str) -> str:
        """解密API密钥"""
        if not encrypted_data:
            return encrypted_data
        try:
            return self._fernet.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            raise ValueError("Decryption failed,Invalid API key Token") from e


# 创建单例实例
security_manager = SecurityManager()

generate_token = security_manager.create_token
verify_password = security_manager.verify_password
get_password_hash = security_manager.get_password_hash
generate_apikey = security_manager.generate_apikey
generate_short_apikey = security_manager.generate_short_apikey
