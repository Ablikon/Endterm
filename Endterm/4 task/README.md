# AdaptiveShield: Advanced Adaptive Rate Limiting System

AdaptiveShield is a sophisticated rate limiting system designed for modern microservices and APIs. It intelligently adapts to traffic patterns and offers multiple rate limiting strategies that can be combined and configured for optimal protection.

## Installation and Setup

1. Install required dependencies:
```bash
pip install -r requirements.txt
```

2. Redis Setup (required for distributed mode):
```bash
# Option 1: Using Docker
docker run -d -p 6379:6379 redis

# Option 2: Local Redis installation
# Follow instructions at https://redis.io/docs/getting-started/
```

## Running the System Components

### Core Examples

1. FastAPI Example (Basic):
```bash
python example_service.py
# Access at http://localhost:8000
```

2. Distributed Rate Limiting with Redis:
```bash
python distributed_example.py
# Access at http://localhost:5001
```

3. Monitoring Dashboard:
```bash
python dashboard.py
# Access at http://localhost:8050
```

4. Run All Examples:
```bash
python run_examples.py
```

### Testing and Evaluation

1. Benchmark Different Strategies:
```bash
python benchmark.py
```

2. Load Testing:
```bash
python benchmark.py --load-test --duration 60
```

## Component Architecture

AdaptiveShield consists of several key components:

1. **Core Shield Library** - Implements various rate limiting strategies
2. **FastAPI/Flask Integration** - Examples showing integration with web frameworks
3. **Distributed Implementation** - Redis-based coordination for multi-instance deployments
4. **Monitoring Dashboard** - Real-time visualization and configuration UI
5. **Benchmarking Tools** - Performance testing and strategy comparison

## Rate Limiting Strategies

| Strategy | Description | Best For |
|----------|-------------|----------|
| Token Bucket | Allows bursts while maintaining average rate | General purpose, API endpoints with occasional bursts |
| Sliding Window | More accurate than fixed windows, less memory than sliding logs | High precision counting, smooth limiting |
| Leaky Bucket | Ensures constant outflow rate | Protecting downstream services, steady traffic flow |
| Adaptive Window | Dynamically adjusts based on traffic patterns | Varying workloads, systems with changing traffic patterns |

## System Requirements

- Python 3.7+
- Redis (for distributed mode)
- 512MB RAM minimum (2GB recommended for running all components)
- Operating Systems: Linux, macOS, Windows

## Verification Steps

1. Check examples are working:
   - FastAPI example should return JSON responses at http://localhost:8000
   - Dashboard should display metrics at http://localhost:8050
   - Distributed example should be accessible at http://localhost:5001

2. Test rate limiting:
   - Run `python run_examples.py --test-limits`
   - Observe 429 status codes when limits are exceeded

3. Monitor adaptation:
   - Run system with high traffic then observe in dashboard how limits adjust
   - Adaptation visible in both metrics and configuration panels

## Troubleshooting

- If Redis connection fails, ensure Redis is running and accessible on port 6379
- For visualization issues, ensure all Dash/Plotly dependencies are installed
- Port conflicts can be resolved by editing the port numbers in each example file

![Adaptive Shield Architecture](docs/architecture_diagram.png)

## The Problem

Microservice APIs often face performance degradation during traffic spikes due to uncontrolled request bursts from clients. Standard rate limiting solutions typically implement a single algorithm with static configuration parameters, which leads to either:

- **Overly restrictive limits** that block legitimate traffic during normal operations
- **Ineffective protection** during unexpected traffic patterns or sophisticated abuse scenarios
- **Inability to adapt** to changing client behaviors and evolving application requirements

## Our Solution

AdaptiveShield implements a multi-layered approach to rate limiting that combines several key features:

### Multiple Rate Limiting Strategies

- **Token Bucket**: Efficiently handles burst traffic while maintaining average rate
- **Sliding Window**: Provides more accurate counting than fixed windows with less memory usage
- **Leaky Bucket**: Ensures consistent output rate regardless of input bursts
- **Adaptive Window**: Dynamically adjusts limits based on traffic patterns

### Advanced Configuration Options

- Global defaults
- Route-specific limits
- Client-specific limits
- Combined client+route limits

### Adaptive Capabilities

- Automatically adjusts to traffic patterns
- Monitors system performance
- Optimizes limits based on actual usage

### Comprehensive Metrics

- Real-time traffic monitoring
- Historical trends
- Client-specific analytics
- Route-specific analytics

## Getting Started

### Installation

```bash
pip install -r requirements.txt
```

### Basic Usage

Here's a simple example using AdaptiveShield with Flask:

```python
from flask import Flask, request
from adaptive_shield import AdaptiveShield, RateLimitStrategy

app = Flask(__name__)
shield = AdaptiveShield(
    default_limit=100,
    default_window=60,
    default_strategy=RateLimitStrategy.TOKEN_BUCKET
)

shield.set_route_limit("/api/public", 200, 60, RateLimitStrategy.SLIDING_WINDOW)
shield.set_route_limit("/api/users", 50, 60, RateLimitStrategy.LEAKY_BUCKET)
shield.set_route_limit("/api/admin", 20, 60, RateLimitStrategy.ADAPTIVE_WINDOW)

@app.route('/api/users')
def users_endpoint():
    client_id = request.headers.get('X-API-Key', 'anonymous')
    allowed = shield.check_request(client_id, '/api/users')
    
    if not allowed:
        return {"error": "Rate limit exceeded"}, 429
        
    return {"status": "success", "data": [...]}
```

## Included Examples

This repository includes several examples to help you get started:

### 1. Flask Example (`flask_example.py`)

A complete Flask application demonstrating:
- How to integrate AdaptiveShield with Flask
- Implementing rate limiting as a decorator
- Managing client identification
- Handling rate limit violations properly

### 2. FastAPI Example (`example_service.py`)

A FastAPI microservice showing:
- Integration with FastAPI dependency injection
- Advanced rate limiting configurations
- Detailed metrics endpoints

### 3. Benchmark Tool (`benchmark.py`)

A comprehensive benchmarking utility to:
- Compare different rate limiting strategies
- Measure performance under various load patterns
- Visualize how each strategy handles traffic spikes

### 4. Dashboard (`dashboard.py`)

A real-time monitoring dashboard built with Dash that provides:
- Live traffic visualization
- Rate limit configuration interface
- Traffic simulation tools
- Metrics displays

### 5. Distributed Example (`distributed_example.py`)

A Redis-backed implementation for distributed deployments:
- Shared rate limiting state across multiple application instances
- Atomic operations using Redis Lua scripts
- Instance-aware monitoring

## Configuration Options

### Rate Limiting Strategies

| Strategy | Description | Best For |
|----------|-------------|----------|
| Token Bucket | Allows bursts while maintaining average rate | General purpose, API endpoints with occasional bursts |
| Sliding Window | More accurate than fixed windows, less memory than sliding logs | High precision counting, smooth limiting |
| Leaky Bucket | Ensures constant outflow rate | Protecting downstream services, steady traffic flow |
| Adaptive Window | Dynamically adjusts based on traffic patterns | Varying workloads, systems with changing traffic patterns |

### Limits Configuration

```python

shield = AdaptiveShield(
    default_limit=100, 
    default_window=60, 
    default_strategy=RateLimitStrategy.TOKEN_BUCKET
)

shield.set_route_limit(
    route="/api/users",
    limit=50,
    window=60,
    strategy=RateLimitStrategy.LEAKY_BUCKET
)

shield.set_client_limit(
    client_id="premium_user_123",
    limit=500,
    window=60
)


shield.set_client_route_limit(
    client_id="premium_user_123",
    route="/api/data",
    limit=200,
    window=60
)
```

## Architecture

AdaptiveShield uses a layered architecture to provide flexibility and performance:

```
┌─────────────────────────────────────────┐
│             AdaptiveShield              │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────┐      ┌──────────────┐  │
│  │ Rate Limit  │      │  Monitoring  │  │
│  │ Strategies  │◄────►│   & Metrics  │  │
│  └─────────────┘      └──────────────┘  │
│         ▲                    ▲          │
│         │                    │          │
│         ▼                    │          │
│  ┌─────────────┐             │          │
│  │   Client &  │             │          │
│  │Route Manager│             │          │
│  └─────────────┘             │          │
│         ▲                    │          │
│         │                    ▼          │
│         │              ┌──────────────┐ │
│         └────────────► │  Adaptation  │ │
│                        │    Engine    │ │
│                        └──────────────┘ │
│                                         │
└─────────────────────────────────────────┘
```

## Metrics and Monitoring

AdaptiveShield provides comprehensive metrics:

### Global Stats

```python
global_stats = shield.get_global_stats()
```

Returns information like:
- Total requests processed
- Allowed vs. rejected requests
- Current traffic rate
- Rejection rate

### Client Stats

```python
client_stats = shield.get_client_stats("client_123")
```

Returns client-specific metrics including:
- Request history
- Allowed/rejected counts
- Current rate limiting status
- Token/window information based on strategy

### Route Stats

```python
route_stats = shield.get_route_stats("/api/users")
```

Provides route-specific analytics including:
- Traffic patterns for the route
- Effectiveness of the applied strategy
- Current configuration

## Advanced Features

### Automatic Adaptation

When enabled, AdaptiveShield monitors traffic patterns and adjusts limits automatically:

```python
shield = AdaptiveShield(
    default_limit=100,
    default_window=60,
    auto_adapt=True,  
    monitor_interval=30 
)
```

### Distributed Rate Limiting

For scalable applications running multiple instances, use the Redis backend:

```python
from adaptive_shield.distributed import RedisAdaptiveShield
import redis

redis_client = redis.Redis(host='localhost', port=6379)

shield = RedisAdaptiveShield(
    redis_client=redis_client,
    key_prefix="my_app",
    default_limit=100,
    default_window=60
)
```

### Custom Client Identification

Implement your own client identification logic:

```python
def get_client_id(request):
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"api:{api_key}"
    
    ip = request.remote_addr
    agent = request.headers.get("User-Agent", "")
    return f"ip:{ip}:{hash(agent)}"
```

## Running the Examples

```bash
python flask_example.py

python example_service.py

python benchmark.py --clients 10 --rps 50 --load-pattern spike

python dashboard.py

python distributed_example.py
```

## Deployment Considerations

### High Availability

For distributed environments, consider using the Redis backend to maintain consistent rate limiting across multiple instances. See `distributed_example.py` for details.

### Performance

AdaptiveShield is designed for high performance:
- In-memory storage for fast lookups
- Efficient algorithms with minimal overhead
- Threaded monitoring to avoid blocking the request path
- Redis Lua scripts for atomic distributed operations

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. 