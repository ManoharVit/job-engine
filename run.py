import uvicorn

if __name__ == "__main__":
    # Ensure service binds to localhost by default for security
    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000, log_level="info")
