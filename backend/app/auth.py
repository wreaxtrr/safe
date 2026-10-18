import os

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def get_current_user(x_token: str = Header(), db: Session = Depends(get_db)) -> User:
    user = db.scalars(select(User).where(User.token == x_token)).first()
    if not user:
        raise HTTPException(401, "Неизвестный токен")
    return user


def require_admin(x_admin_key: str = Header()):
    if x_admin_key != os.getenv("ADMIN_KEY"):
        raise HTTPException(403, "Нужен ключ администратора")
