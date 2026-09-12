import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from backend.config import JWT_HOURS, JWT_SECRET
from backend.database.models import User
from backend.database.session import get_db

bearer = HTTPBearer(auto_error=False)
ITERATIONS = 120_000


def hash_password(password, salt=None):
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt_bytes, ITERATIONS)
    return salt_bytes.hex() + '$' + digest.hex()


def verify_password(password, stored):
    try:
        salt, _digest = stored.split('$', 1)
        candidate = hash_password(password, salt)
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, stored)


def create_token(user):
    payload = {
        'sub': user.id,
        'role': user.role,
        'name': user.full_name,
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, 'Invalid or expired session') from exc


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    if creds is None:
        raise HTTPException(401, 'Sign in required')
    payload = decode_token(creds.credentials)
    user = db.get(User, payload['sub'])
    if user is None or not user.is_active:
        raise HTTPException(401, 'Account is not active')
    return user


def require_roles(*roles):
    def checker(user: User = Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, 'You do not have access to this action')
        return user
    return checker


def new_id(prefix=''):
    return prefix + uuid.uuid4().hex[:12]
