from sqlalchemy.orm import Session

from app.db import base as _base_models  # noqa: F401
from app.db.session import Base


IGNORED_TABLES = {"alembic_version"}


def tenant_isolation_audit(db: Session) -> dict:
    tables = []
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        if table.name in IGNORED_TABLES:
            continue
        columns = {column.name: column for column in table.columns}
        has_tenant_id = "tenant_id" in columns
        issues = []
        warnings = []
        if not has_tenant_id:
            issues.append("missing_tenant_id")
        else:
            tenant_column = columns["tenant_id"]
            if tenant_column.nullable:
                warnings.append("tenant_id_nullable")
            if not any(index.columns.contains_column(tenant_column) for index in table.indexes):
                warnings.append("tenant_id_not_indexed")

        tables.append(
            {
                "table": table.name,
                "has_tenant_id": has_tenant_id,
                "tenant_id_nullable": bool(columns.get("tenant_id").nullable) if has_tenant_id else None,
                "tenant_id_indexed": not has_tenant_id or "tenant_id_not_indexed" not in issues,
                "status": "pass" if not issues else "fail",
                "issues": issues,
                "warnings": warnings,
            }
        )

    failed = [row for row in tables if row["status"] != "pass"]
    return {
        "status": "pass" if not failed else "fail",
        "table_count": len(tables),
        "failed_count": len(failed),
        "tables": tables,
    }
