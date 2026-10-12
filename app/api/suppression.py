"""Suppression list API endpoints."""

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_clock, get_db
from app.api.schemas import PaginatedResponse, SuppressionCreate, SuppressionResponse
from app.core.clock import Clock
from app.models.suppression import Suppression
from app.services.suppression import add, export_to_csv, import_from_csv, remove

router = APIRouter(prefix="/suppression", tags=["Suppression"])


@router.get("", response_model=PaginatedResponse[SuppressionResponse])
def list_suppression(
    pagination: Annotated[Pagination, Depends()],
    search: Annotated[str | None, Query(description="Filter by email or domain substring")] = None,
    is_domain: Annotated[bool | None, Query(description="Filter by domain flag")] = None,
    db: Session = Depends(get_db),
) -> PaginatedResponse[SuppressionResponse]:
    """Retrieve paginated global suppression list."""
    query = select(Suppression)

    if search:
        query = query.where(Suppression.email.ilike(f"%{search}%"))
    if is_domain is not None:
        query = query.where(Suppression.is_domain == is_domain)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    records = db.scalars(
        query.order_by(Suppression.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    items = [SuppressionResponse.model_validate(r) for r in records]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.post("", response_model=SuppressionResponse, status_code=status.HTTP_201_CREATED)
def add_suppression_entry(
    payload: SuppressionCreate,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> SuppressionResponse:
    """Add email address or domain to global suppression."""
    record = add(
        db=db,
        target=payload.entry,
        reason=payload.reason or "manual suppression",
        clock=clock,
    )
    return SuppressionResponse.model_validate(record)


@router.delete("/{entry}")
def remove_suppression_entry(
    entry: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Remove email address or domain from global suppression."""
    success = remove(db=db, target=entry)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry '{entry}' not found in suppression list.",
        )
    return {"status": "removed", "entry": entry}


@router.post("/import")
async def import_suppression_file(
    file: Annotated[UploadFile, File(description="CSV or text file of emails/domains")],
    default_reason: Annotated[str, Query()] = "imported",
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Batch import suppressed emails and domains from CSV or plain-text file."""
    content = await file.read()
    try:
        text_data = content.decode("utf-8")
    except UnicodeDecodeError:
        text_data = content.decode("latin-1")

    imported_count = import_from_csv(
        db=db,
        csv_file=text_data,
        default_reason=default_reason,
        clock=clock,
    )
    return {"status": "success", "imported_count": imported_count}


@router.get("/export")
def export_suppression_csv(
    db: Session = Depends(get_db),
) -> Response:
    """Export entire suppression list as CSV file."""
    output = io.StringIO()
    export_to_csv(db=db, output_dest=output)
    csv_bytes = output.getvalue().encode("utf-8")

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="suppression_list.csv"'},
    )
