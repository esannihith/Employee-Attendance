# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | health() | Did not check MongoDB connectivity and unconditionally returned ok status | Simulating database disconnection still returned 200 OK | Added client ping command and 503 Service Unavailable exception handling |
| 2 | compute_late_minutes | Evaluated minute difference without seconds; 09:40:01 was incorrectly marked as 0 minutes late for a 09:30 shift | Tested boundary input 09:40:01 IST against Rule R2 (expected 10 min) | Evaluated elapsed seconds strictly greater than 600 seconds with floor division |
| 3 | compute_work_hours | Used Python built-in round() on float division, applying banker's rounding | Boundary durations like 9.125 gave 9.12 instead of 9.13 | Used Python Decimal division with ROUND_HALF_UP quantize per Rule R4 |
| 4 | compute_overtime | Failed on overnight shifts where shift_end <= shift_start, and did not enforce the 30-min minimum threshold | 5 min overtime returned 5 instead of 0; overnight shift end resulted in negative delta | Added +1 day shift adjustment for overnight shifts and enforced minutes >= 30 threshold |
| 5 | GET /employees | Pagination skip was calculated as page * page_size, dropping page 1 items; total ignored department filter | Requesting page=1 skipped the first 20 employees; filtering by department returned wrong total | Fixed offset to (page - 1) * page_size and filtered count_documents(q) |
| 6 | GET /employees | Missing sort order by emp_code ascending; created_at returned as raw datetime object | Checked response against OpenAPI contract; order was non-deterministic and timestamp was ISO | Added .sort("emp_code") and converted created_at to integer epoch milliseconds |
| 7 | POST /employees | Allowed identical shift_start and shift_end; concurrent requests with duplicate emp_code caused 500 | Sent identical shifts (09:30 to 09:30) and sent parallel identical creations | Added 422 check for identical shift times and caught DuplicateKeyError to return 409 |
| 8 | POST /employees | created_at used naive local datetime.now() and returned un-serialized datetime in response body | Inspected database document and API response types | Anchored created_at to timezone.utc and serialized to integer epoch milliseconds |
| 9 | POST /attendance/punch-in | Missing 404 check for unknown employee; accepted non-presence statuses; failed R1 overnight date derivation | Submitting punch-in for non-existent emp or shift 22:00-06:00 at 01:00 IST gave wrong date | Added 404 check, 422 presence status check, and shifted morning punches < shift_end to yesterday |
| 10 | POST /attendance/punch-in | Microseconds were not truncated per R1; concurrent duplicate punch-in attempts yielded 500 | Microseconds persisted in BSON; parallel punch-in threw unhandled PyMongo error | Truncated microseconds with replace(microsecond=0) and caught DuplicateKeyError with 409 |
| 11 | GET /attendance | Memory scalability flaw: loaded entire collection into Python memory with list(find()) and sorted in Python | Memory ballooned under large collections; failed at 100k documents | Moved filtering, compound sorting [(date, -1), (emp_code, 1)], and pagination to MongoDB cursor |
| 12 | GET /attendance | Inverted date range date_from > date_to returned 200 []; leaked MongoDB id; punch datetimes un-serialized | Queried date_from=2026-07-10&date_to=2026-07-01; observed ISO strings and internal id field | Added 422 validation for inverted ranges, stripped internal ids, and serialized instants to epoch ms |

Also note anything you looked at and decided was **not** a defect, and why.
- **IST Timezone Definition:** `timezone(timedelta(hours=5, minutes=30))` is standard and correctly models UTC+05:30 as specified by Rule R1.
- **BSON Date Storage in UTC:** Verified that storing UTC datetimes in MongoDB while converting to epoch milliseconds at the API boundary is the required data contract.
- **Database Connection Lifecycle:** Using environment variables for URI and DB with local defaults matches the environment contract.
