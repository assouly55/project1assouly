"""
Tender AI Platform - Authentication Routes
Admin and Client auth endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timezone
from uuid import UUID

from app.core.database import get_db
from app.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    require_admin,
    require_client,
)
from app.models.user import AdminUser, ClientUser

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ============================
# PYDANTIC SCHEMAS
# ============================

class LoginRequest(BaseModel):
    email: str
    password: str


class AdminCreateRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None
    role: str = "admin"


class ClientRegisterRequest(BaseModel):
    email: str
    password: str
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None


class ClientUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class ClientAccountResponse(BaseModel):
    id: str
    email: str
    company_name: Optional[str]
    contact_name: Optional[str]
    phone: Optional[str]
    is_active: bool
    is_approved: bool
    created_at: str
    last_login: Optional[str]


# ============================
# ADMIN AUTH ENDPOINTS
# ============================

@auth_router.post("/admin/login", response_model=TokenResponse)
def admin_login(request: LoginRequest, db: Session = Depends(get_db)):
    """Admin login with email/username + password"""
    user = db.query(AdminUser).filter(
        (AdminUser.email == request.email) | (AdminUser.username == request.email)
    ).first()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account suspended")

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(
        str(user.id), "admin",
        {"username": user.username, "role": user.role}
    )

    return TokenResponse(
        access_token=token,
        user={
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        }
    )


@auth_router.get("/admin/me")
def admin_me(claims: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Get current admin user info"""
    user = db.query(AdminUser).filter(AdminUser.id == claims["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
    }


@auth_router.post("/admin/create-admin")
def create_admin(
    request: AdminCreateRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new admin user (requires existing admin)"""
    # Check if username/email already exists
    existing = db.query(AdminUser).filter(
        (AdminUser.username == request.username) | (AdminUser.email == request.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    admin = AdminUser(
        username=request.username,
        email=request.email,
        password_hash=hash_password(request.password),
        full_name=request.full_name,
        role=request.role,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    return {"id": str(admin.id), "username": admin.username, "email": admin.email}


@auth_router.post("/admin/seed")
def seed_admin(db: Session = Depends(get_db)):
    """Create default admin if none exists (first-time setup)"""
    existing = db.query(AdminUser).first()
    if existing:
        raise HTTPException(status_code=400, detail="Admin already exists")

    admin = AdminUser(
        username="admin",
        email="admin@tenderai.ma",
        password_hash=hash_password("admin123"),
        full_name="System Administrator",
        role="super_admin",
    )
    db.add(admin)
    db.commit()
    return {"message": "Default admin created", "username": "admin", "password": "admin123"}


# ============================
# CLIENT AUTH ENDPOINTS
# ============================

@auth_router.post("/client/login", response_model=TokenResponse)
def client_login(request: LoginRequest, db: Session = Depends(get_db)):
    """Client login with email + password"""
    user = db.query(ClientUser).filter(ClientUser.email == request.email).first()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account suspended")

    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Account pending approval")

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(
        str(user.id), "client",
        {"email": user.email, "company": user.company_name}
    )

    return TokenResponse(
        access_token=token,
        user={
            "id": str(user.id),
            "email": user.email,
            "company_name": user.company_name,
            "contact_name": user.contact_name,
            "phone": user.phone,
        }
    )


@auth_router.post("/client/register")
def client_register(request: ClientRegisterRequest, db: Session = Depends(get_db)):
    """Client self-registration (pending admin approval)"""
    existing = db.query(ClientUser).filter(ClientUser.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    client = ClientUser(
        email=request.email,
        password_hash=hash_password(request.password),
        company_name=request.company_name,
        contact_name=request.contact_name,
        phone=request.phone,
        is_approved=False,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    return {
        "message": "Registration successful. Your account is pending admin approval.",
        "id": str(client.id),
    }


@auth_router.get("/client/me")
def client_me(claims: dict = Depends(require_client), db: Session = Depends(get_db)):
    """Get current client user info"""
    user = db.query(ClientUser).filter(ClientUser.id == claims["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(user.id),
        "email": user.email,
        "company_name": user.company_name,
        "contact_name": user.contact_name,
        "phone": user.phone,
    }


@auth_router.put("/client/profile")
def update_client_profile(
    request: ClientUpdateRequest,
    claims: dict = Depends(require_client),
    db: Session = Depends(get_db)
):
    """Update client profile"""
    user = db.query(ClientUser).filter(ClientUser.id == claims["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if request.company_name is not None:
        user.company_name = request.company_name
    if request.contact_name is not None:
        user.contact_name = request.contact_name
    if request.phone is not None:
        user.phone = request.phone
    if request.email is not None:
        user.email = request.email

    db.commit()
    db.refresh(user)

    return {
        "id": str(user.id),
        "email": user.email,
        "company_name": user.company_name,
        "contact_name": user.contact_name,
        "phone": user.phone,
    }


# ============================
# ADMIN: CLIENT MANAGEMENT
# ============================

@auth_router.get("/admin/clients", response_model=List[ClientAccountResponse])
def list_clients(claims: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """List all client accounts"""
    clients = db.query(ClientUser).order_by(ClientUser.created_at.desc()).all()
    return [
        ClientAccountResponse(
            id=str(c.id),
            email=c.email,
            company_name=c.company_name,
            contact_name=c.contact_name,
            phone=c.phone,
            is_active=c.is_active,
            is_approved=c.is_approved,
            created_at=c.created_at.isoformat() if c.created_at else "",
            last_login=c.last_login.isoformat() if c.last_login else None,
        )
        for c in clients
    ]


@auth_router.post("/admin/clients/{client_id}/approve")
def approve_client(
    client_id: str,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Approve a client account"""
    client = db.query(ClientUser).filter(ClientUser.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.is_approved = True
    db.commit()
    return {"message": "Client approved"}


@auth_router.post("/admin/clients/{client_id}/suspend")
def suspend_client(
    client_id: str,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Suspend/unsuspend a client account"""
    client = db.query(ClientUser).filter(ClientUser.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.is_active = not client.is_active
    db.commit()
    return {"message": f"Client {'suspended' if not client.is_active else 'reactivated'}"}


@auth_router.delete("/admin/clients/{client_id}")
def delete_client(
    client_id: str,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Delete a client account"""
    client = db.query(ClientUser).filter(ClientUser.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    db.delete(client)
    db.commit()
    return {"message": "Client deleted"}


@auth_router.post("/admin/clients/create")
def admin_create_client(
    request: ClientRegisterRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin creates a client account (pre-approved)"""
    existing = db.query(ClientUser).filter(ClientUser.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    client = ClientUser(
        email=request.email,
        password_hash=hash_password(request.password),
        company_name=request.company_name,
        contact_name=request.contact_name,
        phone=request.phone,
        is_approved=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    return {"id": str(client.id), "email": client.email, "message": "Client created and approved"}


# ============================
# ADMIN: STATS ENDPOINTS
# ============================

@auth_router.get("/admin/stats/overview")
def admin_stats_overview(claims: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Get overview statistics for admin dashboard"""
    from app.models.tender import Tender, TenderStatus, ScraperJob

    total_tenders = db.query(func.count(Tender.id)).scalar() or 0
    analyzed_tenders = db.query(func.count(Tender.id)).filter(Tender.status == TenderStatus.ANALYZED).scalar() or 0
    pending_tenders = db.query(func.count(Tender.id)).filter(Tender.status == TenderStatus.PENDING).scalar() or 0
    error_tenders = db.query(func.count(Tender.id)).filter(Tender.status == TenderStatus.ERROR).scalar() or 0

    total_clients = db.query(func.count(ClientUser.id)).scalar() or 0
    active_clients = db.query(func.count(ClientUser.id)).filter(ClientUser.is_active == True, ClientUser.is_approved == True).scalar() or 0
    pending_clients = db.query(func.count(ClientUser.id)).filter(ClientUser.is_approved == False).scalar() or 0

    # Scraper jobs
    total_jobs = db.query(func.count(ScraperJob.id)).scalar() or 0
    completed_jobs = db.query(func.count(ScraperJob.id)).filter(ScraperJob.status == "COMPLETED").scalar() or 0
    failed_jobs = db.query(func.count(ScraperJob.id)).filter(ScraperJob.status == "FAILED").scalar() or 0

    # Recent jobs
    recent_jobs = db.query(ScraperJob).order_by(ScraperJob.started_at.desc()).limit(10).all()

    return {
        "tenders": {
            "total": total_tenders,
            "analyzed": analyzed_tenders,
            "pending": pending_tenders,
            "error": error_tenders,
        },
        "clients": {
            "total": total_clients,
            "active": active_clients,
            "pending_approval": pending_clients,
        },
        "scraper": {
            "total_jobs": total_jobs,
            "completed": completed_jobs,
            "failed": failed_jobs,
            "success_rate": round(completed_jobs / total_jobs * 100, 1) if total_jobs > 0 else 0,
        },
        "recent_jobs": [
            {
                "id": str(j.id),
                "target_date": j.target_date,
                "status": j.status,
                "total_found": j.total_found,
                "downloaded": j.downloaded,
                "failed": j.failed,
                "elapsed_seconds": j.elapsed_seconds,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in recent_jobs
        ],
    }


@auth_router.get("/admin/stats/tenders-by-date")
def tenders_by_date(claims: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Get tender count grouped by download date"""
    from app.models.tender import Tender

    results = (
        db.query(Tender.download_date, func.count(Tender.id))
        .filter(Tender.download_date.isnot(None))
        .group_by(Tender.download_date)
        .order_by(Tender.download_date.desc())
        .limit(30)
        .all()
    )

    return [{"date": r[0], "count": r[1]} for r in results]


@auth_router.get("/admin/stats/categories")
def tenders_by_category(claims: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Get tender count by category"""
    from app.models.tender import Tender

    tenders = db.query(Tender).filter(Tender.categories.isnot(None)).all()
    category_counts = {}

    for t in tenders:
        if t.categories:
            for cat in t.categories:
                main = cat.get("main_category", "Inconnu")
                category_counts[main] = category_counts.get(main, 0) + 1

    return [{"category": k, "count": v} for k, v in sorted(category_counts.items(), key=lambda x: -x[1])]
