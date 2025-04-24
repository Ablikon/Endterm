import time
import logging
from typing import Dict, Optional, List, Tuple, Any, Set
from collections import defaultdict
import threading

from .strategies import (
    BaseRateLimiter,
    TokenBucketRateLimiter, 
    LeakyBucketRateLimiter,
    FixedWindowRateLimiter,
    SlidingWindowRateLimiter,
    AdaptiveWindowRateLimiter
)

logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AdaptiveShield")


class RateLimitExceeded(Exception):
    def __init__(self, client_id: str, route: str, retry_after: int = 60):
        self.client_id = client_id
        self.route = route
        self.retry_after = retry_after
        message = f"Rate limit exceeded for client {client_id} on route {route}"
        super().__init__(message)


class AdaptiveShield:
    STRATEGIES = {
        "token_bucket": TokenBucketRateLimiter,
        "leaky_bucket": LeakyBucketRateLimiter,
        "fixed_window": FixedWindowRateLimiter,
        "sliding_window": SlidingWindowRateLimiter,
        "adaptive_window": AdaptiveWindowRateLimiter
    }
    
    def __init__(self, 
                default_limit: int = 100,
                default_window: int = 60,
                default_strategy: str = "token_bucket",
                monitoring_interval: int = 10):
        if default_strategy not in self.STRATEGIES:
            raise ValueError(f"Unknown strategy: {default_strategy}. " 
                           f"Available strategies: {list(self.STRATEGIES.keys())}")
        
        self.default_limit = default_limit
        self.default_window = default_window
        self.default_strategy = default_strategy
        
        self._lock = threading.RLock()
        
        self._route_limiters: Dict[str, Tuple[int, int, str, BaseRateLimiter]] = {}
        
        self._stats = defaultdict(lambda: defaultdict(lambda: {"allowed": 0, "blocked": 0}))
        
        self._active_clients: Set[str] = set()
        
        self._last_stats_reset = time.time()
        
        self._monitoring_interval = monitoring_interval
        if monitoring_interval > 0:
            self._monitoring_active = True
            self._monitor_thread = threading.Thread(
                target=self._monitoring_task, 
                daemon=True
            )
            self._monitor_thread.start()
        else:
            self._monitoring_active = False
    
    def _get_limiter_for_route(self, route: str) -> Tuple[int, int, str, BaseRateLimiter]:
        if route in self._route_limiters:
            return self._route_limiters[route]
        
        for pattern, limiter_info in self._route_limiters.items():
            if pattern.endswith('*') and route.startswith(pattern[:-1]):
                return limiter_info
        
        limit = self.default_limit
        window = self.default_window
        strategy_name = self.default_strategy
        
        limiter_class = self.STRATEGIES[strategy_name]
        limiter = limiter_class(limit, window)
        
        self._route_limiters[route] = (limit, window, strategy_name, limiter)
        
        return limit, window, strategy_name, limiter
    
    def configure_route(self, 
                       route: str, 
                       limit: int, 
                       window: int, 
                       strategy: str = None) -> None:
        with self._lock:
            strategy_name = strategy or self.default_strategy
            
            if strategy_name not in self.STRATEGIES:
                raise ValueError(f"Unknown strategy: {strategy_name}. "
                               f"Available strategies: {list(self.STRATEGIES.keys())}")
                
            limiter_class = self.STRATEGIES[strategy_name]
            limiter = limiter_class(limit, window)
            
            self._route_limiters[route] = (limit, window, strategy_name, limiter)
            
            logger.info(f"Configured rate limit for route {route}: "
                       f"{limit} requests per {window}s using {strategy_name}")
    
    def check_request(self, client_id: str, route: str) -> bool:
        with self._lock:
            self._active_clients.add(client_id)
            
            _, _, _, limiter = self._get_limiter_for_route(route)
            
            allowed = limiter.check_request(client_id)
            
            if allowed:
                self._stats[client_id][route]["allowed"] += 1
            else:
                self._stats[client_id][route]["blocked"] += 1
            
            return allowed
    
    def check_request_or_raise(self, client_id: str, route: str) -> None:
        if not self.check_request(client_id, route):
            _, window, _, _ = self._get_limiter_for_route(route)
            retry_after = min(window, 60)
            raise RateLimitExceeded(client_id, route, retry_after)
    
    def reset_client(self, client_id: str) -> None:
        with self._lock:
            for _, _, _, limiter in self._route_limiters.values():
                limiter.reset(client_id)
            
            if client_id in self._stats:
                del self._stats[client_id]
            
            if client_id in self._active_clients:
                self._active_clients.remove(client_id)
    
    def get_statistics(self, 
                      client_id: Optional[str] = None, 
                      route: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            result = {
                "active_clients": len(self._active_clients),
                "configured_routes": len(self._route_limiters),
                "stats_since": self._last_stats_reset,
                "elapsed_time": time.time() - self._last_stats_reset
            }
            
            stats_data = {}
            
            if client_id and route:
                if client_id in self._stats and route in self._stats[client_id]:
                    stats_data = {client_id: {route: self._stats[client_id][route]}}
            elif client_id:
                if client_id in self._stats:
                    stats_data = {client_id: self._stats[client_id]}
            elif route:
                stats_data = {}
                for cid, routes in self._stats.items():
                    if route in routes:
                        if cid not in stats_data:
                            stats_data[cid] = {}
                        stats_data[cid][route] = routes[route]
            else:
                stats_data = self._stats
            
            total_allowed = 0
            total_blocked = 0
            
            for cid, routes in stats_data.items():
                for r, counts in routes.items():
                    total_allowed += counts["allowed"]
                    total_blocked += counts["blocked"]
            
            result["total_requests"] = total_allowed + total_blocked
            result["allowed_requests"] = total_allowed
            result["blocked_requests"] = total_blocked
            result["block_rate"] = (
                total_blocked / (total_allowed + total_blocked) 
                if (total_allowed + total_blocked) > 0 else 0
            )
            
            result["detailed_stats"] = stats_data
            
            return result
    
    def reset_statistics(self) -> None:
        with self._lock:
            self._stats = defaultdict(lambda: defaultdict(lambda: {"allowed": 0, "blocked": 0}))
            self._last_stats_reset = time.time()
    
    def _monitoring_task(self) -> None:
        while self._monitoring_active:
            time.sleep(self._monitoring_interval)
            
            try:
                with self._lock:
                    stats = self.get_statistics()
                    
                    logger.info(f"AdaptiveShield Monitoring: "
                               f"Active clients: {stats['active_clients']}, "
                               f"Requests: {stats['total_requests']}, "
                               f"Block rate: {stats['block_rate']:.2%}")
                    
                    if stats['block_rate'] > 0.3 and stats['total_requests'] > 10:
                        logger.warning(f"High block rate detected: {stats['block_rate']:.2%}")
            
            except Exception as e:
                logger.error(f"Error in monitoring task: {e}")
    
    def shutdown(self) -> None:
        if self._monitoring_active:
            self._monitoring_active = False
            if self._monitor_thread:
                self._monitor_thread.join(timeout=1.0)