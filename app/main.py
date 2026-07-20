from fastapi import FastAPI

app = FastAPI(title="peoplefabrix")


@app.get("/")
def read_root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "healthy"}


def dev():
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
