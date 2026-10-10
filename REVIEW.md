# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | health() / line 73 | The functions doesn't check mongodb and always returns ok status | Contract crosscheck revealed the mismatch | Added MongoDB check and correct status code with exception handling |

Also note anything you looked at and decided was **not** a defect, and why.
