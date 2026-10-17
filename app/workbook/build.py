"""Provider workbook builder — two-tab Excel file (openpyxl).

Columns per KICKOFF.md §7:
Provider Name | Service Type | Google Rating & # Reviews | Key Strengths |
Contact / Website | Pre-registration Requirements | Est. Clearance Time |
Notes / Preferred Status
"""

from __future__ import annotations

import csv
import logging
import pathlib
import uuid

from app.domain.cdm import Provider, RiskAssessment, Workbook

logger = logging.getLogger(__name__)

_SEED_CSV = pathlib.Path(__file__).parent.parent.parent / "data" / "providers_seed.csv"
_WORKBOOKS_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "workbooks"


def _load_seed_providers(port: str) -> list[Provider]:
    providers: list[Provider] = []
    if not _SEED_CSV.exists():
        logger.warning("Seed CSV not found at %s", _SEED_CSV)
        return providers
    with open(_SEED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("port", "").strip().lower() == port.lower():
                providers.append(
                    Provider(
                        id=str(uuid.uuid4()),
                        name=row.get("name", ""),
                        port=row.get("port", ""),
                        service_type=row.get("service_type", ""),
                        google_rating=(
                            float(row["google_rating"])
                            if row.get("google_rating")
                            else None
                        ),
                        google_review_count=(
                            int(row["google_review_count"])
                            if row.get("google_review_count")
                            else None
                        ),
                        key_strengths=[
                            s.strip()
                            for s in row.get("key_strengths", "").split(";")
                            if s.strip()
                        ],
                        contact_website=row.get("contact_website") or None,
                        pre_registration_requirements=(
                            row.get("pre_registration_requirements") or "TBC with provider"
                        ),
                        est_clearance_time=(
                            row.get("est_clearance_time") or "TBC with provider"
                        ),
                        notes=row.get("notes") or None,
                        preferred=row.get("preferred", "").lower() in ("true", "1", "yes"),
                        source="seed_csv",
                    )
                )
    return providers


def _build_xlsx(workbook: Workbook) -> pathlib.Path:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()

    header_fill = PatternFill("solid", fgColor="071216")
    header_font = Font(bold=True, color="2CECC0")

    COLS = [
        "Provider Name",
        "Service Type",
        "Google Rating",
        "Reviews",
        "Key Strengths",
        "Contact / Website",
        "Pre-registration Requirements",
        "Est. Clearance Time",
        "Notes / Preferred Status",
    ]
    WIDTHS = [28, 20, 12, 8, 40, 30, 30, 18, 30]

    for sheet_name, providers in [
        ("Jeddah Providers", workbook.jeddah_providers),
        ("Salalah Providers", workbook.salalah_providers),
    ]:
        ws = wb.create_sheet(title=sheet_name)

        for col_idx, col_name in enumerate(COLS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(wrap_text=True)

        for row_idx, p in enumerate(providers, start=2):
            status = "★ Preferred" if p.preferred else ""
            notes_parts = [p.notes or "", status]
            ws.cell(row=row_idx, column=1, value=p.name)
            ws.cell(row=row_idx, column=2, value=p.service_type)
            ws.cell(row=row_idx, column=3, value=p.google_rating)
            ws.cell(row=row_idx, column=4, value=p.google_review_count)
            ws.cell(row=row_idx, column=5, value="; ".join(p.key_strengths))
            ws.cell(row=row_idx, column=6, value=p.contact_website)
            ws.cell(row=row_idx, column=7, value=p.pre_registration_requirements)
            ws.cell(row=row_idx, column=8, value=p.est_clearance_time)
            ws.cell(
                row=row_idx,
                column=9,
                value=" · ".join(filter(None, notes_parts)),
            )

        footer_row = len(providers) + 2
        ws.merge_cells(
            start_row=footer_row,
            start_column=1,
            end_row=footer_row,
            end_column=len(COLS),
        )
        footer_cell = ws.cell(row=footer_row, column=1, value=workbook.spatial_footer_label)
        footer_cell.font = Font(italic=True, color="888888")

        for col_idx, width in enumerate(WIDTHS, start=1):
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = width

    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    _WORKBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _WORKBOOKS_DIR / f"workbook_{workbook.shipment_id[:8]}.xlsx"
    wb.save(str(out_path))
    return out_path


async def build(assessment: RiskAssessment) -> Workbook:
    """Load seed providers and build the two-tab workbook."""
    jeddah_providers = _load_seed_providers("Jeddah")
    salalah_providers = _load_seed_providers("Salalah")

    def _sort_key(p: Provider) -> tuple:
        return (not p.preferred, p.name)

    jeddah_providers.sort(key=_sort_key)
    salalah_providers.sort(key=_sort_key)

    workbook = Workbook(
        shipment_id=assessment.shipment_id,
        assessment_id=assessment.id,
        jeddah_providers=jeddah_providers,
        salalah_providers=salalah_providers,
    )

    try:
        out_path = _build_xlsx(workbook)
        workbook.file_path = str(out_path)
        logger.info("Workbook written to %s", out_path)
    except Exception as exc:
        logger.warning("Workbook XLSX build failed: %s", exc)

    return workbook
