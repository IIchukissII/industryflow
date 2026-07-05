# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
REST API endpoints for alert history management with schema-per-tenant routing
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta
from pydantic import BaseModel
from enum import Enum

from dependencies import get_db_with_tenant, get_current_user_with_company
from models.user import User

router = APIRouter(prefix="/api/alerts", tags=["Alerts History"])


# Local models
class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Verdict(str, Enum):
    """Operator correctness verdict on a fired alert (ADR-0022 decision 1)."""
    true_positive = "true_positive"
    false_positive = "false_positive"
    unsure = "unsure"


class LabelRequest(BaseModel):
    verdict: Verdict
    note: Optional[str] = None


class Bucket(str, Enum):
    """Time-bucket granularity for label metrics over time."""
    day = "day"
    week = "week"


# asyncpg (the gateway's driver) binds an interval param from a timedelta, not a string — a
# string reaches the CAST as text and errors ("'str' object has no attribute 'days'").
_BUCKET_INTERVAL = {"day": timedelta(days=1), "week": timedelta(days=7)}


def _precision(tp: int, fp: int) -> Optional[float]:
    """Precision = TP / (TP + FP), or None when nothing decisive was labelled.

    'unsure' verdicts are intentionally excluded from the denominator — they are neither a
    confirmed hit nor a confirmed miss.
    """
    decided = tp + fp
    return round(tp / decided, 4) if decided else None


def _false_positive_rate(tp: int, fp: int) -> Optional[float]:
    """FP / (TP + FP), or None when nothing decisive was labelled."""
    decided = tp + fp
    return round(fp / decided, 4) if decided else None


class PrecisionBucket(BaseModel):
    bucket: datetime
    true_positive: int
    false_positive: int
    unsure: int
    precision: Optional[float]


class PrecisionSummary(BaseModel):
    true_positive: int
    false_positive: int
    unsure: int
    labeled_total: int
    precision: Optional[float]
    false_positive_rate: Optional[float]


class PrecisionMetrics(BaseModel):
    window_days: int
    bucket: str
    model_id: Optional[str]
    rule_id: Optional[str]
    overall: PrecisionSummary
    series: List[PrecisionBucket]
    # Honest scope (ADR-0022 dec 3): recall is not derivable from fired-alert labels.
    note: str


class AlertResponse(BaseModel):
    alert_id: str
    rule_id: Optional[str]  # nullable: not every alert is bound to a rule (a NULL must not 500 the list)
    sensor_id: Optional[str]
    equipment_id: Optional[str]
    sensor_name: Optional[str]
    equipment_name: Optional[str]
    company_id: str
    severity: str
    message: str
    actual_value: Optional[float]
    threshold_value: Optional[float]
    anomaly_score: Optional[float]
    model_id: Optional[str]      # set for ml/statistical alerts (incl. drift + retrain recommendations)
    condition: Optional[str]     # e.g. 'drift', 'retrain_recommended' (ADR-0021/0022)
    triggered_at: datetime
    acknowledged: bool
    acknowledged_at: Optional[datetime]
    acknowledged_by: Optional[str]
    # Operator feedback (ADR-0022) — null until an operator labels the alert.
    label_verdict: Optional[str] = None
    labeled_at: Optional[datetime] = None
    labeled_by: Optional[str] = None


# Shared SELECT for the three alert-list endpoints: the alert row + joined sensor/equipment
# names + the operator label. Callers append a WHERE and params (see _fetch_alerts).
_ALERT_SELECT = """
    SELECT
        a.alert_id::text, a.rule_id::text, a.sensor_id::text, a.equipment_id::text,
        s.sensor_name, e.name AS equipment_name,
        a.severity, a.message, a.actual_value, a.threshold_value, a.anomaly_score,
        a.model_id::text, a.condition,
        a.triggered_at, a.acknowledged, a.acknowledged_at, a.acknowledged_by,
        al.verdict AS label_verdict, al.labeled_at, al.labeled_by
    FROM alerts a
    LEFT JOIN sensors s ON a.sensor_id = s.sensor_id
    LEFT JOIN equipment e ON a.equipment_id = e.equipment_id
    LEFT JOIN alert_labels al ON a.alert_id = al.alert_id
"""


async def _fetch_alerts(db, company_id, where_sql, params):
    """Run _ALERT_SELECT with an optional WHERE (newest first) and stamp company_id on each row."""
    query = _ALERT_SELECT + (f" WHERE {where_sql}" if where_sql else "") + " ORDER BY a.triggered_at DESC LIMIT :limit"
    rows = (await db.execute(text(query), params)).fetchall()
    out = []
    for row in rows:
        d = dict(row._mapping)
        d["company_id"] = str(company_id)
        out.append(d)
    return out


@router.get("", response_model=List[AlertResponse])
async def list_alerts(
        sensor_id: Optional[str] = None,
        equipment_id: Optional[str] = None,
        severity: Optional[Severity] = None,
        acknowledged: Optional[bool] = None,
        condition: Optional[str] = None,
        model_id: Optional[str] = None,
        limit: int = 100,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """List recent alerts for the authenticated user's company (schema-routed).

    Filters: sensor_id, equipment_id, severity, acknowledged, condition (e.g. 'drift',
    'retrain_recommended' — ADR-0021/0022), model_id. limit default 100, capped at 1000.
    """
    limit = min(limit, 1000)
    clauses, params = [], {"limit": limit}
    # UUID columns compared as text so a string query param binds cleanly under asyncpg.
    for col, val in (("sensor_id", sensor_id), ("equipment_id", equipment_id), ("model_id", model_id)):
        if val:
            clauses.append(f"a.{col}::text = :{col}")
            params[col] = val
    if condition:
        clauses.append("a.condition = :condition")
        params["condition"] = condition
    if severity:
        clauses.append("a.severity = :severity")
        params["severity"] = severity.value
    if acknowledged is not None:
        clauses.append("a.acknowledged = :acknowledged")
        params["acknowledged"] = acknowledged
    return await _fetch_alerts(db, current_user.company_id, " AND ".join(clauses), params)


@router.get("/unacknowledged", response_model=List[AlertResponse])
async def list_unacknowledged_alerts(
        limit: int = 50,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get unacknowledged alerts for the authenticated user's company (schema-routed)"""
    return await _fetch_alerts(db, current_user.company_id, "a.acknowledged = FALSE", {"limit": limit})


@router.get("/critical", response_model=List[AlertResponse])
async def list_critical_alerts(
        limit: int = 50,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get critical alerts for the authenticated user's company (schema-routed)"""
    return await _fetch_alerts(db, current_user.company_id, "a.severity = 'critical'", {"limit": limit})


@router.patch("/{alert_id}/acknowledge")
async def acknowledge_alert(
        alert_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Acknowledge an alert (schema-routed)"""
    result = await db.execute(text("""
        UPDATE alerts 
        SET acknowledged = TRUE,
            acknowledged_at = NOW(),
            acknowledged_by = :user_id
        WHERE alert_id = :alert_id
        RETURNING *
    """), {
        "alert_id": alert_id,
        "user_id": str(current_user.id)
    })
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    await db.commit()

    return {
        "message": "Alert acknowledged successfully",
        "alert": dict(row._mapping)
    }


@router.patch("/{alert_id}/label")
async def label_alert(
        alert_id: UUID,
        body: LabelRequest,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Record an operator's correctness verdict on a fired alert (ADR-0022 decision 1).

    Distinct from acknowledge: acknowledge is "seen", this is "was it real?". The verdict
    lands in the separate alert_labels table (not on the alerts hypertable) so it outlives
    the alert's 90-day retention, denormalizing the alert's rule/model/detection/severity so
    it stays meaningful after the alert ages out. One re-labelable verdict per alert.
    """
    # Resolve the alert first so we can denormalize its fields onto the label — and 404 if the
    # operator is labelling something that isn't there.
    alert_row = (await db.execute(text("""
        SELECT triggered_at, rule_id, model_id, detection_type, severity
        FROM alerts
        WHERE alert_id = :alert_id
    """), {"alert_id": alert_id})).fetchone()

    if not alert_row:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert = dict(alert_row._mapping)

    result = await db.execute(text("""
        INSERT INTO alert_labels
            (alert_id, triggered_at, verdict, note, rule_id, model_id,
             detection_type, severity, labeled_by, labeled_at)
        VALUES
            (:alert_id, :triggered_at, :verdict, :note, :rule_id, :model_id,
             :detection_type, :severity, :labeled_by, NOW())
        ON CONFLICT (alert_id) DO UPDATE SET
            verdict = EXCLUDED.verdict,
            note = EXCLUDED.note,
            labeled_by = EXCLUDED.labeled_by,
            labeled_at = NOW()
        RETURNING *
    """), {
        "alert_id": alert_id,
        "triggered_at": alert["triggered_at"],
        "verdict": body.verdict.value,
        "note": body.note,
        "rule_id": alert["rule_id"],
        "model_id": alert["model_id"],
        "detection_type": alert["detection_type"],
        "severity": alert["severity"],
        "labeled_by": str(current_user.id),
    })

    await db.commit()

    return {
        "message": "Alert labelled successfully",
        "label": dict(result.fetchone()._mapping),
    }


@router.get("/label-metrics", response_model=PrecisionMetrics)
async def alert_label_metrics(
        model_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        days: int = 30,
        bucket: Bucket = Bucket.day,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Precision + false-positive rate over time from operator alert labels (ADR-0022 dec 3).

    Buckets the labelled fired alerts by time and reports, per bucket and overall, precision
    = TP/(TP+FP) and the false-positive rate. Optionally scoped to one model or rule.

    Recall is deliberately NOT reported: fired-alert labels carry no false-negative signal
    (we never see the anomalies that didn't fire), so a recall number here would be a fiction
    (ADR-0022). The response says so in `note`.
    """
    days = max(1, min(days, 365))

    where = ["labeled_at >= NOW() - make_interval(days => :days)"]
    params = {"days": days, "interval": _BUCKET_INTERVAL[bucket.value]}
    if model_id:
        where.append("model_id = :model_id")
        params["model_id"] = model_id
    if rule_id:
        where.append("rule_id = :rule_id")
        params["rule_id"] = rule_id

    query = f"""
        SELECT
            time_bucket(:interval, labeled_at) AS bucket,
            count(*) FILTER (WHERE verdict = 'true_positive')  AS tp,
            count(*) FILTER (WHERE verdict = 'false_positive') AS fp,
            count(*) FILTER (WHERE verdict = 'unsure')         AS unsure
        FROM alert_labels
        WHERE {" AND ".join(where)}
        GROUP BY bucket
        ORDER BY bucket
    """

    rows = (await db.execute(text(query), params)).fetchall()

    series = []
    tot_tp = tot_fp = tot_unsure = 0
    for row in rows:
        m = row._mapping
        tp, fp, unsure = m["tp"], m["fp"], m["unsure"]
        tot_tp += tp
        tot_fp += fp
        tot_unsure += unsure
        series.append(PrecisionBucket(
            bucket=m["bucket"], true_positive=tp, false_positive=fp, unsure=unsure,
            precision=_precision(tp, fp),
        ))

    overall = PrecisionSummary(
        true_positive=tot_tp, false_positive=tot_fp, unsure=tot_unsure,
        labeled_total=tot_tp + tot_fp + tot_unsure,
        precision=_precision(tot_tp, tot_fp),
        false_positive_rate=_false_positive_rate(tot_tp, tot_fp),
    )

    return PrecisionMetrics(
        window_days=days, bucket=bucket.value, model_id=model_id, rule_id=rule_id,
        overall=overall, series=series,
        note=("Precision and false-positive rate only; recall is not derivable from "
              "fired-alert labels (no false-negative signal) — ADR-0022."),
    )
