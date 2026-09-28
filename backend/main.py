from fastapi import FastAPI

app = FastAPI(
    title="DietRx API",
    version="1.0.0"
)

@app.get("/")
def root():
    return {
        "message": "DietRx FastAPI Backend is running"
    }

@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }