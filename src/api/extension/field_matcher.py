import json
import re

def match_fields(fields: list[dict], profile: dict) -> dict:
    """
    fields: list of {index, label, name, placeholder, type, tag, options}
    profile: user profile dict
    Returns: {index: value_to_fill}
    """
    result = {}
    
    for field in fields:
        idx = field.get("index")
        if idx is None:
            continue
            
        label = str(field.get("label", field.get("context", ""))).lower()
        name = str(field.get("name", "")).lower()
        placeholder = str(field.get("placeholder", "")).lower()
        field_type = str(field.get("type", "")).lower()
        tag = str(field.get("tag", "")).lower()
        options = field.get("options", [])
        
        combined_text = f"{label} {name} {placeholder}"
        
        val = _get_best_match(combined_text, field_type, tag, options, profile)
        if val is not None:
            result[idx] = val
            
    return result

def _get_best_match(text, field_type, tag, options, profile):
    # --- Name fields ---
    if any(k in text for k in ["first name", "first_name", "firstname"]):
        return profile.get("name", "").split()[0] if profile.get("name") else ""
    elif any(k in text for k in ["last name", "last_name", "lastname", "surname"]):
        return profile.get("name", "").split()[-1] if profile.get("name") else ""
    elif any(k in text for k in ["full name", "fullname", "your name"]):
        return profile.get("name", "")
    elif re.search(r'\bname\b', text) and not any(k in text for k in ["company", "user"]):
        return profile.get("name", "")
    
    # --- Contact ---
    elif any(k in text for k in ["email", "e-mail", "e_mail"]):
        return profile.get("email", "")
    elif any(k in text for k in ["phone", "mobile", "tel", "contact number", "phone number"]):
        return profile.get("phone", "")
    
    # --- Location ---
    elif any(k in text for k in ["city", "location", "where are you", "current city"]):
        if options:
            loc = profile.get("location", "").lower()
            # Try exact city match first
            city = loc.split(",")[0].strip() if "," in loc else loc
            for opt in options:
                if city in opt.lower() or loc in opt.lower():
                    return opt
            for opt in options:
                if "any" in opt.lower() or "other" in opt.lower():
                    return opt
        return profile.get("location", "").split(",")[0].strip() if "," in profile.get("location", "") else profile.get("location", "")
    elif any(k in text for k in ["state", "province"]):
        loc = profile.get("location", "")
        parts = [p.strip() for p in loc.split(",")]
        if options:
            for part in parts:
                for opt in options:
                    if part.lower() in opt.lower():
                        return opt
        return parts[1] if len(parts) > 1 else ""
    elif any(k in text for k in ["country", "nation"]):
        loc = profile.get("location", "")
        parts = [p.strip() for p in loc.split(",")]
        if options:
            for part in parts:
                for opt in options:
                    if part.lower() in opt.lower():
                        return opt
            for opt in options:
                if "india" in opt.lower():
                    return opt
        return parts[-1] if len(parts) > 1 else loc
    
    # --- Links ---
    elif any(k in text for k in ["linkedin"]):
        return profile.get("linkedin_url", "")
    elif any(k in text for k in ["github"]):
        return profile.get("github_url", "")
    elif any(k in text for k in ["portfolio", "website", "personal url", "resume link", "resume url"]):
        return profile.get("portfolio_url", "") or profile.get("linkedin_url", "")
    
    # --- Professional ---
    elif any(k in text for k in ["current title", "job title", "current role", "current designation", "designation"]):
        return profile.get("current_title", "")
    elif any(k in text for k in ["key skill", "skills", "technical skill", "core skill"]):
        skills_raw = profile.get("skills", "[]")
        if isinstance(skills_raw, str):
            try:
                skills = json.loads(skills_raw)
            except:
                skills = [skills_raw]
        else:
            skills = skills_raw
        return ", ".join(skills[:10])
    elif any(k in text for k in ["experience level", "experience_level"]):
        exp = profile.get("years_experience", 2)
        if options:
            # Try to find matching option
            for opt in options:
                opt_l = opt.lower()
                if exp == 0 and ("fresher" in opt_l or "entry" in opt_l or "0" in opt_l):
                    return opt
                if 1 <= exp <= 3 and ("1" in opt_l or "2" in opt_l or "junior" in opt_l or "entry" in opt_l):
                    return opt
                if 3 < exp <= 6 and ("mid" in opt_l or "3" in opt_l or "5" in opt_l):
                    return opt
                if exp > 6 and ("senior" in opt_l or "6" in opt_l or "7" in opt_l):
                    return opt
            return options[0] if options else str(exp)
        return str(exp)
    elif any(k in text for k in ["experience", "years", "how many", "how long", "proficiency"]):
        if options:
            return _pick_experience_range(options, profile.get("years_experience", 2))
        return str(profile.get("years_experience", 2))
    
    # --- Salary ---
    elif any(k in text for k in ["expected ctc", "expected salary", "expected package", "desired salary", "expected compensation"]):
        return profile.get("expected_ctc", "")
    elif any(k in text for k in ["current ctc", "current salary", "current package", "present ctc", "present salary", "mention your current ctc"]):
        return profile.get("current_ctc", "")
    elif re.search(r'\bctc\b', text) and "expected" not in text:
        # Standalone "CTC" without "expected" — assume current
        return profile.get("current_ctc", "")
    elif re.search(r'\bsalary\b', text) and "expected" not in text and "desired" not in text:
        return profile.get("current_ctc", "")
    elif any(k in text for k in ["notice", "joining", "start date", "notice period", "when can you join"]):
        if options:
            for opt in options:
                if any(x in opt.lower() for x in ["immediate", "15", "1 month", "2 week"]):
                    return opt
            return options[0]
        return profile.get("notice_period", "")
    
    # --- Education ---
    elif any(k in text for k in ["education", "degree", "qualification", "highest qualification"]):
        if options:
            edu = profile.get("education_level", "").lower()
            for opt in options:
                if any(k in opt.lower() for k in ["master", "ms", "m.s", "pg", "post"]):
                    return opt
            for opt in options:
                if any(k in opt.lower() for k in ["bachelor", "b.tech", "ug", "under"]):
                    return opt
        return profile.get("education_level", "")
    elif any(k in text for k in ["graduation", "batch", "passing year", "year of passing"]):
        return str(profile.get("graduation_year", ""))
    elif any(k in text for k in ["gpa", "cgpa", "grade", "percentage"]):
        return profile.get("gpa", "")
    
    # --- Personal ---
    elif any(k in text for k in ["gender"]):
        if options:
            gender = profile.get("gender", "").lower()
            for opt in options:
                if gender in opt.lower():
                    return opt
        return profile.get("gender", "")
    elif any(k in text for k in ["date of birth", "dob", "birth date", "birthday"]):
        return profile.get("date_of_birth", "")
    elif any(k in text for k in ["age"]):
        return profile.get("date_of_birth", "")
    elif any(k in text for k in ["relocat", "willing to move"]):
        return _handle_yes(options, "Yes")
    elif any(k in text for k in ["authorized", "visa", "work permit"]):
        return _handle_yes(options, "Yes")
    
    # --- Content fields ---
    elif any(k in text for k in ["cover letter", "why this role", "about yourself", "about you",
                                   "professional summary", "professional background", "summary"]):
        return profile.get("cover_letter_template", "")
    elif any(k in text for k in ["additional", "comment", "note", "message", "anything else"]):
        return "Looking forward to this opportunity."
    
    # --- Boolean fields ---
    elif any(k in text for k in ["night shift", "rotational", "flexible", "weekend"]):
        return _handle_yes(options, "Yes")
    elif any(k in text for k in ["background check", "drug test", "consent"]):
        return _handle_yes(options, "Yes")
    elif any(k in text for k in ["travel", "commute"]):
        return _handle_yes(options, "Yes")
    elif any(k in text for k in ["internship", "duration", "months"]):
        return "6"
        
    # Checkbox/Radio fallback
    if field_type in ["radio", "checkbox"]:
        return _handle_yes(options, "Yes")
        
    # Default fallback — don't fill with garbage
    return None

def _handle_yes(options, default_val):
    if options:
        for opt in options:
            if opt.lower() in ["yes", "true", "y", "1"]:
                return opt
        return options[0]  # Pick first if no 'yes'
    return default_val

def _pick_experience_range(options, years):
    """Pick the best matching experience range from dropdown options."""
    # Try exact match first
    for opt in options:
        # Match patterns like "1-2", "2-3", etc.
        match = re.search(r'(\d+)\s*[-–to]\s*(\d+)', opt)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if low <= years <= high:
                return opt
    
    # Try "X+ years" pattern
    for opt in options:
        match = re.search(r'(\d+)\+', opt)
        if match and years >= int(match.group(1)):
            return opt
    
    # Fresher
    if years <= 1:
        for opt in options:
            if "fresher" in opt.lower() or "0" in opt:
                return opt
    
    # Fallback to first option
    if options:
        return options[0]
    return str(years)
