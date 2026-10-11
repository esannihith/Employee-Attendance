"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError
import calendar
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Annotated, Literal
from decimal import Decimal, ROUND_HALF_UP

from bson import json_util


from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import MongoClient, ReturnDocument

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    
    yield
    client.close()
    
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: Optional[str] = None) -> int:
    h, m = map(int, shift_start.split(":"))
    if shift_end and shift_end <= shift_start and punch_in.strftime("%H:%M") < shift_end:
        start = (punch_in - timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
    else:
        start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    seconds = int((punch_in - start).total_seconds())
    return seconds // 60 if seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_start : str, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    if shift_end <= shift_start:
        end += timedelta(days=1)
    minutes = int((punch_out - end).total_seconds()) // 60
    return minutes if minutes >= 30 else 0


def serialize_attendance(doc: dict) -> dict:
    doc.pop("_id", None)
    doc.pop("id", None)
    doc.setdefault("half_day", False)
    doc.setdefault("history", [])
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    if isinstance(doc.get("punch_in"), datetime):
        doc["punch_in"] = int(doc["punch_in"].timestamp() * 1000)
    if isinstance(doc.get("punch_out"), datetime):
        doc["punch_out"] = int(doc["punch_out"].timestamp() * 1000)
    for h in doc.get("history", []):
        if isinstance(h.get("at"), datetime):
            h["at"] = int(h["at"].timestamp() * 1000)
        for change in h.get("changes", {}).values():
            if isinstance(change, dict):
                if isinstance(change.get("from"), datetime):
                    change["from"] = int(change["from"].timestamp() * 1000)
                if isinstance(change.get("to"), datetime):
                    change["to"] = int(change["to"].timestamp() * 1000)
    return doc 


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)
    status: str = "PRESENT"

class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)

class RegularizeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)
    punch_out: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health", status_code = 200)
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "MongoDB is not reachable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end cannot be same")
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    created_at = datetime.now(timezone.utc)
    doc["created_at"] = created_at
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] =  int(created_at.timestamp() * 1000)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1)* page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code").skip(skip).limit(page_size))
    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = int(item["created_at"].timestamp() * 1000)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    status = body.status
    if status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(422, "Invalid status for punch-in")

    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    ts_ist = ts.astimezone(IST)
    if emp["shift_end"] <= emp["shift_start"] and ts_ist.strftime("%H:%M") < emp["shift_end"]:
        d = (ts_ist - timedelta(days=1)).date().isoformat()
    else:
        d = ts_ist.date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts_ist, emp["shift_start"], emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return serialize_attendance(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Unprocessable Entity")
         
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size))
    items = [serialize_attendance(d) for d in docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

@app.post("/attendance/punch-out",status_code = 200)
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if body.punched_at is not None:
        punch_out = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond = 0)
    else:
        punch_out = datetime.now(timezone.utc).replace(microsecond=0)
        
    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "punch_in": {"$lte" : punch_out}},
        sort=[("punch_in", -1)])

    if record is None:
        raise HTTPException(404,"No punch-in found")
    
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    punched_in = record.get("punch_in")
    date_str = record.get("date")
    
    if punched_in >= punch_out or (punch_out - punched_in).total_seconds() > 86400:
        raise HTTPException(422, "Unprocessable Entity")
    
    work_hours = compute_work_hours(punched_in, punch_out)
    overtime_minutes = compute_overtime(punch_out, emp["shift_start"], emp["shift_end"], date_str)
    half_day = True if work_hours < 4.5 else False
    
    updated_record = db.attendance_logs.find_one_and_update(
        {"_id" : record["_id"], "punch_out": None},
        {"$set" : {
            "punch_out" : punch_out,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day
            }
        },
        return_document=ReturnDocument.AFTER
    )

    if updated_record is None:
        raise HTTPException(409, "Already punched out")

    return serialize_attendance(updated_record)

@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def update_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "Attendance record not found")

    new_status = body.status or record["status"]

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Punch times cannot be set for ABSENT or LEAVE")
        target = {
            "status": new_status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        # Presence status
        p_in = (
            datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc).replace(microsecond=0)
            if body.punch_in is not None
            else record.get("punch_in")
        )
        if p_in is None:
            raise HTTPException(422, "Presence status requires punch_in")

        # R1 attendance date check
        p_in_ist = p_in.astimezone(IST)
        if emp["shift_end"] <= emp["shift_start"] and p_in_ist.strftime("%H:%M") < emp["shift_end"]:
            derived_date = (p_in_ist - timedelta(days=1)).date().isoformat()
        else:
            derived_date = p_in_ist.date().isoformat()
        if derived_date != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")

        p_out = (
            datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc).replace(microsecond=0)
            if body.punch_out is not None
            else record.get("punch_out")
        )
        if p_out is not None:
            if p_out <= p_in or (p_out - p_in).total_seconds() > 86400:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
            work_hours = compute_work_hours(p_in, p_out)
            overtime = compute_overtime(p_out, emp["shift_start"], emp["shift_end"], date)
            half_day = work_hours < 4.5
        else:
            work_hours = None
            overtime = 0
            half_day = False

        target = {
            "status": new_status,
            "punch_in": p_in,
            "punch_out": p_out,
            "work_hours": work_hours,
            "late_minutes": compute_late_minutes(p_in_ist, emp["shift_start"], emp["shift_end"]),
            "overtime_minutes": overtime,
            "half_day": half_day,
        }

    # Compare tracked fields with legacy fallback defaults
    defaults = {"late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    changes = {}
    for k, v in target.items():
        old_val = record.get(k, defaults.get(k))
        if isinstance(old_val, datetime):
            old_val = old_val.replace(microsecond=0)
        if old_val != v:
            changes[k] = {"from": old_val, "to": v}

    if not changes:
        raise HTTPException(422, "Request changes nothing")

    old_history = record.get("history") or []
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": emp_code,
            "date": date,
            f"history.{len(old_history)}": {"$exists": False},
        },
        {
            "$set": target,
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "Concurrent modification conflict")

    return serialize_attendance(updated)

@app.get("/analytics/employees/{emp_code}/monthly")
def monthly_attendance(emp_code: str, month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    year, mon = map(int, month.split("-"))
    _, num_days = calendar.monthrange(year, mon)
    start_of_month = date(year, mon, 1)
    end_of_month = date(year, mon, num_days)
    joined_date = date.fromisoformat(emp["joined_on"])
    start_date = max(start_of_month, joined_date)

    working_days = 0
    cur = start_date
    while cur <= end_of_month:
        if cur.weekday() < 5:
            working_days += 1
        cur += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": f"{month}-01", "$lte": f"{month}-{num_days:02d}"},
            }
        },
        {
            "$project": {
                "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$dow", [2, 3, 4, 5, 6]]},
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    agg_res = list(db.attendance_logs.aggregate(pipeline))
    if agg_res:
        stats = agg_res[0]
        present_days = stats["present_days"]
        leave_days = stats["leave_days"]
        late_count = stats["late_count"]
        total_late = stats["total_late_minutes"]
        total_ot = stats["total_overtime_minutes"]
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late = 0
        total_ot = 0

    if working_days > 0:
        pct = (Decimal(str(present_days)) * Decimal(100) / Decimal(working_days)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        attendance_pct = float(pct)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    department: Optional[str] = None,
):
    year, mon = map(int, month.split("-"))
    _, num_days = calendar.monthrange(year, mon)
    month_end_date = f"{month}-{num_days:02d}"

    match_employees = {"joined_on": {"$lte": month_end_date}}
    if department:
        match_employees["department"] = department

    pipeline = [
        {"$match": match_employees},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "emp_codes": {"$push": "$emp_code"},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"dept_emps": "$emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$dept_emps"]},
                                    {"$gte": ["$date", f"{month}-01"]},
                                    {"$lte": ["$date", month_end_date]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                            "status": 1,
                            "half_day": 1,
                            "work_hours": 1,
                            "late_minutes": 1,
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$in": ["$dow", [2, 3, 4, 5, 6]]},
                                            ]
                                        },
                                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                                        0.0,
                                    ]
                                }
                            },
                            "leave_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                            },
                            "on_duty_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                            },
                            "late_count": {
                                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                            },
                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },
                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        "$work_hours",
                                        0.0,
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "metrics",
            }
        },
        {"$sort": {"_id": 1}},
    ]

    results = list(db.employees.aggregate(pipeline))
    items = []
    for row in results:
        headcount = row["headcount"]
        metrics = row["metrics"][0] if row["metrics"] else {}
        work_hours_count = metrics.get("work_hours_count", 0)
        work_hours_sum = metrics.get("work_hours_sum", 0.0)

        if work_hours_count > 0:
            avg_work_hours = float(
                (Decimal(str(work_hours_sum)) / Decimal(work_hours_count)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
        else:
            avg_work_hours = None

        items.append({
            "department": row["_id"],
            "headcount": headcount,
            "present_days": metrics.get("present_days", 0.0),
            "avg_work_hours": avg_work_hours,
            "late_count": metrics.get("late_count", 0),
            "total_late_minutes": metrics.get("total_late_minutes", 0),
            "leave_count": metrics.get("leave_count", 0),
            "on_duty_count": metrics.get("on_duty_count", 0),
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    department: Optional[str] = None,
):
    year, mon = map(int, month.split("-"))
    _, num_days = calendar.monthrange(year, mon)
    month_end_date = f"{month}-{num_days:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": f"{month}-01", "$lte": month_end_date},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline.extend([
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ])

    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: Annotated[str, Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")],
    to_date: Annotated[str, Query(alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$")],
):
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Unknown department")

    start_date = date.fromisoformat(from_date)
    end_date = date.fromisoformat(to_date)

    if end_date < start_date:
        raise HTTPException(422, "'to' date cannot be before 'from' date")

    if (end_date - start_date).days > 92:
        raise HTTPException(422, "Date range exceeds 92 days")

    from_instant = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
    to_instant = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)

    dept_employees = list(db.employees.find({"department": department}, {"emp_code": 1}))
    dept_emp_codes = [emp["emp_code"] for emp in dept_employees]

    pipeline = [
        {"$match": {"department": department}},
        {"$limit": 1},
        {"$project": {"date_val": from_instant}},
        {
            "$densify": {
                "field": "date_val",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_instant, to_instant + timedelta(days=1)],
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_val"}},
                "dow": {"$dayOfWeek": "$date_val"},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_info",
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", dept_emp_codes]},
                                    {"$eq": ["$date", "$$day"]},
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                                        0.0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                            },
                        }
                    },
                ],
                "as": "logs_info",
            }
        },
        {"$sort": {"date": 1}},
    ]

    daily_records = list(db.employees.aggregate(pipeline))
    items = []

    for record in daily_records:
        day_str = record["date"]
        is_working_day = record["dow"] in (2, 3, 4, 5, 6)

        headcount_doc = record.get("headcount_info", [])
        headcount = headcount_doc[0]["count"] if headcount_doc else 0

        logs_doc = record.get("logs_info", [])
        if logs_doc:
            present_count = logs_doc[0].get("present_count", 0.0)
            late_count = logs_doc[0].get("late_count", 0)
        else:
            present_count = 0.0
            late_count = 0

        if is_working_day and headcount > 0:
            rate = float(
                (Decimal(str(present_count)) / Decimal(headcount)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            )
        else:
            rate = None

        window_rates = [rate] if rate is not None else []
        preceding_rows = items[-6:] if len(items) >= 6 else items[:]
        for previous_row in preceding_rows:
            if previous_row["attendance_rate"] is not None:
                window_rates.append(previous_row["attendance_rate"])

        if window_rates:
            moving_avg = float(
                (Decimal(str(sum(window_rates))) / Decimal(len(window_rates))).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            )
        else:
            moving_avg = None

        items.append({
            "date": day_str,
            "is_working_day": is_working_day,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": rate,
            "moving_avg_7d": moving_avg,
        })

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    if endpoint == "attendance_list":
        filter_query = {}
        if emp_code:
            filter_query["emp_code"] = emp_code
        if date_from or date_to:
            filter_query["date"] = {}
            if date_from:
                filter_query["date"]["$gte"] = date_from
            if date_to:
                filter_query["date"]["$lte"] = date_to
        if status:
            filter_query["status"] = status

        raw_explain = db.command("explain", {
            "find": "attendance_logs",
            "filter": filter_query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }, verbosity="executionStats")
        collection_name = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        year, mon = map(int, month.split("-"))
        _, num_days = calendar.monthrange(year, mon)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": f"{month}-01", "$lte": f"{month}-{num_days:02d}"}}}
        ]
        raw_explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        collection_name = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        year, mon = map(int, month.split("-"))
        _, num_days = calendar.monthrange(year, mon)
        match_query = {"joined_on": {"$lte": f"{month}-{num_days:02d}"}}
        if department:
            match_query["department"] = department
        pipeline = [
            {"$match": match_query}
        ]
        raw_explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        collection_name = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        year, mon = map(int, month.split("-"))
        _, num_days = calendar.monthrange(year, mon)
        pipeline = [
            {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-{num_days:02d}"}, "late_minutes": {"$gt": 0}}}
        ]
        raw_explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        collection_name = "attendance_logs"

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422, "department, from, and to are required")
        pipeline = [
            {"$match": {"department": department}}
        ]
        raw_explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        collection_name = "employees"

    explain_clean = json.loads(json_util.dumps(raw_explain))
    return {
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": explain_clean,
    }

