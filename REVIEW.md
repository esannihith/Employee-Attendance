# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | health() / line 73 | The functions doesn't check mongodb and always returns ok status | Contract crosscheck revealed the mismatch | Added MongoDB check and correct status code with exception handling |
| 2 | compute_late_minutes / line 33 | An edge case 9:40:01 is false labeled as not late for shift time of 9:30 | Crosscheck the contract with boundary conditions | Added check for seconds and corrected the late minutes calculation |

Also note anything you looked at and decided was **not** a defect, and why.
