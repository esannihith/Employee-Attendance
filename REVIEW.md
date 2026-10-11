# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | health() | The functions doesn't check mongodb and always returns ok status | Contract crosscheck revealed the mismatch | Added MongoDB check and correct status code with exception handling |
| 2 | compute_late_minutes | An edge case 9:40:01 is false labeled as not late for shift time of 9:30 | Crosscheck the contract with boundary conditions | Added check for seconds and corrected the late minutes calculation |
| 3 | compute_work_hours | The functions uses bankers rounding using round library| Crosscheck the contract with business rules | Replace round with Decimal library for half up rounding |
| 4 | compute_overtime | The function fails for night shift | Crosscheck with rules R1, R3 | Added check for night shift and corrected the overtime calculation |
| 5 | GET /employees | Pagination issue, date item, filter not passed | Contract crosscheck | Fixed pagination, pass the query to filter, converted return created_at to milliseconds | 
| 6 | POST /employees | Concurrency Race Condition, created_at contract mismatch, identical shift and start end time not handled | Contract crosscheck | Added error handling for race condition, check for identical shift and start end time, fixed created_at |
| 7 | POST /attendance/punch-in | Logic issue, night shift employee not handled, timezone | Contract crosscheck | Added check for night shift and timezone | 

Also note anything you looked at and decided was **not** a defect, and why.
