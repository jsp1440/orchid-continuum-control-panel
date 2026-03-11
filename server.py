from fastapi import FastAPI

app = FastAPI(
    title="Orchid Continuum Control Panel",
    description="Control panel for Orchid Continuum harvesters and database",
    version="1.0"
)


@app.get("/")
def root():
    return {
        "service": "Orchid Continuum Control Panel",
        "status": "running"
    }
