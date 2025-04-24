import time
import uuid
import uvicorn
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from adaptive_shield import AdaptiveShield, RateLimitStrategy

shield = AdaptiveShield(
    default_limit=100,
    default_window=60,
    default_strategy=RateLimitStrategy.TOKEN_BUCKET,
    monitor_interval=10,
    auto_adapt=True
)

shield.set_route_limit("/api/public", 200, 60, RateLimitStrategy.SLIDING_WINDOW)
shield.set_route_limit("/api/users", 50, 60, RateLimitStrategy.LEAKY_BUCKET)
shield.set_route_limit("/api/admin", 20, 60, RateLimitStrategy.ADAPTIVE_WINDOW)

app = FastAPI(
    title="Rate Limited API Example",
    description="A simple API with AdaptiveShield rate limiting",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_client_id(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"

async def check_rate_limit(
    request: Request,
    client_id: str = Depends(get_client_id)
) -> str:
    route = request.url.path
    
    allowed = shield.check_request(client_id, route)
    
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Too Many Requests",
                "message": "Rate limit exceeded. Please slow down your requests.",
                "client_id": client_id,
                "route": route
            }
        )
    
    return client_id

@app.get("/")
async def root():
    return {
        "message": "Welcome to the Rate Limited API Example",
        "endpoints": [
            "/api/public",
            "/api/users",
            "/api/admin",
            "/stats"
        ]
    }

@app.get("/api/public", dependencies=[Depends(check_rate_limit)])
async def public_endpoint():
    time.sleep(0.01)
    return {
        "message": "This is a public endpoint with higher rate limit (200 requests/min)",
        "data": {
            "timestamp": time.time(),
            "id": str(uuid.uuid4())
        }
    }

@app.get("/api/users", dependencies=[Depends(check_rate_limit)])
async def users_endpoint():
    time.sleep(0.05)
    return {
        "message": "This is a users endpoint with medium rate limit (50 requests/min)",
        "data": {
            "timestamp": time.time(),
            "users": [f"user_{i}" for i in range(5)]
        }
    }

@app.get("/api/admin", dependencies=[Depends(check_rate_limit)])
async def admin_endpoint():
    time.sleep(0.1)
    return {
        "message": "This is an admin endpoint with low rate limit (20 requests/min)",
        "data": {
            "timestamp": time.time(),
            "status": "ok",
            "system_load": 0.75
        }
    }

@app.get("/stats")
async def stats_endpoint(client_id: Optional[str] = None):
    if client_id:
        return shield.get_client_stats(client_id)
    else:
        return shield.get_global_stats()

@app.get("/stats/client/{client_id}")
async def client_stats(client_id: str):
    return shield.get_client_stats(client_id)

@app.get("/stats/route/{route}")
async def route_stats(route: str):
    if not route.startswith("/"):
        route = f"/{route}"
    if not route.startswith("/api/") and route != "/":
        route = f"/api/{route}"
    
    return shield.get_route_stats(route)

@app.exception_handler(429)
async def rate_limit_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=429,
        content=exc.detail,
        headers={"Retry-After": "60"}
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)