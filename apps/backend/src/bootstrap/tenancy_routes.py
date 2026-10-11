"""
Cross-module "who am I and what can I see" views.

These live in bootstrap, not in a module, because they span identity and
organizations - exactly the cross-module knowledge access.py already
owns. Both are read-only and scoped to the caller.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.organizations.models import Branch, Organization
from modules.organizations.schemas import OrganizationResponse

from .access import AccessContext, ScopeResolver, current_access
from .database import get_db

router = APIRouter(tags=["tenancy"])


class MyBranchResponse(BaseModel):
    id: str
    name: str
    timezone: str
    organization_id: str
    organization_name: str


@router.get("/auth/me/branches", response_model=list[MyBranchResponse])
def my_branches(
    ctx: AccessContext = Depends(current_access), db: Session = Depends(get_db)
) -> list[MyBranchResponse]:
    """Every branch the signed-in user may act on. Owners get every branch
    of their organization (including ones created later); other roles get
    their explicit grants; the platform operator gets everything."""
    ids = ScopeResolver(db).accessible_branch_ids(ctx)
    if not ids:
        return []
    rows = db.execute(
        select(Branch, Organization.legal_name)
        .join(Organization, Organization.id == Branch.organization_id)
        .where(Branch.id.in_(ids))
        .order_by(Organization.legal_name, Branch.name)
    ).all()
    return [
        MyBranchResponse(
            id=b.id, name=b.name, timezone=b.timezone,
            organization_id=b.organization_id, organization_name=org_name,
        )
        for b, org_name in rows
    ]


@router.get("/organizations", response_model=list[OrganizationResponse])
def list_my_organizations(
    ctx: AccessContext = Depends(current_access), db: Session = Depends(get_db)
) -> list[OrganizationResponse]:
    """Operator: every organization. Everyone else: their own."""
    query = select(Organization).order_by(Organization.created_at)
    if not ctx.is_operator:
        query = query.where(Organization.id == ctx.organization_id)
    return [OrganizationResponse.model_validate(o) for o in db.execute(query).scalars().all()]
