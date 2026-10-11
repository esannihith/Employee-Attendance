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
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated, Literal
from decimal import Decimal, ROUND_HALF_UP

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



