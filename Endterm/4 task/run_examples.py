import os
import sys
import time
import json
import signal
import subprocess
import argparse
import multiprocessing
import matplotlib.pyplot as plt
import numpy as np
from typing import Dict, List, Tuple, Any, Optional
import requests
from datetime import datetime
import threading
import queue

EXAMPLES = {
    "flask": {
        "script": "flask_example.py",
        "description": "Flask API with rate limiting",
        "port": 5000,
        "endpoints": [
            "/",
            "/api/public",
            "/api/users",
            "/api/admin",
            "/stats"
        ]
    },
    "fastapi": {
        "script": "example_service.py",
        "description": "FastAPI microservice with rate limiting",
        "port": 8000,
        "endpoints": [
            "/",
            "/api/public", 
            "/api/users",
            "/api/admin",
            "/stats"
        ]
    },
    "distributed": {
        "script": "distributed_example.py",
        "description": "Redis-backed distributed rate limiting",
        "port": 5001,
        "endpoints": [
            "/",
            "/api/public",
            "/api/users",
            "/api/admin",
            "/stats"
        ]
    },
    "dashboard": {
        "script": "dashboard.py",
        "description": "Monitoring dashboard",
        "port": 8050,
        "endpoints": [
            "/"
        ]
    },
    "benchmark": {
        "script": "benchmark.py",
        "description": "Performance benchmark",
        "args": ["--clients", "3", "--requests", "50", "--rps", "20", "--load-pattern", "spike"],
        "is_script": True
    }
}

server_process = None
output_queue = queue.Queue()
stop_threads = False

def check_requirements(example_name: str) -> bool:
    if example_name == "distributed":
        try:
            import redis
            redis_client = redis.Redis(host='localhost', port=6379)
            redis_client.ping()
            return True
        except (ImportError, redis.ConnectionError):
            print("Redis is not available. Install and start Redis to run this example.")
            print("  pip install redis")
            print("  To start Redis: ")
            print("    - Docker: docker run -d -p 6379:6379 redis")
            print("    - macOS: brew install redis && brew services start redis")
            print("    - Linux: apt-get install redis-server && systemctl start redis")
            return False
    return True

def run_server(example_name: str) -> None:
    global server_process, stop_threads, output_queue
    
    example = EXAMPLES[example_name]
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), example["script"])
    
    if not os.path.exists(script_path):
        print(f"Error: Script {script_path} not found")
        return
    
    print(f"Starting {example_name} server ({example['description']})...")
    
    stop_threads = False
    server_process = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    def read_output():
        while not stop_threads and server_process and server_process.poll() is None:
            line = server_process.stdout.readline()
            if line:
                output_queue.put(line.strip())
                print(f"[{example_name}] {line.strip()}")
    
    output_thread = threading.Thread(target=read_output)
    output_thread.daemon = True
    output_thread.start()
    
    print(f"{example_name} server starting on port {example['port']}...")
    
    for _ in range(30):
        try:
            if example.get("port"):
                response = requests.get(f"http://localhost:{example['port']}/")
                if response.status_code == 200:
                    print(f"{example_name} server is running on port {example['port']}")
                    return
        except requests.RequestException:
            time.sleep(0.5)
    
    print(f"Warning: {example_name} server might not have started correctly")

def stop_server() -> None:
    global server_process, stop_threads
    
    if server_process:
        print("Stopping server...")
        stop_threads = True
        
        if sys.platform == 'win32':
            server_process.send_signal(signal.CTRL_C_EVENT)
        else:
            server_process.send_signal(signal.SIGTERM)
            
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Server didn't terminate gracefully, forcing...")
            server_process.kill()
        
        server_process = None
        print("Server stopped")

def run_script(example_name: str, args: Optional[List[str]] = None) -> None:
    example = EXAMPLES[example_name]
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), example["script"])
    
    if not os.path.exists(script_path):
        print(f"Error: Script {script_path} not found")
        return
    
    cmd = [sys.executable, script_path]
    
    if args:
        cmd.extend(args)
    elif "args" in example:
        cmd.extend(example["args"])
    
    print(f"Running {example_name} script: {' '.join(cmd)}")
    
    start_time = time.time()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    for line in process.stdout:
        print(f"[{example_name}] {line.strip()}")
    
    process.wait()
    end_time = time.time()
    
    print(f"{example_name} script completed in {end_time - start_time:.2f} seconds with exit code {process.returncode}")

def test_endpoints(example_name: str) -> None:
    example = EXAMPLES[example_name]
    
    if "port" not in example:
        print(f"Cannot test endpoints for {example_name}: no port defined")
        return
    
    if "endpoints" not in example or not example["endpoints"]:
        print(f"No endpoints defined for {example_name}")
        return
    
    base_url = f"http://localhost:{example['port']}"
    
    print(f"Testing endpoints for {example_name}...")
    for endpoint in example["endpoints"]:
        url = f"{base_url}{endpoint}"
        print(f"  Testing {url}...")
        
        try:
            start_time = time.time()
            response = requests.get(url)
            end_time = time.time()
            
            status = response.status_code
            if 200 <= status < 300:
                status_str = f"OK ({status})"
            else:
                status_str = f"Error ({status})"
            
            print(f"    Status: {status_str}")
            print(f"    Time: {(end_time - start_time) * 1000:.2f} ms")
            print(f"    Content: {response.text[:100]}..." if len(response.text) > 100 else f"    Content: {response.text}")
            print()
            
        except requests.RequestException as e:
            print(f"    Error: {e}")
            print()

def load_test(
    example_name: str, 
    endpoint: str = "/api/public",
    requests: int = 100,
    concurrency: int = 10,
    client_id: Optional[str] = None
) -> Dict[str, Any]:
    example = EXAMPLES[example_name]
    
    if "port" not in example:
        print(f"Cannot load test {example_name}: no port defined")
        return {}
    
    base_url = f"http://localhost:{example['port']}"
    url = f"{base_url}{endpoint}"
    
    print(f"Running load test for {example_name} on {endpoint}...")
    print(f"  URL: {url}")
    print(f"  Requests: {requests}")
    print(f"  Concurrency: {concurrency}")
    print(f"  Client ID: {client_id or 'random'}")
    
    results = {
        "start_time": datetime.now().isoformat(),
        "url": url,
        "total_requests": requests,
        "concurrency": concurrency,
        "client_id": client_id,
        "response_times": [],
        "status_codes": {},
        "errors": []
    }
    
    def make_request(_) -> Dict[str, Any]:
        headers = {}
        if client_id:
            headers["X-API-Key"] = client_id
        
        start_time = time.time()
        result = {
            "start_time": start_time,
            "success": False
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            end_time = time.time()
            result.update({
                "success": True,
                "status_code": response.status_code,
                "response_time": (end_time - start_time) * 1000,  # ms
                "response_size": len(response.content),
                "end_time": end_time
            })
            
        except requests.RequestException as e:
            end_time = time.time()
            result.update({
                "success": False,
                "error": str(e),
                "response_time": (end_time - start_time) * 1000,  # ms
                "end_time": end_time
            })
        
        return result
    
    start_time = time.time()
    
    with multiprocessing.Pool(concurrency) as pool:
        request_results = pool.map(make_request, range(requests))
    
    end_time = time.time()
    
    results["total_time"] = end_time - start_time
    results["requests_per_second"] = requests / results["total_time"]
    
    for result in request_results:
        if result["success"]:
            results["response_times"].append(result["response_time"])
            status_code = result["status_code"]
            results["status_codes"][str(status_code)] = results["status_codes"].get(str(status_code), 0) + 1
        else:
            results["errors"].append(result["error"])
    
    if results["response_times"]:
        results["min_response_time"] = min(results["response_times"])
        results["max_response_time"] = max(results["response_times"])
        results["avg_response_time"] = sum(results["response_times"]) / len(results["response_times"])
    
    print(f"Load test completed in {results['total_time']:.2f} seconds")
    print(f"Requests per second: {results['requests_per_second']:.2f}")
    print(f"Status codes: {results['status_codes']}")
    
    if results["errors"]:
        print(f"Errors: {len(results['errors'])}")
    
    return results

def visualize_load_test(test_name: str, results: Dict[str, Any]) -> None:
    if not results or "response_times" not in results or not results["response_times"]:
        print("No data to visualize")
        return
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
    
    response_times = np.array(results["response_times"])
    
    ax1.hist(response_times, bins=30, alpha=0.7, color='blue')
    ax1.set_title('Response Time Distribution')
    ax1.set_xlabel('Response Time (ms)')
    ax1.set_ylabel('Count')
    
    ax1.axvline(results["avg_response_time"], color='r', linestyle='dashed', linewidth=1)
    ax1.text(results["avg_response_time"] * 1.1, 
            ax1.get_ylim()[1] * 0.9, 
            f'Avg: {results["avg_response_time"]:.2f} ms',
            color='r')
    
    status_codes = results["status_codes"]
    labels = list(status_codes.keys())
    sizes = list(status_codes.values())
    
    ax2.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90, colors=plt.cm.Set3.colors)
    ax2.axis('equal')
    ax2.set_title('HTTP Status Codes')
    
    plt.suptitle(f'Load Test Results: {test_name}')
    plt.tight_layout()
    
    filename = f'load_test_{test_name.replace(" ", "_").lower()}.png'
    plt.savefig(filename)
    print(f"Visualization saved to {filename}")
    plt.close()

def main() -> None:
    parser = argparse.ArgumentParser(description='Run AdaptiveShield examples')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    list_parser = subparsers.add_parser('list', help='List available examples')
    
    run_parser = subparsers.add_parser('run', help='Run an example')
    run_parser.add_argument('example', choices=list(EXAMPLES.keys()), help='Example to run')
    run_parser.add_argument('--test', action='store_true', help='Test endpoints after starting')
    
    test_parser = subparsers.add_parser('test', help='Test endpoints of a running example')
    test_parser.add_argument('example', choices=list(EXAMPLES.keys()), help='Example to test')
    
    load_parser = subparsers.add_parser('load', help='Run a load test on an endpoint')
    load_parser.add_argument('example', choices=list(EXAMPLES.keys()), help='Example to load test')
    load_parser.add_argument('--endpoint', default='/api/public', help='Endpoint to test')
    load_parser.add_argument('--requests', type=int, default=100, help='Number of requests')
    load_parser.add_argument('--concurrency', type=int, default=10, help='Concurrency level')
    load_parser.add_argument('--client-id', help='Client ID to use')
    
    args = parser.parse_args()
    
    if args.command == 'list':
        print("Available examples:")
        for name, example in EXAMPLES.items():
            print(f"  {name}: {example['description']}")
    
    elif args.command == 'run':
        example_name = args.example
        
        if not check_requirements(example_name):
            return
        
        if EXAMPLES[example_name].get("is_script", False):
            run_script(example_name)
        else:
            try:
                run_server(example_name)
                
                if args.test:
                    time.sleep(2) 
                    test_endpoints(example_name)
                
                print("\nPress Ctrl+C to stop the server...")
                while True:
                    time.sleep(1)
            
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received, stopping server...")
            
            finally:
                stop_server()
    
    elif args.command == 'test':
        test_endpoints(args.example)
    
    elif args.command == 'load':
        results = load_test(
            args.example,
            endpoint=args.endpoint,
            requests=args.requests,
            concurrency=args.concurrency,
            client_id=args.client_id
        )
        
        if results:
            test_name = f"{args.example}_{args.endpoint.replace('/', '_')}"
            visualize_load_test(test_name, results)
            
            with open(f"load_test_{test_name}.json", 'w') as f:
                json.dump(results, f, indent=2)

if __name__ == "__main__":
    main() 