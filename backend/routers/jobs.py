from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_capability
from services.intern_jobs_pipeline import execute_intern_jobs_pipeline
from services.job_fetcher import run_job_fetch_cycle

router = APIRouter(tags=["jobs"])

# Both endpoints drive write pipelines with the Supabase service role, so they
# are gated on a grant in public.admin_grants rather than on being signed in.
# A guest is refused with 403: they are authenticated but can never hold a
# grant, since grants are seeded by hand against an account. Note that until
# 20260917000200 is pushed to a project, admin_grants does not exist there and
# these fail closed with 503.
RUN_JOBS = Depends(require_capability("run_jobs"))


class JobFetchRequest(BaseModel):
    role: str | None = None
    count: int | None = None
    experience_level: str | None = None


@router.post("/jobs/fetch", dependencies=[RUN_JOBS])
async def fetch_jobs(req: JobFetchRequest = JobFetchRequest()):
    """
    Trigger a job fetch cycle. Fetches from the Cloudflare Worker (if
    JOB_FETCHER_API_URL is set) or directly from Supabase job_sources,
    then dedupes and persists to job_listings.
    """
    query = {}
    if req.role:
        query["role"] = req.role
    if req.count:
        query["count"] = req.count
    if req.experience_level:
        query["experienceLevel"] = req.experience_level

    result = await run_job_fetch_cycle(query or None)
    return result


@router.post("/intern-jobs/run", dependencies=[RUN_JOBS])
async def run_intern_jobs_pipeline():
    """
    Execute the intern jobs pipeline: pull top 100 jobs from Airtable,
    snapshot to jobs_snapshot, advance pipeline_state. Used by the
    Intern Jobs Alerts page "Fetch Now" button.
    """
    try:
        result = await execute_intern_jobs_pipeline()
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc) or "Unknown pipeline error")
