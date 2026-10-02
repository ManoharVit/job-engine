import urllib.parse

def canonicalize_url(url: str) -> str:
    """
    Standardize URL formatting and strip tracking parameters.
    """
    if not url:
        return ""
        
    parsed = urllib.parse.urlparse(url)
    
    # Standardize scheme and netloc (lowercase)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    
    # Strip common tracking params based on explicit allowlist (tracking params we strip)
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    clean_params = []
    tracking_allowlist = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source", "tracker"}
    for k, v in query_params:
        if k.lower() not in tracking_allowlist:
            clean_params.append((k, v))
            
    new_query = urllib.parse.urlencode(clean_params)
    
    # Strip fragments, standardize path (remove trailing slash unless it's root)
    path = parsed.path
    if len(path) > 1:
        path = path.rstrip('/')
        
    canonical = urllib.parse.urlunparse((scheme, netloc, path, parsed.params, new_query, ""))
    return canonical
