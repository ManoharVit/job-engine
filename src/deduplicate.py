import difflib
from typing import List, Iterable
from src.models.job import JobCreate

def fuzzy_match(a: str, b: str, threshold: float = 0.8) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold

def is_duplicate(new_job: JobCreate, existing_jobs: Iterable[JobCreate]) -> bool:
    """
    Check if new_job matches any job in existing_jobs based on canonical URL
    or (company + title + location).
    """
    new_url = str(new_job.canonical_url) if new_job.canonical_url else ""
    new_title = new_job.title.strip().lower()
    new_company = new_job.company.strip().lower()
    new_location = new_job.location.strip().lower() if new_job.location else ""

    for job in existing_jobs:
        job_url = str(job.canonical_url) if job.canonical_url else ""
        if new_url and job_url and new_url == job_url:
            return True
            
        job_title = job.title.strip().lower()
        job_company = job.company.strip().lower()
        job_location = job.location.strip().lower() if job.location else ""
        
        if fuzzy_match(new_company, job_company, 0.8) and fuzzy_match(new_title, job_title, 0.8):
            if new_location == job_location:
                return True

    return False

def deduplicate_jobs(jobs: List[JobCreate]) -> List[JobCreate]:
    """
    Deduplicate a list of jobs against itself.
    Returns a new list with unique jobs.
    """
    unique_jobs = []
    for job in jobs:
        if not is_duplicate(job, unique_jobs):
            unique_jobs.append(job)
    return unique_jobs
