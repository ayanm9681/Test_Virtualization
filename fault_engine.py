import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class FaultDecision:
    extra_delay: float = 0.0
    status_override: Optional[int] = None
    body_override: Optional[dict] = None


class FaultEngine:
    def __init__(self) -> None:
        self.active_scenario: Optional[Dict[str, Any]] = None
        self.scenario_start: Optional[float] = None

        # Concurrent in-progress request count, keyed by api_path. Tracked on
        # every request regardless of whether a scenario is active, so
        # load_correlated always sees an accurate concurrency picture.
        self.in_flight: Dict[str, int] = {}

        # Whether a given "root" endpoint is currently considered failing,
        # used by cascading_dependency.
        self.endpoint_health: Dict[str, bool] = {}

        # Per-root request counts, used by cascading_dependency's
        # trigger_after_requests condition.
        self._cascade_request_counts: Dict[str, int] = {}

        # Request timestamps within the current rate-limit window, keyed by
        # api_path, used by rate_limit.
        self.request_log: Dict[str, list] = {}

        self._handlers = {
            "latency_creep": self._eval_latency_creep,
            "flaky": self._eval_flaky,
            "load_correlated": self._eval_load_correlated,
            "cascading_dependency": self._eval_cascading_dependency,
            "transient_blip": self._eval_transient_blip,
            "rate_limit": self._eval_rate_limit,
        }

    def activate(self, scenario_type: str, params: Dict[str, Any]) -> None:
        self.active_scenario = {"type": scenario_type, "params": params}
        self.scenario_start = time.time()
        self._clear_state()

    def deactivate(self) -> None:
        self.active_scenario = None
        self.scenario_start = None
        self._clear_state()

    def _clear_state(self) -> None:
        """Reset per-scenario tracking state."""
        self.in_flight = {}
        self.endpoint_health = {}
        self._cascade_request_counts = {}
        self.request_log = {}

    def debug_state(self) -> Dict[str, Any]:
        """Live counters for the control UI's status view -- never exposed
        to the agents under test."""
        return {
            "in_flight": dict(self.in_flight),
            "endpoint_health": dict(self.endpoint_health),
            "request_log_counts": {k: len(v) for k, v in self.request_log.items()},
        }

    def increment_in_flight(self, api_path: str) -> None:
        self.in_flight[api_path] = self.in_flight.get(api_path, 0) + 1

    def decrement_in_flight(self, api_path: str) -> None:
        self.in_flight[api_path] = max(0, self.in_flight.get(api_path, 0) - 1)

    def evaluate(self, api_path: str, method: str) -> FaultDecision:
        if not self.active_scenario:
            return FaultDecision()

        scenario_type = self.active_scenario.get("type")
        handler = self._handlers.get(scenario_type)
        if handler is None:
            return FaultDecision()

        return handler(api_path, method)

    def elapsed_seconds(self) -> float:
        if self.scenario_start is None:
            return 0.0
        return time.time() - self.scenario_start

    def _eval_latency_creep(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        if api_path != params.get("endpoint"):
            return FaultDecision()

        start_delay = params.get("start_delay", 0.0)
        max_delay = params.get("max_delay", start_delay)
        ramp_duration = params.get("ramp_duration_seconds", 0.0)

        if ramp_duration <= 0:
            delay = max_delay
        else:
            fraction = min(self.elapsed_seconds() / ramp_duration, 1.0)
            delay = start_delay + fraction * (max_delay - start_delay)

        return FaultDecision(extra_delay=delay)

    def _eval_flaky(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        if api_path != params.get("endpoint"):
            return FaultDecision()

        failure_rate = params.get("failure_rate", 0.0)
        if random.random() < failure_rate:
            error_status = params.get("error_status", 500)
            error_body = params.get("error_body", {"error": "simulated failure"})
            return FaultDecision(status_override=error_status, body_override=error_body)

        return FaultDecision()

    def _eval_load_correlated(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        endpoint = params.get("endpoint")
        if api_path != endpoint:
            return FaultDecision()

        threshold = params.get("concurrency_threshold", 0)
        current = self.in_flight.get(endpoint, 0)
        if current <= threshold:
            return FaultDecision()

        decision = FaultDecision(extra_delay=params.get("degraded_delay", 0.0))
        degraded_failure_rate = params.get("degraded_failure_rate", 0.0)
        if random.random() < degraded_failure_rate:
            decision.status_override = params.get("error_status", 503)
            decision.body_override = params.get("error_body", {"error": "service degraded under load"})

        return decision

    def _cascade_trigger_met(self, params: Dict[str, Any]) -> bool:
        trigger_after_seconds = params.get("trigger_after_seconds")
        if trigger_after_seconds is not None:
            return self.elapsed_seconds() >= trigger_after_seconds

        trigger_after_requests = params.get("trigger_after_requests")
        if trigger_after_requests is not None:
            root = params.get("root_endpoint")
            return self._cascade_request_counts.get(root, 0) >= trigger_after_requests

        return False

    def _eval_cascading_dependency(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        root_endpoint = params.get("root_endpoint")
        dependent_endpoints = params.get("dependent_endpoints", [])

        if api_path == root_endpoint:
            self._cascade_request_counts[root_endpoint] = (
                self._cascade_request_counts.get(root_endpoint, 0) + 1
            )

            if self.endpoint_health.get(root_endpoint, True) and self._cascade_trigger_met(params):
                self.endpoint_health[root_endpoint] = False

            if not self.endpoint_health.get(root_endpoint, True):
                error_status = params.get("root_error_status", 500)
                error_body = params.get(
                    "root_error_body", {"error": f"{root_endpoint} is failing"}
                )
                return FaultDecision(status_override=error_status, body_override=error_body)

            return FaultDecision()

        if api_path in dependent_endpoints:
            if not self.endpoint_health.get(root_endpoint, True):
                error_status = params.get("dependent_error_status", 502)
                error_body = params.get(
                    "dependent_error_body",
                    {"error": "upstream dependency unavailable", "dependency": root_endpoint},
                )
                return FaultDecision(status_override=error_status, body_override=error_body)

            return FaultDecision()

        return FaultDecision()

    def _eval_transient_blip(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        if api_path != params.get("endpoint"):
            return FaultDecision()

        start_offset = params.get("start_offset_seconds", 0.0)
        duration = params.get("duration_seconds", 0.0)
        elapsed = self.elapsed_seconds()

        if start_offset <= elapsed <= start_offset + duration:
            error_status = params.get("error_status", 500)
            error_body = params.get("error_body", {"error": "transient blip"})
            return FaultDecision(status_override=error_status, body_override=error_body)

        return FaultDecision()

    def _eval_rate_limit(self, api_path: str, method: str) -> FaultDecision:
        params = self.active_scenario["params"]
        endpoint = params.get("endpoint")
        if api_path != endpoint:
            return FaultDecision()

        window_seconds = params.get("window_seconds", 0.0)
        limit = params.get("limit_per_window", 0)

        now = time.time()
        cutoff = now - window_seconds
        log = [t for t in self.request_log.get(endpoint, []) if t >= cutoff]
        log.append(now)
        self.request_log[endpoint] = log

        if len(log) > limit:
            return FaultDecision(status_override=429, body_override={"error": "rate limit exceeded"})

        return FaultDecision()


fault_engine = FaultEngine()
