"""
Rate Limiting Strategies for AdaptiveShield

This module contains implementations of various rate limiting algorithms used
to control request rates to an API. Each strategy has different characteristics
and trade-offs in terms of memory usage, accuracy, and adaptability.
"""

import time
import threading
import math
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Dict, Any, List, Tuple, Optional


class RateLimitStrategy(ABC):
    """Base class for all rate limiting strategies."""
    
    def __init__(self, limit: int, window: int):
        """
        Initialize the rate limiting strategy.
        
        Args:
            limit: Maximum number of requests allowed in the time window
            window: Time window in seconds
        """
        self.limit = limit
        self.window = window
        self._lock = threading.RLock()
        self._clients = {}
    
    @abstractmethod
    def allow_request(self, client_id: str) -> bool:
        """
        Check if a request from the client should be allowed.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            bool: True if the request should be allowed, False otherwise
        """
        pass
    
    def get_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get statistics for the client.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Dict[str, Any]: Statistics for the client
        """
        with self._lock:
            if client_id not in self._clients:
                return {
                    "client_id": client_id,
                    "exists": False
                }
            
            return {
                "client_id": client_id,
                "exists": True,
                "limit": self.limit,
                "window": self.window
            }
    
    def reset(self, client_id: str) -> None:
        """
        Reset the client's statistics.
        
        Args:
            client_id: Unique identifier for the client
        """
        with self._lock:
            if client_id in self._clients:
                del self._clients[client_id]


class TokenBucketStrategy(RateLimitStrategy):
    """
    Token Bucket algorithm implementation.
    
    This strategy models rate limiting as a bucket that contains tokens.
    Each request consumes one token, and tokens are replenished at a constant rate.
    If the bucket is empty, requests are rejected until new tokens are added.
    
    This approach handles bursts well while maintaining a consistent average rate.
    """
    
    def __init__(self, limit: int, window: int):
        """
        Initialize the token bucket strategy.
        
        Args:
            limit: Maximum number of tokens in the bucket
            window: Time window in seconds to refill the bucket
        """
        super().__init__(limit, window)
        # Rate at which tokens are added to the bucket (tokens per second)
        self.refill_rate = limit / window
        # Client state tracking
        self._client_buckets = {}
        self._client_last_updated = {}
    
    def allow_request(self, client_id: str) -> bool:
        """
        Check if a request should be allowed based on available tokens.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            bool: True if the request should be allowed, False otherwise
        """
        with self._lock:
            current_time = time.time()
            
            if client_id not in self._client_buckets:
                self._client_buckets[client_id] = self.limit
                self._client_last_updated[client_id] = current_time
                self._clients[client_id] = True
                return True
            
            tokens = self._client_buckets[client_id]
            last_updated = self._client_last_updated[client_id]
            
            elapsed = current_time - last_updated
            tokens = min(self.limit, tokens + elapsed * self.refill_rate)
            
            if tokens < 1:
                self._client_buckets[client_id] = tokens
                self._client_last_updated[client_id] = current_time
                return False
            
            self._client_buckets[client_id] = tokens - 1
            self._client_last_updated[client_id] = current_time
            
            return True
    
    def get_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get statistics for the client including token bucket specifics.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Dict[str, Any]: Statistics for the client
        """
        with self._lock:
            stats = super().get_stats(client_id)
            
            if not stats["exists"]:
                return stats
            
            current_time = time.time()
            tokens = self._client_buckets[client_id]
            last_updated = self._client_last_updated[client_id]
            
            elapsed = current_time - last_updated
            tokens = min(self.limit, tokens + elapsed * self.refill_rate)
            
            stats.update({
                "tokens": tokens,
                "refill_rate": self.refill_rate,
                "time_to_full": (self.limit - tokens) / self.refill_rate if tokens < self.limit else 0,
                "strategy": "token_bucket"
            })
            
            return stats


class SlidingWindowCounterStrategy(RateLimitStrategy):
    """
    Sliding Window Counter implementation.
    
    This strategy divides time into discrete windows and counts requests in each window.
    As time progresses, it weights the previous window's count to create a sliding effect.
    
    This approach provides a more accurate count than fixed windows while using less memory
    than a true sliding log.
    """
    
    def __init__(self, limit: int, window: int):
        """
        Initialize the sliding window counter strategy.
        
        Args:
            limit: Maximum number of requests allowed in the time window
            window: Time window in seconds
        """
        super().__init__(limit, window)
        # Client state tracking
        self.precision = min(window, 60)
        self.slice_duration = window / self.precision
        
        self._client_windows = {}
        self._client_last_request = {}
    
    def allow_request(self, client_id: str) -> bool:
        """
        Check if a request should be allowed based on the sliding window count.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            bool: True if the request should be allowed, False otherwise
        """
        with self._lock:
            current_time = time.time()
            current_minute = int(current_time / self.slice_duration)
            
            if client_id not in self._client_windows:
                self._client_windows[client_id] = defaultdict(int)
                self._client_last_request[client_id] = current_time
                self._clients[client_id] = True
            
            window_start = current_minute - self.precision + 1
            
            counter = 0
            for minute, count in list(self._client_windows[client_id].items()):
                if minute < window_start:
                    del self._client_windows[client_id][minute]
                else:
                    counter += count
            
            if counter >= self.limit:
                return False
            
            self._client_windows[client_id][current_minute] += 1
            self._client_last_request[client_id] = current_time
            
            return True
    
    def get_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get statistics for the client including sliding window specifics.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Dict[str, Any]: Statistics for the client
        """
        with self._lock:
            stats = super().get_stats(client_id)
            
            if not stats["exists"]:
                return stats
            
            current_time = time.time()
            current_minute = int(current_time / self.slice_duration)
            window_start = current_minute - self.precision + 1
            
            counter = 0
            for minute, count in list(self._client_windows[client_id].items()):
                if minute >= window_start:
                    counter += count
            
            minutes_distribution = {
                minute: count 
                for minute, count in self._client_windows[client_id].items()
                if minute >= window_start
            }
            
            stats.update({
                "current_count": counter,
                "remaining": self.limit - counter,
                "utilization": counter / self.limit,
                "reset_at": window_start * self.slice_duration + self.window,
                "minutes": minutes_distribution,
                "strategy": "sliding_window"
            })
            
            return stats


class LeakyBucketStrategy(RateLimitStrategy):
    """
    Leaky Bucket algorithm implementation.
    
    This strategy models requests as water in a bucket with a constant leak rate.
    Each request adds to the bucket, and the bucket "leaks" at a constant rate.
    If adding a request would overflow the bucket, the request is rejected.
    
    This approach smooths out bursts and enforces a constant outflow rate.
    """
    
    def __init__(self, limit: int, window: int):
        """
        Initialize the leaky bucket strategy.
        
        Args:
            limit: Bucket depth (maximum level/burst capacity)
            window: Time window used to calculate leak rate
                   (bucket will empty completely in 'window' seconds)
        """
        super().__init__(limit, window)
        # Leak rate in units per second
        self.leak_rate = limit / window
        # Client state tracking
        self._client_buckets = {}
        self._client_last_leak = {}
    
    def allow_request(self, client_id: str) -> bool:
        """
        Check if a request should be allowed based on the leaky bucket state.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            bool: True if the request should be allowed, False otherwise
        """
        with self._lock:
            current_time = time.time()
            
            if client_id not in self._client_buckets:
                self._client_buckets[client_id] = 0
                self._client_last_leak[client_id] = current_time
                self._clients[client_id] = True
                return True
            
            last_leak = self._client_last_leak[client_id]
            level = self._client_buckets[client_id]
            
            elapsed = current_time - last_leak
            leaked = elapsed * self.leak_rate
            level = max(0, level - leaked)
            
            if level >= self.limit:
                self._client_buckets[client_id] = level
                self._client_last_leak[client_id] = current_time
                return False
            
            self._client_buckets[client_id] = level + 1
            self._client_last_leak[client_id] = current_time
            
            return True
    
    def get_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get statistics for the client including leaky bucket specifics.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Dict[str, Any]: Statistics for the client
        """
        with self._lock:
            stats = super().get_stats(client_id)
            
            if not stats["exists"]:
                return stats
            
            current_time = time.time()
            last_leak = self._client_last_leak[client_id]
            level = self._client_buckets[client_id]
            
            elapsed = current_time - last_leak
            leaked = elapsed * self.leak_rate
            level = max(0, level - leaked)
            
            stats.update({
                "bucket_level": level,
                "leak_rate": self.leak_rate,
                "time_to_empty": level / self.leak_rate if level > 0 else 0,
                "utilization": level / self.limit,
                "strategy": "leaky_bucket"
            })
            
            return stats


class AdaptiveWindowStrategy(RateLimitStrategy):
    """
    Adaptive Window strategy implementation.
    
    This advanced strategy adjusts its limits and window sizes based on request patterns
    and system load. It combines elements of both sliding window and token bucket
    approaches, with additional intelligence to adapt to changing conditions.
    
    This approach is best for handling variable traffic with unknown patterns.
    """
    
    def __init__(self, limit: int, window: int):
        """
        Initialize the adaptive window strategy.
        
        Args:
            limit: Initial maximum requests per window
            window: Initial time window in seconds
        """
        super().__init__(limit, window)
        
        # Client state tracking
        self.min_limit = max(1, limit // 10)
        self.max_limit = limit * 2
        self.min_window = max(1, window // 4)
        self.max_window = window * 2
        
        self.adaptation_rate = 0.1
        self.threshold_high = 0.8
        self.threshold_low = 0.2
        
        self._effective_limits = {}
        self._effective_windows = {}
        self._client_requests = {}
        self._client_allowed = {}
        self._client_last_adapt = {}
        self._client_last_request = {}
    
    def allow_request(self, client_id: str) -> bool:
        """
        Check if a request should be allowed based on adaptive windowing.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            bool: True if the request should be allowed, False otherwise
        """
        with self._lock:
            current_time = time.time()
            
            if client_id not in self._effective_limits:
                self._effective_limits[client_id] = self.limit
                self._effective_windows[client_id] = self.window
                self._client_requests[client_id] = 0
                self._client_allowed[client_id] = 0
                self._client_last_adapt[client_id] = current_time
                self._client_last_request[client_id] = []
                self._clients[client_id] = True
            
            self._adjust_parameters(client_id)
            
            effective_limit = self._effective_limits[client_id]
            effective_window = self._effective_windows[client_id]
            
            window_start = current_time - effective_window
            
            self._client_requests[client_id] += 1
            
            requests_in_window = 0
            self._client_last_request[client_id] = [
                t for t in self._client_last_request[client_id] if t > window_start
            ]
            requests_in_window = len(self._client_last_request[client_id])
            
            if requests_in_window >= effective_limit:
                return False
            
            self._client_last_request[client_id].append(current_time)
            self._client_allowed[client_id] += 1
            
            return True
    
    def _adjust_parameters(self, client_id: str) -> None:
        """
        Adjust rate limiting parameters based on observed request patterns.
        
        Args:
            client_id: Unique identifier for the client
        """
        current_time = time.time()
        last_adapt = self._client_last_adapt[client_id]
        
        if current_time - last_adapt < self.window / 4:
            return
        
        total_requests = self._client_requests[client_id]
        if total_requests < 10:
            return
        
        allowed = self._client_allowed[client_id]
        
        effective_limit = self._effective_limits[client_id]
        effective_window = self._effective_windows[client_id]
        
        allow_ratio = allowed / total_requests
        
        if allow_ratio < self.threshold_low:
            effective_limit = min(self.max_limit, effective_limit * (1 + self.adaptation_rate))
            effective_window = min(self.max_window, effective_window * (1 + self.adaptation_rate))
        elif allow_ratio > self.threshold_high:
            effective_limit = max(self.min_limit, effective_limit * (1 - self.adaptation_rate))
            effective_window = max(self.min_window, effective_window * (1 - self.adaptation_rate))
        
        self._effective_limits[client_id] = effective_limit
        self._effective_windows[client_id] = effective_window
        self._client_last_adapt[client_id] = current_time
        
        self._client_requests[client_id] = 0
        self._client_allowed[client_id] = 0
    
    def get_stats(self, client_id: str) -> Dict[str, Any]:
        """
        Get statistics for the client including adaptive strategy specifics.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Dict[str, Any]: Statistics for the client
        """
        with self._lock:
            stats = super().get_stats(client_id)
            
            if not stats["exists"]:
                return stats
            
            current_time = time.time()
            effective_limit = self._effective_limits[client_id]
            effective_window = self._effective_windows[client_id]
            window_start = current_time - effective_window
            
            requests_in_window = len([
                t for t in self._client_last_request[client_id] if t > window_start
            ])
            
            total_requests = self._client_requests[client_id]
            allowed = self._client_allowed[client_id]
            allow_ratio = allowed / total_requests if total_requests > 0 else 1.0
            
            stats.update({
                "base_limit": self.limit,
                "base_window": self.window,
                "effective_limit": effective_limit,
                "effective_window": effective_window,
                "current_count": requests_in_window,
                "remaining": effective_limit - requests_in_window,
                "utilization": requests_in_window / effective_limit,
                "allow_ratio": allow_ratio,
                "total_requests_since_adapt": total_requests,
                "allowed_since_adapt": allowed,
                "strategy": "adaptive_window"
            })
            
            return stats 