"""
AdaptiveShield - Advanced Adaptive Rate Limiting System

This module provides a comprehensive rate limiting solution for APIs and microservices,
with support for multiple strategies, dynamic adaptation, and detailed metrics.
"""

import time
import threading
import logging
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple, Callable, Union
from collections import defaultdict

from .strategies import (
    RateLimitStrategy as BaseLimitStrategy,
    TokenBucketStrategy,
    SlidingWindowCounterStrategy,
    LeakyBucketStrategy,
    AdaptiveWindowStrategy
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("AdaptiveShield")


class RateLimitStrategy(Enum):
    """Enumeration of available rate limiting strategies."""
    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW = "sliding_window"
    LEAKY_BUCKET = "leaky_bucket"
    ADAPTIVE_WINDOW = "adaptive_window"


class AdaptiveShield:
    """
    Adaptive rate limiting system for protecting APIs and microservices.
    
    Features:
    - Multiple rate limiting strategies
    - Client-specific limits
    - Route-specific limits
    - Automatic adaptation based on traffic patterns
    - Detailed metrics and monitoring
    """
    
    def __init__(
        self,
        default_limit: int = 100,
        default_window: int = 60,
        default_strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET,
        monitor_interval: int = 30,
        metrics_retention: int = 3600,
        auto_adapt: bool = True
    ):
        """
        Initialize the AdaptiveShield rate limiter.
        
        Args:
            default_limit: Default request limit per time window
            default_window: Default time window in seconds
            default_strategy: Default rate limiting strategy to use
            monitor_interval: How often to run monitoring and adaptation (seconds)
            metrics_retention: How long to keep metrics data (seconds)
            auto_adapt: Whether to automatically adapt limits based on traffic
        """
        self.default_limit = default_limit
        self.default_window = default_window
        self.default_strategy = default_strategy
        
        self._lock = threading.RLock()
        
        self._route_limits: Dict[str, Tuple[int, int, RateLimitStrategy]] = {}
        self._client_limits: Dict[str, Tuple[int, int, Optional[RateLimitStrategy]]] = {}
        self._client_route_limits: Dict[str, Dict[str, Tuple[int, int, Optional[RateLimitStrategy]]]] = defaultdict(dict)
        
        self._strategy_instances: Dict[str, Dict[str, BaseLimitStrategy]] = defaultdict(dict)
        
        self._metrics_lock = threading.RLock()
        self._request_metrics: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
        self._route_metrics: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._global_metrics = {
            "total_requests": 0,
            "allowed_requests": 0,
            "rejected_requests": 0,
            "start_time": time.time(),
            "routes": set(),
            "clients": set(),
            "processing_times": []
        }
        
        self._metrics_retention = metrics_retention
        self._monitor_interval = monitor_interval
        self._auto_adapt = auto_adapt
        
        self._monitor_thread = None
        if monitor_interval > 0:
            self._stop_monitoring = False
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
    
    def __del__(self):
        """Clean up resources when the object is destroyed."""
        self._stop_monitoring = True
        if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)
    
    def _get_strategy_instance(
        self, 
        strategy_type: RateLimitStrategy, 
        limit: int, 
        window: int
    ) -> BaseLimitStrategy:
        """
        Get or create a strategy instance of the specified type.
        
        Args:
            strategy_type: Type of rate limiting strategy to use
            limit: Request limit for this strategy
            window: Time window in seconds for this strategy
            
        Returns:
            An instance of the requested strategy
        """
        strategy_key = f"{strategy_type.value}:{limit}:{window}"
        
        with self._lock:
            if strategy_key not in self._strategy_instances:
                if strategy_type == RateLimitStrategy.TOKEN_BUCKET:
                    instance = TokenBucketStrategy(limit, window)
                elif strategy_type == RateLimitStrategy.SLIDING_WINDOW:
                    instance = SlidingWindowCounterStrategy(limit, window)
                elif strategy_type == RateLimitStrategy.LEAKY_BUCKET:
                    instance = LeakyBucketStrategy(limit, window)
                elif strategy_type == RateLimitStrategy.ADAPTIVE_WINDOW:
                    instance = AdaptiveWindowStrategy(limit, window)
                else:
                    raise ValueError(f"Unknown strategy type: {strategy_type}")
                
                self._strategy_instances[strategy_key] = instance
            
            return self._strategy_instances[strategy_key]
    
    def check_request(
        self, 
        client_id: str, 
        route: str = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Check if a request should be allowed based on rate limits.
        
        This method will check the most specific applicable limit first:
        1. Client+Route specific limit (if both are provided)
        2. Route-specific limit (if route is provided)
        3. Client-specific limit
        4. Default global limit
        
        Args:
            client_id: Identifier for the client making the request
            route: Optional API route being accessed
            metadata: Optional additional metadata about the request
            
        Returns:
            bool: True if the request should be allowed, False if it should be rejected
        """
        start_time = time.time()
        allowed = False
        
        try:
            with self._lock:
                if route and client_id and route in self._client_route_limits.get(client_id, {}):
                    limit_info = self._client_route_limits[client_id][route]
                    limit, window, strategy_type = limit_info
                    strategy_type = strategy_type or self.default_strategy
                    
                elif client_id in self._client_limits:
                    limit_info = self._client_limits[client_id]
                    limit, window, strategy_type = limit_info
                    strategy_type = strategy_type or self.default_strategy
                    
                elif route and route in self._route_limits:
                    limit_info = self._route_limits[route]
                    limit, window, strategy_type = limit_info
                    
                else:
                    limit = self.default_limit
                    window = self.default_window
                    strategy_type = self.default_strategy
                
                strategy = self._get_strategy_instance(strategy_type, limit, window)
                request_key = f"{client_id}:{route}" if route else client_id
                
                allowed = strategy.allow_request(request_key)
                
                end_time = time.time()
                processing_time = (end_time - start_time) * 1000  # Convert to ms
                
                with self._metrics_lock:
                    self._global_metrics["total_requests"] += 1
                    if allowed:
                        self._global_metrics["allowed_requests"] += 1
                    else:
                        self._global_metrics["rejected_requests"] += 1
                    
                    self._global_metrics["processing_times"].append(processing_time)
                    if len(self._global_metrics["processing_times"]) > 1000:
                        self._global_metrics["processing_times"] = self._global_metrics["processing_times"][-1000:]
                    
                    self._global_metrics["clients"].add(client_id)
                    if route:
                        self._global_metrics["routes"].add(route)
                    
                    client_metrics = self._request_metrics[client_id]
                    if route not in client_metrics:
                        client_metrics[route] = {
                            "total_requests": 0,
                            "allowed_requests": 0,
                            "rejected_requests": 0,
                            "last_request": end_time,
                            "first_request": end_time,
                            "processing_times": []
                        }
                    
                    route_metrics = client_metrics[route]
                    route_metrics["total_requests"] += 1
                    route_metrics["last_request"] = end_time
                    
                    if allowed:
                        route_metrics["allowed_requests"] += 1
                    else:
                        route_metrics["rejected_requests"] += 1
                    
                    route_metrics["processing_times"].append(processing_time)
                    if len(route_metrics["processing_times"]) > 100:
                        route_metrics["processing_times"] = route_metrics["processing_times"][-100:]
                
                return allowed
                
        except Exception as e:
            logger.error(f"Error in check_request: {e}")
            return True
    
    def set_client_limit(
        self, 
        client_id: str, 
        limit: int, 
        window: int = None,
        strategy: RateLimitStrategy = None
    ) -> None:
        """
        Set a custom rate limit for a specific client.
        
        Args:
            client_id: Identifier for the client
            limit: Request limit for this client
            window: Time window in seconds (defaults to global default)
            strategy: Rate limiting strategy to use (defaults to global default)
        """
        with self._lock:
            if window is None:
                window = self.default_window
            
            self._client_limits[client_id] = (limit, window, strategy)
            
            logger.info(f"Set client limit for '{client_id}': {limit} requests per {window}s"
                      f" using {strategy.name if strategy else 'default'} strategy")
    
    def set_route_limit(
        self, 
        route: str, 
        limit: int, 
        window: int = None,
        strategy: RateLimitStrategy = None
    ) -> None:
        """
        Set a custom rate limit for a specific route.
        
        Args:
            route: API route pattern
            limit: Request limit for this route
            window: Time window in seconds (defaults to global default)
            strategy: Rate limiting strategy to use (defaults to global default)
        """
        with self._lock:
            if window is None:
                window = self.default_window
            
            if strategy is None:
                strategy = self.default_strategy
            
            self._route_limits[route] = (limit, window, strategy)
            
            logger.info(f"Set route limit for '{route}': {limit} requests per {window}s"
                      f" using {strategy.name} strategy")
    
    def set_client_route_limit(
        self, 
        client_id: str,
        route: str,
        limit: int, 
        window: int = None,
        strategy: RateLimitStrategy = None
    ) -> None:
        """
        Set a custom rate limit for a specific client and route combination.
        
        Args:
            client_id: Identifier for the client
            route: API route pattern
            limit: Request limit for this client+route
            window: Time window in seconds (defaults to global default)
            strategy: Rate limiting strategy to use (defaults to global default)
        """
        with self._lock:
            if window is None:
                window = self.default_window
                
            self._client_route_limits[client_id][route] = (limit, window, strategy)
            
            logger.info(f"Set client-route limit for '{client_id}' on '{route}': "
                      f"{limit} requests per {window}s"
                      f" using {strategy.name if strategy else 'default'} strategy")
    
    def _clean_old_metrics(self) -> None:
        """Remove metrics older than the retention period."""
        with self._metrics_lock:
            current_time = time.time()
            retention_threshold = current_time - self._metrics_retention
            
            clients_to_remove = []
            for client_id, routes in self._request_metrics.items():
                routes_to_remove = []
                
                for route, metrics in routes.items():
                    if metrics.get("last_request", 0) < retention_threshold:
                        routes_to_remove.append(route)
                
                for route in routes_to_remove:
                    del routes[route]
                
                if not routes:
                    clients_to_remove.append(client_id)
            
            for client_id in clients_to_remove:
                del self._request_metrics[client_id]
            
            routes_to_remove = []
            for route, metrics in self._route_metrics.items():
                if metrics.get("last_request", 0) < retention_threshold:
                    routes_to_remove.append(route)
            
            for route in routes_to_remove:
                del self._route_metrics[route]
    
    def _update_metrics(self) -> None:
        """Update rate metrics based on current data."""
        with self._metrics_lock:
            current_time = time.time()
            elapsed = current_time - self._global_metrics["start_time"]
            
            if elapsed > 0:
                total = self._global_metrics["total_requests"]
                allowed = self._global_metrics["allowed_requests"]
                rejected = self._global_metrics["rejected_requests"]
                
                self._global_metrics["requests_per_second"] = total / elapsed
                self._global_metrics["rejection_rate"] = rejected / total if total > 0 else 0
                
                self._global_metrics["uptime"] = elapsed
                self._global_metrics["client_count"] = len(self._global_metrics["clients"])
                self._global_metrics["route_count"] = len(self._global_metrics["routes"])
                
                if len(self._global_metrics["processing_times"]) > 0:
                    times = self._global_metrics["processing_times"]
                    self._global_metrics["avg_processing_time"] = sum(times) / len(times)
    
    def _adapt_limits(self) -> None:
        """
        Adapt rate limits based on traffic patterns.
        
        This is a key feature that makes AdaptiveShield unique - it can
        automatically adjust limits based on observed traffic patterns
        to optimize both service availability and resource utilization.
        """
        if not self._auto_adapt:
            return
        
        with self._lock, self._metrics_lock:
            for route, metrics in self._route_metrics.items():
                if route not in self._route_limits:
                    continue
                    
                total = metrics.get("total_requests", 0)
                if total < 100:
                    continue
                
                limit, window, strategy = self._route_limits[route]
                
                rejected = metrics.get("rejected_requests", 0)
                rejection_rate = rejected / total if total > 0 else 0
                
                if strategy == RateLimitStrategy.ADAPTIVE_WINDOW:
                    continue
                
                if rejection_rate > 0.2 and rejection_rate < 0.4:
                    new_limit = int(limit * 1.1)
                    self._route_limits[route] = (new_limit, window, strategy)
                    
                    logger.info(f"Adaptive increase: Route '{route}' limit adjusted from {limit} to {new_limit}")
                
                elif rejection_rate < 0.05 and limit > self.default_limit:
                    new_limit = max(self.default_limit, int(limit * 0.95))
                    self._route_limits[route] = (new_limit, window, strategy)
                    
                    logger.info(f"Adaptive decrease: Route '{route}' limit adjusted from {limit} to {new_limit}")
            
            for client_id, routes in self._request_metrics.items():
                if client_id not in self._client_limits:
                    continue
                
                total_client_requests = sum(m.get("total_requests", 0) for m in routes.values())
                if total_client_requests < 100:
                    continue
                
                limit, window, strategy = self._client_limits[client_id]
                if strategy == RateLimitStrategy.ADAPTIVE_WINDOW:
                    continue
                
                total_rejected = sum(m.get("rejected_requests", 0) for m in routes.values())
                rejection_rate = total_rejected / total_client_requests
                
                if rejection_rate > 0.2 and rejection_rate < 0.4:
                    new_limit = int(limit * 1.1)
                    self._client_limits[client_id] = (new_limit, window, strategy)
                    
                    logger.info(f"Adaptive increase: Client '{client_id}' limit adjusted from {limit} to {new_limit}")
                
                elif rejection_rate < 0.05 and limit > self.default_limit:
                    new_limit = max(self.default_limit, int(limit * 0.95))
                    self._client_limits[client_id] = (new_limit, window, strategy)
                    
                    logger.info(f"Adaptive decrease: Client '{client_id}' limit adjusted from {limit} to {new_limit}")
    
    def _monitor_loop(self) -> None:
        """Background thread for monitoring and adaptation."""
        try:
            while not getattr(self, '_stop_monitoring', False):
                self._clean_old_metrics()
                self._update_metrics()
                self._adapt_limits()
                
                time.sleep(self._monitor_interval)
        except Exception as e:
            logger.error(f"Error in monitoring thread: {e}")
    
    def get_client_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get detailed statistics for a specific client.
        
        Args:
            client_id: Client identifier
            
        Returns:
            Dict containing client statistics
        """
        with self._metrics_lock:
            stats = {
                "client_id": client_id,
                "exists": client_id in self._request_metrics or client_id in self._client_limits,
                "limits": {}
            }
            
            if client_id in self._client_limits:
                limit, window, strategy = self._client_limits[client_id]
                stats["limits"]["client"] = {
                    "limit": limit,
                    "window": window,
                    "strategy": strategy.name if strategy else "default"
                }
            
            if client_id in self._client_route_limits:
                stats["limits"]["routes"] = {}
                for route, (limit, window, strategy) in self._client_route_limits[client_id].items():
                    stats["limits"]["routes"][route] = {
                        "limit": limit,
                        "window": window,
                        "strategy": strategy.name if strategy else "default"
                    }
            
            if client_id in self._request_metrics:
                routes_metrics = self._request_metrics[client_id]
                
                total_requests = sum(r.get("total_requests", 0) for r in routes_metrics.values())
                allowed_requests = sum(r.get("allowed_requests", 0) for r in routes_metrics.values())
                rejected_requests = sum(r.get("rejected_requests", 0) for r in routes_metrics.values())
                
                stats.update({
                    "total_requests": total_requests,
                    "allowed_requests": allowed_requests,
                    "rejected_requests": rejected_requests,
                    "rejection_rate": rejected_requests / total_requests if total_requests > 0 else 0,
                    "routes": {route: metrics for route, metrics in routes_metrics.items()}
                })
                
                all_times = []
                for route_metrics in routes_metrics.values():
                    all_times.extend(route_metrics.get("processing_times", []))
                
                if all_times:
                    stats["avg_processing_time"] = sum(all_times) / len(all_times)
                    stats["processing_times"] = all_times[-100:]
            
            return stats
    
    def get_route_stats(self, route: str) -> Dict[str, Any]:
        """
        Get detailed statistics for a specific route.
        
        Args:
            route: API route
            
        Returns:
            Dict containing route statistics
        """
        with self._metrics_lock:
            stats = {
                "route": route,
                "exists": route in self._route_metrics or route in self._route_limits,
                "limits": {}
            }
            
            if route in self._route_limits:
                limit, window, strategy = self._route_limits[route]
                stats["limits"]["route"] = {
                    "limit": limit,
                    "window": window,
                    "strategy": strategy.name
                }
            
            route_specific_client_limits = {}
            for client_id, routes in self._client_route_limits.items():
                if route in routes:
                    limit, window, strategy = routes[route]
                    route_specific_client_limits[client_id] = {
                        "limit": limit,
                        "window": window,
                        "strategy": strategy.name if strategy else "default"
                    }
            
            if route_specific_client_limits:
                stats["limits"]["clients"] = route_specific_client_limits
            
            if route in self._route_metrics:
                metrics = self._route_metrics[route]
                
                stats.update({
                    "total_requests": metrics.get("total_requests", 0),
                    "allowed_requests": metrics.get("allowed_requests", 0),
                    "rejected_requests": metrics.get("rejected_requests", 0),
                    "rejection_rate": (
                        metrics.get("rejected_requests", 0) / metrics.get("total_requests", 1)
                        if metrics.get("total_requests", 0) > 0 else 0
                    ),
                    "client_count": len(metrics.get("clients", set())),
                    "first_request": metrics.get("first_request", 0),
                    "last_request": metrics.get("last_request", 0)
                })
                
                if "processing_times" in metrics and metrics["processing_times"]:
                    stats["avg_processing_time"] = sum(metrics["processing_times"]) / len(metrics["processing_times"])
                    stats["processing_times"] = metrics["processing_times"][-100:]
            
            return stats
    
    def get_global_stats(self) -> Dict[str, Any]:
        """
        Get global statistics for the rate limiter.
        
        Returns:
            Dict containing global statistics
        """
        with self._metrics_lock:
            self._update_metrics()
            
            stats = {
                "total_requests": self._global_metrics["total_requests"],
                "allowed_requests": self._global_metrics["allowed_requests"],
                "rejected_requests": self._global_metrics["rejected_requests"],
                "rejection_rate": self._global_metrics.get("rejection_rate", 0),
                "requests_per_second": self._global_metrics.get("requests_per_second", 0),
                "uptime": self._global_metrics.get("uptime", 0),
                "client_count": self._global_metrics.get("client_count", 0),
                "route_count": self._global_metrics.get("route_count", 0)
            }
            
            if "avg_processing_time" in self._global_metrics:
                stats["avg_processing_time"] = self._global_metrics["avg_processing_time"]
            
            if self._global_metrics["processing_times"]:
                stats["processing_times"] = self._global_metrics["processing_times"][-100:]
            
            return stats


# Default shield instance for simple usage
default_shield = AdaptiveShield() 