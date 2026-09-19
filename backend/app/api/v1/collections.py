"""Collections: folders of documents that share access rules."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.deps import AdminUser, CurrentUser, SessionDep
from app.db.models import Collection
from app.schemas.documents import CollectionCreate, CollectionOut
from app.services.retrieval.permissions import readable_collections

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionOut, status_code=201)
async def create_collection(
    body: CollectionCreate, user: AdminUser, session: SessionDep
) -> Collection:
    collection = Collection(
        tenant_id=user.tenant_id,  # from the authenticated user, never the request
        name=body.name.strip(),
        description=body.description,
        visibility=body.visibility,
    )
    session.add(collection)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A collection with this name exists"
        ) from None
    await session.refresh(collection)
    return collection


@router.get("", response_model=list[CollectionOut])
async def list_collections(user: CurrentUser, session: SessionDep) -> list[Collection]:
    return await readable_collections(session, user)
