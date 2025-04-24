import time
import uuid
import json
from functools import wraps
from typing import Dict, Any, Optional, Callable, List

from flask import Flask, request, jsonify, g, Response
from werkzeug.middleware.proxy_fix import ProxyFix

from adaptive_shield import AdaptiveShield, RateLimitStrategy

shield = AdaptiveShield(
    default_limit=100,
    default_window=60,
    default_strategy=RateLimitStrategy.TOKEN_BUCKET,
    monitor_interval=10,
    metrics_retention=3600,
    auto_adapt=True
)

shield.set_route_limit("/api/public", 200, 60, RateLimitStrategy.SLIDING_WINDOW)
shield.set_route_limit("/api/users", 50, 60, RateLimitStrategy.LEAKY_BUCKET)
shield.set_route_limit("/api/admin", 20, 60, RateLimitStrategy.ADAPTIVE_WINDOW)

shield.set_client_limit("premium_client_1", 500, 60)

shield.set_client_route_limit("premium_client_1", "/api/users", 200, 60)

app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

def get_client_id() -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    
    client_ip = request.remote_addr
    return f"ip:{client_ip}"

def rate_limit(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_id = get_client_id()
        
        g.client_id = client_id
        
        route = request.path
        
        allowed = shield.check_request(client_id, route)
        
        if not allowed:
            response = jsonify({
                "error": "Too Many Requests",
                "message": "Rate limit exceeded. Please slow down your requests.",
                "client_id": client_id,
                "route": route
            })
            response.status_code = 429
            
            stats = shield.get_client_stats(client_id)
            retry_after = stats.get("time_until_reset", 60)
            response.headers["Retry-After"] = str(int(retry_after))
            
            return response
        
        return f(*args, **kwargs)
    
    return decorated_function

@app.before_request
def before_request():
    g.start_time = time.time()

@app.after_request
def after_request(response: Response) -> Response:
    if hasattr(g, 'start_time'):
        duration = time.time() - g.start_time
        
        app.logger.info(
            f"Request: {request.method} {request.path} | "
            f"Client: {getattr(g, 'client_id', 'unknown')} | "
            f"Status: {response.status_code} | "
            f"Duration: {duration:.4f}s"
        )
    
    return response

@app.route("/")
def root():
    return jsonify({
        "message": "Welcome to the Flask Rate Limited API Example",
        "endpoints": [
            "/api/public",
            "/api/users",
            "/api/admin",
            "/stats",
            "/stats/client/<client_id>",
            "/stats/route/<route>"
        ]
    })

@app.route("/api/public")
@rate_limit
def public_endpoint():
    time.sleep(0.01)
    return jsonify({
        "message": "This is a public endpoint with higher rate limit (200 requests/min)",
        "data": {
            "timestamp": time.time(),
            "id": str(uuid.uuid4())
        }
    })

@app.route("/api/users")
@rate_limit
def users_endpoint():
    time.sleep(0.05)
    
    users = [{"id": i, "name": f"User {i}"} for i in range(5)]
    
    return jsonify({
        "message": "This is a users endpoint with medium rate limit (50 requests/min)",
        "data": {
            "timestamp": time.time(),
            "users": users
        }
    })

@app.route("/api/admin")
@rate_limit
def admin_endpoint():
    time.sleep(0.1)
    
    stats = shield.get_global_stats()
    
    return jsonify({
        "message": "This is an admin endpoint with low rate limit (20 requests/min)",
        "data": {
            "timestamp": time.time(),
            "status": "ok",
            "system_load": 0.75,
            "rate_limiting_stats": {
                "total_requests": stats.get("total_requests", 0),
                "allowed_requests": stats.get("allowed_requests", 0),
                "rejected_requests": stats.get("rejected_requests", 0),
                "rejection_rate": stats.get("rejection_rate", 0)
            }
        }
    })

@app.route("/api/test_strategies")
def test_strategies():
    strategies_info = []
    
    for strategy in RateLimitStrategy:
        strategies_info.append({
            "name": strategy.name,
            "value": strategy.value,
            "description": get_strategy_description(strategy)
        })
    
    return jsonify({
        "message": "Available rate limiting strategies",
        "strategies": strategies_info,
        "usage": "To test different strategies, configure specific routes with different strategies."
    })

def get_strategy_description(strategy: RateLimitStrategy) -> str:
    descriptions = {
        RateLimitStrategy.TOKEN_BUCKET: (
            "Token Bucket algorithm - Handles bursts well while maintaining a "
            "consistent average rate. Each request consumes one token, and tokens "
            "are replenished at a constant rate."
        ),
        RateLimitStrategy.SLIDING_WINDOW: (
            "Sliding Window Counter - Divides time into discrete windows and counts "
            "requests in each window. Provides more accurate counting than fixed windows "
            "while using less memory than a true sliding log."
        ),
        RateLimitStrategy.LEAKY_BUCKET: (
            "Leaky Bucket algorithm - Models rate limiting as a bucket with a leak, "
            "providing consistent output rate regardless of input bursts. Good for "
            "smoothing out traffic and preventing downstream services from being overwhelmed."
        ),
        RateLimitStrategy.ADAPTIVE_WINDOW: (
            "Adaptive Window - Dynamically adjusts window size and request limit based "
            "on traffic patterns and system load. Provides optimal balance between "
            "protection and throughput."
        )
    }
    return descriptions.get(strategy, "Unknown strategy")

@app.route("/stats")
def stats_endpoint():
    return jsonify(shield.get_global_stats())

@app.route("/stats/client/<client_id>")
def client_stats(client_id: str):
    return jsonify(shield.get_client_stats(client_id))

@app.route("/stats/route/<path:route>")
def route_stats(route: str):
    if not route.startswith("/"):
        route = f"/{route}"
    
    return jsonify(shield.get_route_stats(route))

@app.route("/api/load_test/<test_type>")
@rate_limit
def load_test(test_type: str):
    if test_type == "slow":
        time.sleep(0.5)
    else:
        time.sleep(0.01)
    
    if test_type == "error":
        return jsonify({"error": "Simulated error"}), 500
    elif test_type == "large":
        large_data = [{"id": i, "data": "X" * 100} for i in range(100)]
        return jsonify({"data": large_data})
    else:
        return jsonify({
            "test_type": test_type,
            "timestamp": time.time(),
            "message": "Load test successful"
        })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)