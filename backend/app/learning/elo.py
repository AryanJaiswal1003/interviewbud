"""Elo-style person x item calibration (Pelánek, "Applications of the Elo rating system in adaptive
educational systems"). p = 1 / (1 + 10^((d - theta)/400)); both ratings move by K * (s - p)."""


def expected(theta: float, difficulty: float) -> float:
    return 1.0 / (1.0 + 10 ** ((difficulty - theta) / 400.0))


def k_factor(attempts: int) -> float:
    # Uncertainty decays with evidence: new items move fast, well-attempted ones settle.
    return max(16.0, 64.0 / (1.0 + 0.25 * attempts))


def outcome(hidden_pass_rate: float, hard_pass_rate: float, max_hint_level: int, solved_optimal: bool) -> float:
    """Hint-adjusted performance in [0, 1]."""
    s = 0.6 * hidden_pass_rate + 0.25 * hard_pass_rate + (0.15 if solved_optimal else 0.0)
    penalty = {0: 0.0, 1: 0.03, 2: 0.08, 3: 0.15, 4: 0.3, 5: 0.5}.get(max_hint_level, 0.5)
    return max(0.0, min(1.0, s - penalty))


def update(theta: float, difficulty: float, s: float, item_attempts: int) -> tuple[float, float]:
    p = expected(theta, difficulty)
    delta = s - p
    new_theta = theta + 40.0 * delta
    new_d = difficulty - k_factor(item_attempts) * delta
    return new_theta, new_d
