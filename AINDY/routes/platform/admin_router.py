from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from AINDY.db.database import get_db
from AINDY.db.models.user import User
from AINDY.services.auth_service import require_admin_principal

router = APIRouter()


@router.get("/admin/users", response_model=None)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """List all registered users. Admin-only."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "is_admin": u.is_admin,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@router.post("/admin/users/{user_id}/promote", response_model=None)
def promote_user(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """Grant admin privileges to a user. Grant-only — never revokes."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_admin:
        return {
            "id": str(user.id),
            "email": user.email,
            "is_admin": True,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "already_admin": True,
        }
    user.is_admin = True
    db.commit()
    db.refresh(user)
    return {
        "id": str(user.id),
        "email": user.email,
        "is_admin": True,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "already_admin": False,
    }
