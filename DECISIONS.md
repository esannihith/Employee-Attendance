# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I created unique indexes on `employees.emp_code` and compound `attendance_logs(emp_code, date)` to enforce business entity uniqueness and guarantee race safety under concurrent requests. For queries, I created `employees(department, emp_code)` to back filtered employee pagination, and compound `attendance_logs(date: -1, emp_code: 1)` to match the exact pagination and sorting requirement of `GET /attendance`.
I considered `attendance_logs(date: 1, emp_code: 1)`, but rejected it because `GET /attendance` sorts by `date` descending and `emp_code` ascending. MongoDB cannot traverse an index with opposing directions, which forces a blocking in-memory `SORT` stage on large collections.
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.
