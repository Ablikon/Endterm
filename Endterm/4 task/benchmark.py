import time
import concurrent.futures
import random
import argparse
import json
import statistics
from typing import Dict, List, Any, Callable, Tuple
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm

from adaptive_shield import AdaptiveShield, RateLimitStrategy


class Benchmark:
    def __init__(self):
        self.shields = {}
        self.results = {}
        
        for strategy in RateLimitStrategy:
            self.shields[strategy.name] = AdaptiveShield(
                default_limit=100,
                default_window=5,
                default_strategy=strategy,
                monitor_interval=1,
                auto_adapt=True
            )
    
    def run_single_client(
        self, 
        strategy_name: str, 
        client_id: str,
        num_requests: int,
        requests_per_second: float,
        burst_factor: float = 1.0
    ) -> Dict[str, Any]:
        shield = self.shields[strategy_name]
        
        allowed = 0
        rejected = 0
        response_times = []
        
        base_delay = 1.0 / requests_per_second
        
        for i in range(num_requests):
            if random.random() < 0.2:
                delay = base_delay / burst_factor
            else:
                delay = base_delay
            
            start_time = time.time()
            result = shield.check_request(client_id, "/api/test")
            end_time = time.time()
            
            if result:
                allowed += 1
            else:
                rejected += 1
            
            response_times.append((end_time - start_time) * 1000)
            
            time_to_sleep = max(0, delay - (end_time - start_time))
            if time_to_sleep > 0:
                time.sleep(time_to_sleep)
        
        avg_response_time = statistics.mean(response_times) if response_times else 0
        p95_response_time = np.percentile(response_times, 95) if response_times else 0
        p99_response_time = np.percentile(response_times, 99) if response_times else 0
        
        client_stats = shield.get_client_stats(client_id)
        
        return {
            "strategy": strategy_name,
            "client_id": client_id,
            "total_requests": num_requests,
            "allowed_requests": allowed,
            "rejected_requests": rejected,
            "acceptance_rate": allowed / num_requests if num_requests > 0 else 0,
            "avg_response_time_ms": avg_response_time,
            "p95_response_time_ms": p95_response_time,
            "p99_response_time_ms": p99_response_time,
            "shield_stats": client_stats
        }
    
    def run_multiple_clients(
        self, 
        strategy_name: str,
        num_clients: int,
        num_requests_per_client: int,
        requests_per_second: float,
        burst_factor: float = 1.0
    ) -> Dict[str, Any]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_clients) as executor:
            futures = []
            
            for i in range(num_clients):
                client_id = f"benchmark_client_{i}"
                futures.append(
                    executor.submit(
                        self.run_single_client,
                        strategy_name,
                        client_id,
                        num_requests_per_client,
                        requests_per_second,
                        burst_factor
                    )
                )
            
            client_results = []
            for future in concurrent.futures.as_completed(futures):
                client_results.append(future.result())
        
        total_requests = sum(r["total_requests"] for r in client_results)
        allowed_requests = sum(r["allowed_requests"] for r in client_results)
        rejected_requests = sum(r["rejected_requests"] for r in client_results)
        
        all_response_times = []
        for result in client_results:
            if "processing_times" in result["shield_stats"]:
                all_response_times.extend(result["shield_stats"]["processing_times"])
        
        avg_response_time = statistics.mean(all_response_times) if all_response_times else 0
        p95_response_time = np.percentile(all_response_times, 95) if all_response_times else 0
        p99_response_time = np.percentile(all_response_times, 99) if all_response_times else 0
        
        return {
            "strategy": strategy_name,
            "num_clients": num_clients,
            "requests_per_client": num_requests_per_client,
            "total_requests": total_requests,
            "allowed_requests": allowed_requests,
            "rejected_requests": rejected_requests,
            "acceptance_rate": allowed_requests / total_requests if total_requests > 0 else 0,
            "avg_response_time_ms": avg_response_time,
            "p95_response_time_ms": p95_response_time,
            "p99_response_time_ms": p99_response_time,
            "client_results": client_results
        }
    
    def run_benchmark_all_strategies(
        self,
        num_clients: int = 5,
        num_requests_per_client: int = 100,
        requests_per_second: float = 30.0,
        burst_factor: float = 1.0
    ) -> Dict[str, Dict[str, Any]]:
        results = {}
        
        for strategy_name in tqdm(self.shields.keys(), desc="Benchmarking strategies"):
            print(f"\nRunning benchmark for {strategy_name}...")
            result = self.run_multiple_clients(
                strategy_name,
                num_clients,
                num_requests_per_client,
                requests_per_second,
                burst_factor
            )
            results[strategy_name] = result
            
            print(f"  Allowed: {result['allowed_requests']}/{result['total_requests']} "
                  f"({result['acceptance_rate']*100:.1f}%)")
            print(f"  Avg response time: {result['avg_response_time_ms']:.3f} ms")
        
        return results
    
    def run_load_test(
        self,
        strategy_name: str,
        load_pattern: Callable[[int], float],
        duration_seconds: int = 60,
        num_clients: int = 5
    ) -> Dict[str, Any]:
        shield = self.shields[strategy_name]
        
        time_points = []
        request_rates = []
        acceptance_rates = []
        
        client_ids = [f"load_test_client_{i}" for i in range(num_clients)]
        
        for client_id in client_ids:
            shield.reset_client(client_id) if hasattr(shield, 'reset_client') else None
        
        start_time = time.time()
        end_time = start_time + duration_seconds
        
        with tqdm(total=duration_seconds, desc=f"Load testing {strategy_name}") as pbar:
            current_second = 0
            while time.time() < end_time:
                current_time = time.time()
                elapsed = current_time - start_time
                
                seconds_elapsed = int(elapsed)
                if seconds_elapsed > current_second:
                    pbar.update(seconds_elapsed - current_second)
                    current_second = seconds_elapsed
                
                target_rps = load_pattern(elapsed)
                
                time_points.append(elapsed)
                request_rates.append(target_rps)
                
                allowed = 0
                total = 0
                
                for client_id in client_ids:
                    result = shield.check_request(client_id, "/api/load_test")
                    if result:
                        allowed += 1
                    total += 1
                
                acceptance_rates.append(allowed / total if total > 0 else 0)
                
                delay = 1.0 / (target_rps * num_clients) if target_rps > 0 else 0.1
                time_to_sleep = max(0, delay - (time.time() - current_time))
                if time_to_sleep > 0:
                    time.sleep(time_to_sleep)
        
        client_stats = {client_id: shield.get_client_stats(client_id) for client_id in client_ids}
        
        return {
            "strategy": strategy_name,
            "duration_seconds": duration_seconds,
            "num_clients": num_clients,
            "time_points": time_points,
            "request_rates": request_rates,
            "acceptance_rates": acceptance_rates,
            "client_stats": client_stats
        }
    
    def plot_results(self, results: Dict[str, Dict[str, Any]]) -> None:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        strategies = list(results.keys())
        acceptance_rates = [results[s]["acceptance_rate"] * 100 for s in strategies]
        response_times = [results[s]["avg_response_time_ms"] for s in strategies]
        
        bars1 = ax1.bar(strategies, acceptance_rates, color='royalblue')
        ax1.set_title('Request Acceptance Rate by Strategy')
        ax1.set_ylabel('Acceptance Rate (%)')
        ax1.set_ylim(0, 100)
        
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 1,
                    f'{height:.1f}%', ha='center', va='bottom')
        
        bars2 = ax2.bar(strategies, response_times, color='firebrick')
        ax2.set_title('Average Response Time by Strategy')
        ax2.set_ylabel('Response Time (ms)')
        
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.02,
                    f'{height:.3f} ms', ha='center', va='bottom')
        
        plt.tight_layout()
        plt.savefig('benchmark_results.png')
        plt.close()
    
    def plot_load_test(self, load_test_results: Dict[str, Any]) -> None:
        fig, ax1 = plt.subplots(figsize=(12, 6))
        
        time_points = load_test_results["time_points"]
        request_rates = load_test_results["request_rates"]
        acceptance_rates = load_test_results["acceptance_rates"]
        
        ax1.set_xlabel('Time (seconds)')
        ax1.set_ylabel('Requests per Second', color='blue')
        ax1.plot(time_points, request_rates, color='blue', label='Target RPS')
        ax1.tick_params(axis='y', labelcolor='blue')
        
        ax2 = ax1.twinx()
        ax2.set_ylabel('Acceptance Rate', color='red')
        ax2.plot(time_points, acceptance_rates, color='red', label='Acceptance Rate')
        ax2.tick_params(axis='y', labelcolor='red')
        ax2.set_ylim(0, 1.1)
        
        plt.title(f'Load Test: {load_test_results["strategy"]} Strategy')
        
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right')
        
        plt.tight_layout()
        plt.savefig(f'load_test_{load_test_results["strategy"]}.png')
        plt.close()


def sine_pattern(t: float) -> float:
    return 55 + 45 * np.sin(2 * np.pi * t / 20)


def spike_pattern(t: float) -> float:
    if t < 15:
        return 20
    elif t < 20:
        return 20 + (200 - 20) * (t - 15) / 5
    else:
        return 20 + (200 - 20) * np.exp(-(t - 20) / 10)


def step_pattern(t: float) -> float:
    if t < 10:
        return 20
    elif t < 20:
        return 50
    elif t < 30:
        return 100
    elif t < 40:
        return 150
    else:
        return 50


def main():
    parser = argparse.ArgumentParser(description='AdaptiveShield Benchmark Tool')
    parser.add_argument('--clients', type=int, default=5, help='Number of concurrent clients')
    parser.add_argument('--requests', type=int, default=100, help='Requests per client')
    parser.add_argument('--rps', type=float, default=30, help='Target requests per second per client')
    parser.add_argument('--burst', type=float, default=2.0, help='Burst factor (1.0 = steady, >1.0 = bursty)')
    parser.add_argument('--load-test-duration', type=int, default=60, help='Duration of load test in seconds')
    parser.add_argument('--load-pattern', choices=['sine', 'spike', 'step'], default='sine', 
                       help='Load pattern for load test')
    args = parser.parse_args()
    
    print("--- AdaptiveShield Benchmark Tool ---")
    print(f"Configuration:")
    print(f"  Clients: {args.clients}")
    print(f"  Requests per client: {args.requests}")
    print(f"  Target RPS per client: {args.rps}")
    print(f"  Burst factor: {args.burst}")
    print(f"  Load test duration: {args.load_test_duration} seconds")
    print(f"  Load pattern: {args.load_pattern}")
    print("\nStarting benchmarks...\n")
    
    benchmark = Benchmark()
    
    results = benchmark.run_benchmark_all_strategies(
        num_clients=args.clients,
        num_requests_per_client=args.requests,
        requests_per_second=args.rps,
        burst_factor=args.burst
    )
    
    with open('benchmark_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    benchmark.plot_results(results)
    
    if args.load_pattern == 'sine':
        pattern_func = sine_pattern
    elif args.load_pattern == 'spike':
        pattern_func = spike_pattern
    else:
        pattern_func = step_pattern
    
    for strategy_name in benchmark.shields.keys():
        print(f"\nRunning load test with {args.load_pattern} pattern for {strategy_name}...")
        load_results = benchmark.run_load_test(
            strategy_name,
            pattern_func,
            duration_seconds=args.load_test_duration,
            num_clients=args.clients
        )
        
        with open(f'load_test_{strategy_name}_results.json', 'w') as f:
            serializable_results = {k: v for k, v in load_results.items() 
                                  if k not in ['client_stats']}
            json.dump(serializable_results, f, indent=2)
        
        benchmark.plot_load_test(load_results)
    
    print("\nBenchmarking complete!")
    print("Results saved to benchmark_results.json and load_test_*.json")
    print("Plots saved to benchmark_results.png and load_test_*.png")


if __name__ == "__main__":
    main() 