import csv
import io
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple
from pydantic import ValidationError
from src.models.job import JobCreate
from src.canonicalize import canonicalize_url

class JobIngestor(ABC):
    @abstractmethod
    def ingest(self, source_data: Any) -> Tuple[List[JobCreate], List[Dict[str, Any]]]:
        pass

class CSVIngestor(JobIngestor):
    def ingest(self, source_data: str) -> Tuple[List[JobCreate], List[Dict[str, Any]]]:
        """
        Expects a CSV string or file content as source_data.
        CSV columns expected:
        source, source_job_id, url, title, company, location, remote_status, employment_type, salary_text, description, required_skills, preferred_skills, experience_level
        """
        jobs = []
        rejected = []
        reader = csv.DictReader(io.StringIO(source_data.strip()))
        for idx, row in enumerate(reader, start=2):
            url = row.get("url") or row.get("canonical_url", "")
            canonical = canonicalize_url(url)
            
            # Simple skills parsing
            req_skills = [s.strip() for s in row.get("required_skills", "").split(",") if s.strip()]
            pref_skills = [s.strip() for s in row.get("preferred_skills", "").split(",") if s.strip()]
            
            try:
                job = JobCreate(
                    source=row.get("source", "csv"),
                    source_job_id=row.get("source_job_id", ""),
                    canonical_url=canonical,
                    title=row.get("title", ""),
                    company=row.get("company", ""),
                    location=row.get("location"),
                    remote_status=row.get("remote_status"),
                    employment_type=row.get("employment_type"),
                    salary_text=row.get("salary_text"),
                    description=row.get("description"),
                    required_skills=req_skills,
                    preferred_skills=pref_skills,
                    experience_level=row.get("experience_level")
                )
                jobs.append(job)
            except ValidationError as e:
                rejected.append({"row_number": idx, "row": {"title": row.get("title", "Unknown"), "source_job_id": row.get("source_job_id", "Unknown")}, "error": str(e)})
        return jobs, rejected

class URLIngestor(JobIngestor):
    def ingest(self, source_data: List[Dict[str, Any]]) -> Tuple[List[JobCreate], List[Dict[str, Any]]]:
        """
        Expects a list of dictionaries, each with at least 'url', 'title', 'company', 'source_job_id'.
        """
        jobs = []
        rejected = []
        for item in source_data:
            url = item.get("url") or item.get("canonical_url", "")
            canonical = canonicalize_url(url)
            
            try:
                job = JobCreate(
                    source=item.get("source", "manual_url"),
                    source_job_id=item.get("source_job_id", ""),
                    canonical_url=canonical,
                    title=item.get("title", ""),
                    company=item.get("company", ""),
                    location=item.get("location"),
                    remote_status=item.get("remote_status"),
                    employment_type=item.get("employment_type"),
                    salary_text=item.get("salary_text"),
                    description=item.get("description"),
                    required_skills=item.get("required_skills", []),
                    preferred_skills=item.get("preferred_skills", []),
                    experience_level=item.get("experience_level")
                )
                jobs.append(job)
            except ValidationError as e:
                rejected.append({"row": {"title": item.get("title", "Unknown"), "source_job_id": item.get("source_job_id", "Unknown")}, "error": str(e)})
        return jobs, rejected
