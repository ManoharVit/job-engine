import pytest
from pydantic import ValidationError
from src.canonicalize import canonicalize_url
from src.deduplicate import is_duplicate, deduplicate_jobs
from src.ingestors import CSVIngestor, URLIngestor
from src.scoring import calculate_fit_score
from src.models.job import JobCreate
from src.models.profile import CandidateProfile, Evidence

def test_canonicalize_url():
    # Valid tracking params
    url1 = "https://example.com/job?utm_source=linkedin&utm_medium=social&ref=123"
    assert canonicalize_url(url1) == "https://example.com/job"
    
    # Valid non-tracking params
    url2 = "https://example.com/job?id=456&tracker=123"
    assert canonicalize_url(url2) == "https://example.com/job?id=456"
    
    # Missing trailing slash
    url3 = "https://example.com/job/"
    assert canonicalize_url(url3) == "https://example.com/job"
    
def test_functional_url_parameters():
    # 9. Add tests for functional URL query parameters that must not be removed.
    url = "https://example.com/job?refresh_token=abc&source_id=999&utm_source=test"
    canonical = canonicalize_url(url)
    assert "utm_source" not in canonical
    assert "refresh_token=abc" in canonical
    assert "source_id=999" in canonical

def test_csv_ingestion_valid():
    csv_data = "source,source_job_id,url,title,company,location,remote_status,employment_type,salary_text,description,required_skills,preferred_skills,experience_level\ncsv,job_001,https://example.com/job1?ref=test,Software Engineer,Acme Corp,NY,remote,full-time,$100k,Desc,\"Python,SQL\",\"AWS,Docker\",mid-level"
    ingestor = CSVIngestor()
    jobs, rejected = ingestor.ingest(csv_data)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Software Engineer"
    assert str(job.canonical_url) == "https://example.com/job1"
    assert job.required_skills == ["Python", "SQL"]

def test_url_ingestion_valid():
    data = [{
        "source": "manual",
        "source_job_id": "job_002",
        "url": "https://example.com/job2?utm_source=twitter",
        "title": "Data Scientist",
        "company": "Globex",
        "required_skills": ["Python", "Machine Learning"]
    }]
    ingestor = URLIngestor()
    jobs, rejected = ingestor.ingest(data)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Data Scientist"
    assert str(job.canonical_url) == "https://example.com/job2"

def test_ingestion_malformed_url():
    # URLIngestor should skip items with malformed URLs because of JobCreate validation
    data = [{
        "source": "manual",
        "source_job_id": "job_003",
        "url": "not-a-url",
        "title": "Data Scientist",
        "company": "Globex"
    }]
    ingestor = URLIngestor()
    jobs, rejected = ingestor.ingest(data)
    assert len(jobs) == 0
    assert len(rejected) == 1

def test_deduplication():
    job1 = JobCreate(source="csv", source_job_id="1", canonical_url="https://example.com/1", title="A", company="B")
    job2 = JobCreate(source="url", source_job_id="2", canonical_url="https://example.com/1", title="C", company="D")
    job3 = JobCreate(source="csv", source_job_id="3", canonical_url="https://example.com/3", title="A", company="B")
    job4 = JobCreate(source="csv", source_job_id="4", canonical_url="https://example.com/4", title="X", company="Y")
    
    jobs = [job1, job2, job3, job4]
    unique = deduplicate_jobs(jobs)
    
    # job2 is duplicate of job1 (same URL)
    # job3 is duplicate of job1 (same title and company and location)
    assert len(unique) == 2
    assert unique[0].source_job_id == "1"
    assert unique[1].source_job_id == "4"

def test_deduplication_different_tracking_params():
    # 10. Add tests for duplicate jobs with different tracking parameters.
    job1 = JobCreate(source="csv", source_job_id="1", canonical_url=canonicalize_url("https://example.com/job?utm_source=a"), title="Dev", company="Tech")
    job2 = JobCreate(source="url", source_job_id="2", canonical_url=canonicalize_url("https://example.com/job?utm_source=b"), title="Dev2", company="Tech2")
    
    jobs = [job1, job2]
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 1
    assert unique[0].source_job_id == "1"

def test_fit_score():
    job = JobCreate(
        source="csv",
        source_job_id="1",
        canonical_url="https://example.com/1",
        title="Senior Data Engineer",
        company="Tech Inc",
        location="San Francisco, CA",
        remote_status="hybrid",
        experience_level="3-5 years",
        description="Looking for a data engineer.",
        required_skills=["Python", "SQL", "Spark"]
    )
    
    evidence = Evidence(skill="Python", source_type="resume", reference="line 10", verified=True)
    profile = CandidateProfile(
        target_roles=["Data Engineer"],
        preferred_locations=["San Francisco"],
        remote_preference="hybrid",
        experience_range="3-5 years",
        must_have_skills=["Python", "SQL"],
        evidence_registry=[evidence]
    )
    
    score, breakdown = calculate_fit_score(job, profile)
    
    assert breakdown['role_title'] == 0.25
    assert breakdown['core_technical_skills'] == 0.30 # both Python and SQL matched
    assert breakdown['domain'] == 0.15
    assert breakdown['seniority'] == 0.10
    assert breakdown['location_remote'] == 0.10
    assert breakdown['evidence'] == 0.05 # 1 of 2 skills has evidence
    assert score == 0.95

def test_negated_skill_fit_score():
    job = JobCreate(
        source="csv",
        source_job_id="1",
        canonical_url="https://example.com/1",
        title="Software Engineer",
        company="Tech",
        description="We are looking for someone with Java. No Python required."
    )
    profile = CandidateProfile(
        target_roles=["Software Engineer"],
        must_have_skills=["Python", "Java"],
        evidence_registry=[]
    )
    score, breakdown = calculate_fit_score(job, profile)
    assert breakdown['core_technical_skills'] == 0.15 # Java matched, Python skipped due to negation
    
def test_fit_score_missing_fields_and_unsupported_claims():
    # Job missing skills, location, etc.
    job = JobCreate(
        source="csv",
        source_job_id="2",
        canonical_url="https://example.com/2",
        title="Unknown Developer",
        company="Mystery Inc"
    )
    profile = CandidateProfile(
        target_roles=["Data Engineer"],
        must_have_skills=["Python", "SQL"],
        evidence_registry=[] # No evidence
    )
    
    score, breakdown = calculate_fit_score(job, profile)
    assert breakdown['role_title'] == 0.0
    assert breakdown['core_technical_skills'] == 0.0
    assert breakdown['domain'] == 0.0
    assert breakdown['seniority'] == 0.05
    assert breakdown['location_remote'] == 0.0
    assert breakdown['evidence'] == 0.0
    assert score == 0.05

def test_fit_score_no_must_haves_no_evidence_no_location_no_roles():
    # 8. Test scoring when the candidate has no must-have skills, no evidence, no location, and no target roles.
    job = JobCreate(
        source="csv",
        source_job_id="3",
        canonical_url="https://example.com/3",
        title="Generic Job",
        company="Company"
    )
    profile = CandidateProfile(
        target_roles=[],
        preferred_locations=[],
        remote_preference="",
        experience_range="",
        must_have_skills=[],
        evidence_registry=[]
    )
    score, breakdown = calculate_fit_score(job, profile)
    assert breakdown['role_title'] == 0.25 # defaults to full if empty
    assert breakdown['core_technical_skills'] == 0.30 # defaults to full if empty
    assert breakdown['domain'] == 0.15
    assert breakdown['seniority'] == 0.05
    assert breakdown['location_remote'] == 0.10 # defaults to full if both empty
    assert breakdown['evidence'] == 0.10 # defaults to full if empty must-have skills
    assert score == 0.95

def test_canonicalize_keeps_blank_values():
    url = "https://example.com/job?flag=&id=123"
    assert canonicalize_url(url) == "https://example.com/job?flag=&id=123"

def test_csv_ingestor_nul_byte():
    csv_data = "source,source_job_id,url,title,company\ncsv,1,https://a.com,Title\x00,Company"
    ingestor = CSVIngestor()
    # Pydantic validation handles null bytes or we strip it, either way shouldn't crash
    # If the CSV module itself crashes, it'll propagate, which fulfills "no swallowed errors"
    jobs, rejected = ingestor.ingest(csv_data)
    if jobs:
        assert "Title" in jobs[0].title

def test_canonicalize_bad_url():
    # A malformed URL that might crash parse
    url = "http://"
    assert canonicalize_url(url) == "http://"

def test_scoring_zero_division():
    # If a profile has NO must_have_skills, what happens to the score?
    job = JobCreate(
        source="csv",
        source_job_id="1",
        canonical_url="https://example.com/1",
        title="Software Engineer",
        company="Tech Inc",
        required_skills=["Python"]
    )
    profile = CandidateProfile(
        target_roles=["Engineer"],
        preferred_locations=[],
        remote_preference="remote",
        experience_range="",
        must_have_skills=[],  # empty
        evidence_registry=[]
    )
    score, breakdown = calculate_fit_score(job, profile)
    assert breakdown['core_technical_skills'] == 0.30

def test_url_ingestor_missing_keys():
    # What if a manual URL misses the 'url' completely?
    data = [{
        "source": "manual",
        "source_job_id": "job_002",
        "title": "Data Scientist",
        "company": "Globex"
    }]
    ingestor = URLIngestor()
    # Should skip
    jobs, rejected = ingestor.ingest(data)
    assert len(jobs) == 0
    assert len(rejected) == 1

def test_csv_ingestor_skips_malformed_rows():
    csv_data = "source,source_job_id,url,title,company\ncsv,1,https://valid.com,Valid,Company\ncsv,2,not-a-url,Invalid,Company\ncsv,3,https://also-valid.com,Valid2,Company"
    ingestor = CSVIngestor()
    jobs, rejected = ingestor.ingest(csv_data)
    assert len(jobs) == 2
    assert len(rejected) == 1
    assert jobs[0].source_job_id == "1"
    assert jobs[1].source_job_id == "3"

def test_deduplication_fuzzy():
    job1 = JobCreate(source="csv", source_job_id="1", canonical_url="https://example.com/1", title="Senior Engineer", company="Tech Corp", location="NY")
    # Minor typos, should be deduplicated
    job2 = JobCreate(source="url", source_job_id="2", canonical_url="https://example.com/2", title="Sr. Engineer", company="Tech Corp", location="NY")
    
    jobs = [job1, job2]
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 1

def test_scoring_special_chars():
    job = JobCreate(
        source="csv",
        source_job_id="1",
        canonical_url="https://example.com/1",
        title="Backend Developer",
        company="Tech",
        description="Must know C++ and .NET. No C# required."
    )
    profile = CandidateProfile(
        target_roles=["Backend Developer"],
        must_have_skills=["C++", ".NET", "C#"],
        evidence_registry=[]
    )
    score, breakdown = calculate_fit_score(job, profile)
    # C++ and .NET matched, C# negated
    assert breakdown['core_technical_skills'] == 0.20
