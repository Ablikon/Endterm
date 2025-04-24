"""
Distributed AdaptiveShield Example

This example demonstrates how to use AdaptiveShield with Redis for distributed rate limiting
across multiple application instances.
"""

import time
import uuid
import redis
import json
import logging
from flask import Flask, request, jsonify, g, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
from typing import Dict, Any, Optional, Callable, List, Tuple
from enum import Enum, auto
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DistributedShield")

from adaptive_shield import AdaptiveShield, RateLimitStrategy

redis_client = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True
)

class RedisKeys:
    def __init__(self, namespace: str = "adaptive_shield"):
        self.namespace = namespace
        self.global_stats_key = f"{namespace}:global_stats"
        self.routes_key = f"{namespace}:routes"
        self.clients_key = f"{namespace}:clients"
        self.route_stats_prefix = f"{namespace}:route_stats:"
        self.client_stats_prefix = f"{namespace}:client_stats:"
        self.route_counters_prefix = f"{namespace}:route_counters:"
        self.client_counters_prefix = f"{namespace}:client_counters:"
        self.route_config_prefix = f"{namespace}:route_config:"
        self.auto_adapt_prefix = f"{namespace}:auto_adapt:"

class RateLimitStrategy(Enum):
    TOKEN_BUCKET = auto()
    LEAKY_BUCKET = auto()
    FIXED_WINDOW = auto()
    SLIDING_WINDOW = auto()
    ADAPTIVE_WINDOW = auto()

class DistributedAdaptiveShield:
    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        namespace: str = "adaptive_shield",
        default_limit: int = 100,
        default_window: int = 60,
        default_strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET,
        monitor_interval: int = 10,
        auto_adapt: bool = False
    ):
        self.redis = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True
        )
        self.keys = RedisKeys(namespace)
        self.default_limit = default_limit
        self.default_window = default_window
        self.default_strategy = default_strategy
        self.monitor_interval = monitor_interval
        self.auto_adapt = auto_adapt
        
        self._initialize_redis()
        
        if self.auto_adapt:
            self._start_monitor_thread()

    def _initialize_redis(self):
        if not self.redis.exists(self.keys.global_stats_key):
            self.redis.hset(self.keys.global_stats_key, mapping={
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0
            })
        
        if not self.redis.exists(self.keys.routes_key):
            self.redis.sadd(self.keys.routes_key, "/")
            
        if not self.redis.exists(self.keys.clients_key):
            self.redis.sadd(self.keys.clients_key, "default")
            
        for route in self.redis.smembers(self.keys.routes_key):
            route_stats_key = f"{self.keys.route_stats_prefix}{route}"
            if not self.redis.exists(route_stats_key):
                self.redis.hset(route_stats_key, mapping={
                    "total_requests": 0,
                    "allowed_requests": 0,
                    "rejected_requests": 0
                })
                
            route_config_key = f"{self.keys.route_config_prefix}{route}"
            if not self.redis.exists(route_config_key):
                self.redis.hset(route_config_key, mapping={
                    "limit": self.default_limit,
                    "window": self.default_window,
                    "strategy": self.default_strategy.name
                })

    def _start_monitor_thread(self):
        def monitor_loop():
            while True:
                try:
                    self._monitor_and_adapt()
                except Exception as e:
                    logging.error(f"Error in monitor thread: {e}")
                time.sleep(self.monitor_interval)
                
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()

    def _monitor_and_adapt(self):
        for route in self.redis.smembers(self.keys.routes_key):
            stats_key = f"{self.keys.route_stats_prefix}{route}"
            config_key = f"{self.keys.route_config_prefix}{route}"
            auto_adapt_key = f"{self.keys.auto_adapt_prefix}{route}"
            
            stats = self.redis.hgetall(stats_key)
            config = self.redis.hgetall(config_key)
            
            if not stats or not config:
                continue
                
            total = int(stats.get("total_requests", 0))
            allowed = int(stats.get("allowed_requests", 0))
            rejected = int(stats.get("rejected_requests", 0))
            
            if total == 0:
                continue
                
            rejection_rate = rejected / total
            
            history = self.redis.lrange(auto_adapt_key, 0, -1)
            history = [float(r) for r in history]
            
            if len(history) >= 10:
                self.redis.ltrim(auto_adapt_key, -9, -1)
            
            self.redis.rpush(auto_adapt_key, rejection_rate)
            
            if len(history) < 3:
                continue
                
            avg_rejection = sum(history) / len(history)
            
            if avg_rejection > 0.2 and rejection_rate > 0.25:
                current_limit = int(config.get("limit", self.default_limit))
                new_limit = int(current_limit * 1.2)
                self.redis.hset(config_key, "limit", new_limit)
                logging.info(f"Auto-adapted {route}: increased limit from {current_limit} to {new_limit}")
                
            elif avg_rejection < 0.05 and rejection_rate < 0.03:
                current_limit = int(config.get("limit", self.default_limit))
                new_limit = max(10, int(current_limit * 0.9))
                self.redis.hset(config_key, "limit", new_limit)
                logging.info(f"Auto-adapted {route}: decreased limit from {current_limit} to {new_limit}")
    
    def check_request(self, client_id: str, route: str) -> bool:
        lua_script = """
        local keys = {
            KEYS[1], -- global_stats
            KEYS[2], -- route stats
            KEYS[3], -- client stats
            KEYS[4], -- route config
            KEYS[5], -- route counters
            KEYS[6], -- client counters
            KEYS[7], -- routes set
            KEYS[8]  -- clients set
        }

        local args = {
            ARGV[1], -- current time
            ARGV[2], -- client_id
            ARGV[3], -- route
            ARGV[4], -- strategy name
            ARGV[5], -- default_limit
            ARGV[6]  -- default_window
        }

        -- Ensure route and client exist in sets
        redis.call('SADD', keys[7], args[3])
        redis.call('SADD', keys[8], args[2])

        -- Get or create config
        local config = redis.call('HGETALL', keys[4])
        local limit = tonumber(ARGV[5])
        local window = tonumber(ARGV[6])
        local strategy = ARGV[4]

        if next(config) ~= nil then
            local i = 1
            while i <= #config do
                if config[i] == 'limit' then
                    limit = tonumber(config[i+1])
                elseif config[i] == 'window' then
                    window = tonumber(config[i+1])
                elseif config[i] == 'strategy' then
                    strategy = config[i+1]
                end
                i = i + 2
            end
        else
            redis.call('HSET', keys[4], 'limit', limit, 'window', window, 'strategy', strategy)
        end

        -- Increment request counters
        redis.call('HINCRBY', keys[1], 'total_requests', 1)
        redis.call('HINCRBY', keys[2], 'total_requests', 1)
        redis.call('HINCRBY', keys[3], 'total_requests', 1)

        local current_time = tonumber(args[1])
        local allowed = false

        if strategy == 'TOKEN_BUCKET' then
            local last_time_key = 'last_time'
            local tokens_key = 'tokens'
            
            local last_time = tonumber(redis.call('HGET', keys[5], last_time_key) or 0)
            local tokens = tonumber(redis.call('HGET', keys[5], tokens_key) or limit)
            
            -- Calculate new token count
            local new_tokens = math.min(limit, tokens + ((current_time - last_time) * limit / window))
            
            if new_tokens >= 1 then
                new_tokens = new_tokens - 1
                allowed = true
            end
            
            redis.call('HSET', keys[5], last_time_key, current_time)
            redis.call('HSET', keys[5], tokens_key, new_tokens)
        
        elseif strategy == 'LEAKY_BUCKET' then
            local queue_key = 'queue'
            local last_leak_key = 'last_leak'
            
            local queue = tonumber(redis.call('HGET', keys[5], queue_key) or 0)
            local last_leak = tonumber(redis.call('HGET', keys[5], last_leak_key) or current_time)
            
            -- Calculate leakage
            local leak_rate = limit / window
            local leaked = math.floor((current_time - last_leak) * leak_rate)
            queue = math.max(0, queue - leaked)
            
            if queue < limit then
                queue = queue + 1
                allowed = true
            end
            
            redis.call('HSET', keys[5], queue_key, queue)
            redis.call('HSET', keys[5], last_leak_key, current_time)
            
        elseif strategy == 'FIXED_WINDOW' then
            local window_key = math.floor(current_time / window)
            local requests = tonumber(redis.call('HGET', keys[5], window_key) or 0)
            
            if requests < limit then
                redis.call('HSET', keys[5], window_key, requests + 1)
                allowed = true
            end
            
            -- Clean up old windows (keep only current)
            local keys_to_del = {}
            local all_keys = redis.call('HKEYS', keys[5])
            for i, k in ipairs(all_keys) do
                if k ~= tostring(window_key) and k ~= 'limit' and k ~= 'window' and k ~= 'strategy' then
                    table.insert(keys_to_del, k)
                end
            end
            if #keys_to_del > 0 then
                redis.call('HDEL', keys[5], unpack(keys_to_del))
            end
            
        elseif strategy == 'SLIDING_WINDOW' then
            local window_start = current_time - window
            local count = 0
            
            -- Count requests in window
            local all_keys = redis.call('HKEYS', keys[5])
            local all_vals = redis.call('HVALS', keys[5])
            local keys_to_del = {}
            
            for i, k in ipairs(all_keys) do
                if string.match(k, '^ts:') then
                    local ts = tonumber(string.sub(k, 4))
                    if ts > window_start then
                        count = count + tonumber(all_vals[i])
                    else
                        table.insert(keys_to_del, k)
                    end
                end
            end
            
            -- Clean up old entries
            if #keys_to_del > 0 then
                redis.call('HDEL', keys[5], unpack(keys_to_del))
            end
            
            if count < limit then
                -- Add the new request
                local ts_key = 'ts:' .. current_time
                redis.call('HINCRBY', keys[5], ts_key, 1)
                allowed = true
            end
            
        elseif strategy == 'ADAPTIVE_WINDOW' then
            local window_start = current_time - window
            local count = 0
            local load = 0
            
            -- Count requests in window and calculate load
            local all_keys = redis.call('HKEYS', keys[5])
            local all_vals = redis.call('HVALS', keys[5])
            local keys_to_del = {}
            
            for i, k in ipairs(all_keys) do
                if string.match(k, '^ts:') then
                    local ts = tonumber(string.sub(k, 4))
                    if ts > window_start then
                        count = count + tonumber(all_vals[i])
                        -- Recent requests contribute more to load
                        local age_factor = 1 - ((current_time - ts) / window)
                        load = load + (tonumber(all_vals[i]) * age_factor)
                    else
                        table.insert(keys_to_del, k)
                    end
                end
            end
            
            -- Clean up old entries
            if #keys_to_del > 0 then
                redis.call('HDEL', keys[5], unpack(keys_to_del))
            end
            
            -- Adjust effective limit based on load
            local load_factor = 1.0
            if count > 0 then
                load_factor = math.max(0.5, math.min(1.0, 1.0 - (load / limit / 2)))
            end
            local effective_limit = math.max(1, math.floor(limit * load_factor))
            
            if count < effective_limit then
                -- Add the new request
                local ts_key = 'ts:' .. current_time
                redis.call('HINCRBY', keys[5], ts_key, 1)
                allowed = true
            end
        end

        -- Update allowed/rejected counts
        if allowed then
            redis.call('HINCRBY', keys[1], 'allowed_requests', 1)
            redis.call('HINCRBY', keys[2], 'allowed_requests', 1)
            redis.call('HINCRBY', keys[3], 'allowed_requests', 1)
        else
            redis.call('HINCRBY', keys[1], 'rejected_requests', 1)
            redis.call('HINCRBY', keys[2], 'rejected_requests', 1)
            redis.call('HINCRBY', keys[3], 'rejected_requests', 1)
        end

        return allowed and 1 or 0
        """
        
        current_time = time.time()
        
        self._ensure_route(route)
        self._ensure_client(client_id)
        
        route_stats_key = f"{self.keys.route_stats_prefix}{route}"
        client_stats_key = f"{self.keys.client_stats_prefix}{client_id}"
        route_config_key = f"{self.keys.route_config_prefix}{route}"
        route_counters_key = f"{self.keys.route_counters_prefix}{route}:{client_id}"
        client_counters_key = f"{self.keys.client_counters_prefix}{client_id}:{route}"
        
        try:
            result = self.redis.eval(
                lua_script,
                8,  # num keys
                self.keys.global_stats_key,
                route_stats_key,
                client_stats_key,
                route_config_key,
                route_counters_key,
                client_counters_key,
                self.keys.routes_key,
                self.keys.clients_key,
                # args
                current_time,
                client_id,
                route,
                self.default_strategy.name,
                self.default_limit,
                self.default_window
            )
            
            return bool(result)
        except Exception as e:
            logging.error(f"Error checking rate limit: {e}")
            return True 
    
    def _ensure_route(self, route: str):
        if not self.redis.sismember(self.keys.routes_key, route):
            self.redis.sadd(self.keys.routes_key, route)
            
            route_stats_key = f"{self.keys.route_stats_prefix}{route}"
            self.redis.hset(route_stats_key, mapping={
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0
            })
            
            route_config_key = f"{self.keys.route_config_prefix}{route}"
            self.redis.hset(route_config_key, mapping={
                "limit": self.default_limit,
                "window": self.default_window,
                "strategy": self.default_strategy.name
            })
    
    def _ensure_client(self, client_id: str):
        if not self.redis.sismember(self.keys.clients_key, client_id):
            self.redis.sadd(self.keys.clients_key, client_id)
            
            client_stats_key = f"{self.keys.client_stats_prefix}{client_id}"
            self.redis.hset(client_stats_key, mapping={
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0
            })
    
    def set_route_limit(
        self, 
        route: str, 
        limit: int, 
        window: int, 
        strategy: RateLimitStrategy
    ):
        self._ensure_route(route)
        
        route_config_key = f"{self.keys.route_config_prefix}{route}"
        self.redis.hset(route_config_key, mapping={
            "limit": limit,
            "window": window,
            "strategy": strategy.name
        })
    
    def get_global_stats(self) -> Dict[str, int]:
        stats = self.redis.hgetall(self.keys.global_stats_key)
        return {k: int(v) for k, v in stats.items()}
    
    def get_route_stats(self, route: str) -> Dict[str, Any]:
        self._ensure_route(route)
        
        route_stats_key = f"{self.keys.route_stats_prefix}{route}"
        route_config_key = f"{self.keys.route_config_prefix}{route}"
        
        stats = self.redis.hgetall(route_stats_key)
        config = self.redis.hgetall(route_config_key)
        
        result = {
            "total_requests": int(stats.get("total_requests", 0)),
            "allowed_requests": int(stats.get("allowed_requests", 0)),
            "rejected_requests": int(stats.get("rejected_requests", 0)),
            "config": {
                "limit": int(config.get("limit", self.default_limit)),
                "window": int(config.get("window", self.default_window)),
                "strategy": config.get("strategy", self.default_strategy.name)
            }
        }
        
        return result
    
    def get_client_stats(self, client_id: str) -> Dict[str, int]:
        self._ensure_client(client_id)
        
        client_stats_key = f"{self.keys.client_stats_prefix}{client_id}"
        stats = self.redis.hgetall(client_stats_key)
        
        return {k: int(v) for k, v in stats.items()}
    
    def get_all_routes(self) -> List[str]:
        return list(self.redis.smembers(self.keys.routes_key))
    
    def get_all_clients(self) -> List[str]:
        return list(self.redis.smembers(self.keys.clients_key))
    
    def reset_stats(self):
        self.redis.hset(self.keys.global_stats_key, mapping={
            "total_requests": 0,
            "allowed_requests": 0,
            "rejected_requests": 0
        })
        
        for route in self.redis.smembers(self.keys.routes_key):
            route_stats_key = f"{self.keys.route_stats_prefix}{route}"
            self.redis.hset(route_stats_key, mapping={
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0
            })
        
        for client_id in self.redis.smembers(self.keys.clients_key):
            client_stats_key = f"{self.keys.client_stats_prefix}{client_id}"
            self.redis.hset(client_stats_key, mapping={
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0
            })
            

shield = DistributedAdaptiveShield(
    redis_host="localhost",
    redis_port=6379,
    default_limit=100,
    default_window=60,
    default_strategy=RateLimitStrategy.TOKEN_BUCKET,
    auto_adapt=True
)

shield.set_route_limit("/api/public", 200, 60, RateLimitStrategy.SLIDING_WINDOW)
shield.set_route_limit("/api/users", 50, 60, RateLimitStrategy.LEAKY_BUCKET)
shield.set_route_limit("/api/admin", 20, 60, RateLimitStrategy.ADAPTIVE_WINDOW)

app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

def get_client_id() -> str:
    """Extract client ID from request."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"api:{api_key}"
    
    client_ip = request.remote_addr
    return f"ip:{client_ip}"

def rate_limit(f: Callable) -> Callable:
    """Decorator to apply rate limiting to a route."""
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
            
            stats = shield.get_route_stats(route)
            retry_after = max(1, min(60, stats["config"]["limit"] * 100))
            response.headers["Retry-After"] = str(int(retry_after))
            
            return response
        
        return f(*args, **kwargs)
    
    return decorated_function

APP_INSTANCE_ID = str(uuid.uuid4())[:8]

@app.before_request
def before_request():
    g.start_time = time.time()

@app.after_request
def after_request(response: Response) -> Response:
    if hasattr(g, 'start_time'):
        duration = time.time() - g.start_time
        
        logger.info(f"Request: {request.method} {request.path} | "
                  f"Client: {getattr(g, 'client_id', 'unknown')} | "
                  f"Status: {response.status_code} | "
                  f"Duration: {duration:.4f}s")
    
    return response

@app.route("/")
def root():
    """Welcome endpoint with no rate limiting."""
    return jsonify({
        "message": "Redis Distributed Rate Limiting Example",
        "endpoints": [
            "/api/public",
            "/api/users",
            "/api/admin",
            "/stats",
            "/stats/client/<client_id>",
            "/reset/<client_id>"
        ]
    })

@app.route("/api/public")
@rate_limit
def public_endpoint():
    """Public endpoint with higher rate limit."""
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
    """Users endpoint with medium rate limit."""
    time.sleep(0.05) 
    return jsonify({
        "message": "This is a users endpoint with medium rate limit (50 requests/min)",
        "data": {
            "timestamp": time.time(),
            "users": [f"user_{i}" for i in range(5)]
        }
    })

@app.route("/api/admin")
@rate_limit
def admin_endpoint():
    """Admin endpoint with low rate limit."""
    time.sleep(0.1) 
    return jsonify({
        "message": "This is an admin endpoint with low rate limit (20 requests/min)",
        "data": {
            "timestamp": time.time(),
            "status": "ok",
            "system_load": 0.75
        }
    })

@app.route("/stats")
def stats_endpoint():
    """Get global statistics."""
    client_ids = set()
    
    for key in redis_client.keys(f"{shield.keys.route_stats_prefix}*"):
        parts = key.split(":")
        if len(parts) >= 2:
            client_ids.add(parts[1])
    
    total_requests = 0
    total_allowed = 0
    total_rejected = 0
    
    client_stats = {}
    for client_id in client_ids:
        stats = shield.get_route_stats(client_id)
        client_stats[client_id] = stats
        
        total_requests += stats["total_requests"]
        total_allowed += stats["allowed_requests"]
        total_rejected += stats["rejected_requests"]
    
    return jsonify({
        "instance_id": APP_INSTANCE_ID,
        "total_requests": total_requests,
        "total_allowed": total_allowed, 
        "total_rejected": total_rejected,
        "acceptance_rate": total_allowed / total_requests if total_requests > 0 else 0,
        "rejection_rate": total_rejected / total_requests if total_requests > 0 else 0,
        "clients": client_stats
    })

@app.route("/stats/client/<client_id>")
def client_stats(client_id: str):
    """Get statistics for a specific client."""
    return jsonify({
        "instance_id": APP_INSTANCE_ID,
        "client_id": client_id,
        "stats": shield.get_route_stats(client_id)
    })

@app.route("/reset/<client_id>")
def reset_client(client_id: str):
    """Reset rate limiting state for a client."""
    shield.reset_stats()
    return jsonify({
        "instance_id": APP_INSTANCE_ID,
        "message": f"Rate limiting data for client {client_id} has been reset"
    })

if __name__ == "__main__":
    try:
        redis_client.ping()
        logger.info("Redis connection successful")
    except redis.ConnectionError:
        logger.error("Redis connection failed. Make sure Redis is running.")
        logger.info("You can start Redis with: docker run -d -p 6379:6379 redis")
        exit(1)
    
    logger.info(f"Starting distributed rate limiter instance {APP_INSTANCE_ID}")
    app.run(host="0.0.0.0", port=5001, debug=True) 