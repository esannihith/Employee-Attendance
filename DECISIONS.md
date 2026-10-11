# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I created unique indexes on `employees.emp_code` and compound `attendance_logs(emp_code, date)` to enforce business entity uniqueness and guarantee race safety under concurrent requests. For queries, I created `employees(department, emp_code)` to back filtered employee pagination, and compound `attendance_logs(date: -1, emp_code: 1)` to match the exact pagination and sorting requirement of `GET /attendance`.
I considered `attendance_logs(date: 1, emp_code: 1)`, but rejected it because `GET /attendance` sorts by `date` descending and `emp_code` ascending. MongoDB cannot traverse an index with opposing directions, which forces a blocking in-memory `SORT` stage on large collections.
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
Both requests validate the employee and independently compute the same attendance date. Even if both pass the initial `find_one` query simultaneously, they must insert into `attendance_logs`. Backed by the compound unique index on `(emp_code, date)`, MongoDB's storage engine atomically commits the first document and rejects the second with a `DuplicateKeyError` (E11000). The winning thread returns 201, while the losing thread catches the exception and returns 409 Conflict without data corruption.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
The leaderboard returns all tied employees whose rank meets the threshold, even if the total items exceed `limit`. Ranking follows standard competition ranking (e.g., 1, 2, 2, 4) computed with MongoDB's `$rank` window function. Filtering on `rank <= limit` instead of an arbitrary array slice ensures tied employees are treated fairly rather than dropped due to secondary sorting.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
The aggregation pipeline originates on the `employees` collection rather than `attendance_logs`. It matches employees whose `joined_on` is on or before month end, groups by department to calculate the exact headcount (`$sum: 1`), and joins logs via `$lookup`. Starting on `attendance_logs` would omit employees with zero activity, but anchoring on `employees` ensures full department coverage.

5. **One thing you would change** if this had to serve 100x the data.
At 100x data (~10M logs), computing ad-hoc `$lookup` joins and `$densify` pipelines on raw logs would bottleneck database memory. I would maintain pre-aggregated monthly summary documents updated asynchronously or on daily shift close, converting heavy pipeline scans into sub-millisecond point reads.
