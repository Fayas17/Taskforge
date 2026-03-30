"""
TaskForge Load Test
===================
Simulates realistic user behaviour:
  - Register + login once per user (staggered to avoid rate limit cascade)
  - Upload a CSV (realistic 10k rows — tests API + dispatch, not worker throughput)
  - Poll job status frequently (real usage pattern)
  - Occasionally refresh token

For worker throughput testing (2.5M+ rows), use the separate stress test below:
  locust -f locustfile.py --class-picker
  select: WorkerStressUser
"""

import io
import random
import time
import uuid
from typing import Any, ClassVar

from locust import HttpUser, between, task

# ── CSV fixtures ──────────────────────────────────────────────────────────────

# Small CSV — tests API response time and Celery dispatch speed (not worker)
SMALL_CSV = b"id,name,age,email,city\n" + (b"1,Alice,30,alice@example.com,New York\n" * 10_000)

# Medium CSV — tests worker with a realistic but manageable payload
MEDIUM_CSV = b"id,name,age,email,city\n" + (b"1,Alice,30,alice@example.com,New York\n" * 100_000)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _register_and_login(client: Any, max_retries: int = 3) -> tuple[str | None, bool | None]:
    """Register a new user and login, retrying on 429."""
    email = f"{uuid.uuid4()}@example.com"
    username = f"user_{uuid.uuid4().hex[:8]}"

    for attempt in range(max_retries):
        r = client.post(
            "/auth/register/",
            json={"email": email, "username": username, "password": "test@password123"},
            name="/auth/register/",
        )
        if r.status_code == 200:
            break
        if r.status_code == 429:
            time.sleep(2**attempt)  # exponential backoff: 1s, 2s, 4s
        else:
            return None, None

    for attempt in range(max_retries):
        r = client.post(
            "/auth/login/",
            json={"email": email, "password": "test@password123"},
            name="/auth/login/",
        )
        if r.status_code == 200:
            # Let the session carry all cookies — don't extract manually
            # The session will send access_token + refresh_token on every request
            return email, True
        if r.status_code == 429:
            time.sleep(2**attempt)

    return None, None


# ── Main load test user ───────────────────────────────────────────────────────


class TaskForgeUser(HttpUser):
    """
    Simulates a real user: uploads CSVs, polls status, refreshes token.
    Spawn rate: 2/sec, target: 10 users.
    """

    wait_time = between(1, 3)
    job_ids: ClassVar[list[str]] = []

    def on_start(self) -> None:
        # Stagger startup to avoid all users hitting rate limits simultaneously
        time.sleep(random.uniform(0, 5))  # noqa: S311
        _, success = _register_and_login(self.client)
        if not success:
            self.environment.runner.quit()

    @task(2)
    def upload_csv(self) -> None:
        """Upload a small CSV — tests API response and Celery dispatch."""
        with self.client.post(
            "/jobs/upload/",
            files={"file": ("data.csv", io.BytesIO(SMALL_CSV), "text/csv")},
            name="/jobs/upload/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                job_id = response.json().get("id")
                if job_id:
                    self.job_ids.append(job_id)
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited on upload")
            elif response.status_code == 401:
                response.failure("Unauthenticated — token missing")
            else:
                response.failure(f"Unexpected: {response.status_code}")

    @task(8)
    def poll_job_status(self) -> None:
        """Poll job status — most common real-world action after upload."""
        if not self.job_ids:
            return
        job_id = random.choice(self.job_ids)  # noqa: S311
        with self.client.get(
            f"/jobs/{job_id}/",
            name="/jobs/{id}/",  # group all job IDs under one metric
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                self.job_ids.remove(job_id)
                response.success()  # expected after rollback
            else:
                response.failure(f"Unexpected: {response.status_code}")

    @task(1)
    def list_jobs(self) -> None:
        """List all jobs for the current user."""
        self.client.get("/jobs/", name="/jobs/")

    @task(3)
    def refresh_token(self) -> None:
        """
        Refresh token — session carries refresh_token cookie automatically
        after login. Do NOT pass cookies manually here.
        """
        with self.client.post(
            "/auth/refresh/",
            name="/auth/refresh/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited on refresh")
            elif response.status_code == 401:
                response.failure("Refresh token missing or expired")
            else:
                response.failure(f"Unexpected: {response.status_code}")


# ── Worker stress test user ───────────────────────────────────────────────────


class WorkerStressUser(HttpUser):
    """
    Stress tests the Celery worker with a medium CSV (100k rows).
    Use this separately — NOT together with TaskForgeUser.
    Run with: locust --class-picker, select WorkerStressUser, 3 users max.
    """

    wait_time = between(30, 60)  # wait between uploads — worker needs time
    job_ids: ClassVar[list[str]] = []

    def on_start(self) -> None:
        time.sleep(random.uniform(0, 10))  # noqa: S311
        _register_and_login(self.client)

    @task
    def upload_medium_csv(self) -> None:
        with self.client.post(
            "/jobs/upload/",
            files={"file": ("stress.csv", io.BytesIO(MEDIUM_CSV), "text/csv")},
            name="/jobs/upload/ [stress]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                job_id = response.json().get("id")
                if job_id:
                    self.job_ids.append(job_id)
                response.success()
            else:
                response.failure(f"{response.status_code}")

    @task(10)
    def poll_until_complete(self) -> None:
        if not self.job_ids:
            return
        job_id = self.job_ids[-1]
        with self.client.get(
            f"/jobs/{job_id}/",
            name="/jobs/{id}/ [stress]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                status = response.json().get("status")
                if status == "completed":
                    self.job_ids.remove(job_id)
                response.success()
            else:
                response.failure(f"{response.status_code}")
