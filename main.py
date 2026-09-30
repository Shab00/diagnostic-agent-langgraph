from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import fault, report
from simulator import list_scenarios

app = FastAPI(title="Diagnostic Repair Ranking Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fault.router)
app.include_router(report.router)


@app.get("/scenarios")
def get_scenarios() -> list[str]:
    return list_scenarios()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
