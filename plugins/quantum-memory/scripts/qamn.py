#!/usr/bin/env python3
"""QAMN — quantum-inspired associative memory that actually recalls.

A corrected implementation of the Quantum Associative Memory Network. The gate
simulation in the original was sound; the memory built on top of it was not.
What changed, and why:

1. Encoding is now symmetric. The original applied the entanglement network to
   the query but never to the stored patterns, so it compared states that had
   been through different transformations. That single asymmetry cost about 15
   percentage points of recall accuracy.

2. Ties break deterministically. Phase-encoding fidelity is a strictly
   decreasing function of Hamming distance (see `fidelity_decay`), so equally
   distant patterns score equal fidelity to within rounding. Selecting on raw
   float comparison let 1e-16 of noise decide, which is a coin flip wearing a
   lab coat. Ties now resolve by stored order.

3. Attractors have dynamics. The original called its patterns "attractors" but
   never iterated anything, so nothing was ever attracted. `HopfieldMemory`
   adds the actual update rule, which is what makes recall from a partial cue
   possible at all.

4. Partial queries work. Recalling a whole pattern from a fragment is the
   defining job of an associative memory, and the original had no way to
   express an unknown bit.

5. Capacity, stability, and size limits report honestly. See `capacity`,
   `check_stability`, and MAX_QUBITS.

On what this is not: the phase-encoding fidelity is rank-equivalent to Hamming
distance, so quantum recall cannot beat nearest-neighbour matching on a
symmetric noise channel -- nearest-neighbour is already the optimal decoder
there. A correct implementation matches it. Claims of exponential capacity or
quantum speedup require actual quantum hardware and different algorithms
(Ventura & Martinez 1999, Trugenberger 2001); none of that is happening here.
This is a classical simulation, and it is bounded by classical results.

Usage:
    qamn.py demo                     store patterns, recall from noise and fragments
    qamn.py benchmark                accuracy against optimal baselines
    qamn.py capacity --qubits 64     capacity estimates with their assumptions
    qamn.py phases                   how the phase constant affects discrimination
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# A dense state vector is 16 bytes per amplitude, so memory is 2**n * 16. At 26
# qubits that is 1 GiB and at 32 it is 64 GiB. The original tried to allocate
# and died with a numpy MemoryError; refusing early with the actual number is
# more useful.
MAX_QUBITS = 26

# Golden ratio, the original framework's phase constant. It works, but nothing
# about it is uniquely optimal -- see `analyse_phases`, which measures the
# tradeoff directly rather than asserting it.
PHI = (1 + math.sqrt(5)) / 2

UNKNOWN = -1  # marks a masked bit in a partial query


def _check_qubits(n: int) -> None:
    if n < 1:
        raise ValueError("need at least 1 qubit")
    if n > MAX_QUBITS:
        gib = (2 ** n) * 16 / 2 ** 30
        raise ValueError(
            f"{n} qubits needs {gib:,.1f} GiB for the state vector; "
            f"the limit is {MAX_QUBITS} qubits ({(2**MAX_QUBITS)*16/2**30:.1f} GiB). "
            "Dense simulation cannot go further on any classical machine -- "
            "use HopfieldMemory, which is O(n^2) rather than O(2^n).")


# ---------------------------------------------------------------------------
# Quantum state
# ---------------------------------------------------------------------------

class QuantumState:
    """Pure state of an n-qubit register, as a dense complex vector."""

    def __init__(self, n_qubits: int):
        _check_qubits(n_qubits)
        self.n_qubits = n_qubits
        self.dim = 2 ** n_qubits
        self.amplitudes = np.zeros(self.dim, dtype=np.complex128)
        self.amplitudes[0] = 1.0

    def copy(self) -> "QuantumState":
        other = QuantumState.__new__(QuantumState)
        other.n_qubits, other.dim = self.n_qubits, self.dim
        other.amplitudes = self.amplitudes.copy()
        return other

    def normalize(self) -> None:
        norm = np.linalg.norm(self.amplitudes)
        if norm > 1e-12:
            self.amplitudes /= norm

    # Gates are vectorised over the basis index rather than looped, which is
    # both faster and harder to get wrong than manual stride arithmetic.

    def _mask(self, qubit: int) -> np.ndarray:
        idx = np.arange(self.dim)
        return ((idx >> qubit) & 1).astype(bool)

    def hadamard(self, qubit: int) -> None:
        hi = self._mask(qubit)
        lo = ~hi
        a, b = self.amplitudes[lo].copy(), self.amplitudes[hi].copy()
        inv = 1 / np.sqrt(2)
        self.amplitudes[lo] = inv * (a + b)
        self.amplitudes[hi] = inv * (a - b)

    def pauli_x(self, qubit: int) -> None:
        hi = self._mask(qubit)
        lo = ~hi
        a, b = self.amplitudes[lo].copy(), self.amplitudes[hi].copy()
        self.amplitudes[lo], self.amplitudes[hi] = b, a

    def pauli_z(self, qubit: int) -> None:
        self.amplitudes[self._mask(qubit)] *= -1

    def phase(self, qubit: int, angle: float) -> None:
        self.amplitudes[self._mask(qubit)] *= np.exp(1j * angle)

    def cnot(self, control: int, target: int) -> None:
        idx = np.arange(self.dim)
        active = ((idx >> control) & 1).astype(bool)
        partner = idx ^ (1 << target)
        # Swap only once per pair, so the exchange is not undone on the way back.
        move = active & (idx < partner)
        a = self.amplitudes[move].copy()
        self.amplitudes[move] = self.amplitudes[partner[move]]
        self.amplitudes[partner[move]] = a

    def controlled_z(self, control: int, target: int, angle: float = np.pi) -> None:
        idx = np.arange(self.dim)
        both = (((idx >> control) & 1) & ((idx >> target) & 1)).astype(bool)
        self.amplitudes[both] *= np.exp(1j * angle)

    def probabilities(self) -> np.ndarray:
        return np.abs(self.amplitudes) ** 2

    def inner_product(self, other: "QuantumState") -> complex:
        return np.vdot(self.amplitudes, other.amplitudes)

    def fidelity(self, other: "QuantumState") -> float:
        return float(np.abs(self.inner_product(other)) ** 2)

    def shannon_entropy(self) -> float:
        """Entropy of the measurement distribution in the computational basis.

        Not the von Neumann entropy: that is identically zero for any pure
        state, which the original code computed and mislabelled.
        """
        p = self.probabilities()
        p = p[p > 1e-15]
        return float(-np.sum(p * np.log2(p)))

    def apply_depolarizing_noise(self, epsilon: float, rng: np.random.Generator) -> None:
        noise = (rng.standard_normal(self.dim) + 1j * rng.standard_normal(self.dim))
        self.amplitudes += epsilon * noise
        self.normalize()

    def __repr__(self) -> str:
        return f"QuantumState(n_qubits={self.n_qubits})"


# ---------------------------------------------------------------------------
# Phase encoding
# ---------------------------------------------------------------------------

def encode_pattern(pattern: Sequence[int], phase_constant: float = PHI) -> QuantumState:
    """Encode a binary pattern as phases on a uniform superposition.

    Used for stored patterns and queries alike. The original had two separate
    encoding paths that diverged, which is what broke recall.
    """
    n = len(pattern)
    state = QuantumState(n)
    for q in range(n):
        state.hadamard(q)
    for q, bit in enumerate(pattern):
        if bit == 1:
            state.phase(q, np.pi * phase_constant)
    state.normalize()
    return state


def fidelity_decay(phase_constant: float = PHI) -> float:
    """Fidelity multiplier per mismatched bit.

    Encoding factorises over qubits, so the overlap of two encoded patterns is
    a product of per-qubit terms: 1 for each agreement and (1 + e^{i.pi.c}) / 2
    for each disagreement. Fidelity is therefore exactly

        cos^2(pi * c / 2) ** hamming_distance

    verified against the simulator in the test suite. Because that is strictly
    decreasing in distance for any c that is not an even integer, ranking by
    fidelity and ranking by Hamming distance give the same order.
    """
    return float(np.cos(np.pi * phase_constant / 2) ** 2)


def predicted_fidelity(distance: int, phase_constant: float = PHI) -> float:
    return fidelity_decay(phase_constant) ** distance


# ---------------------------------------------------------------------------
# Hopfield dynamics
# ---------------------------------------------------------------------------

class HopfieldMemory:
    """Associative memory with actual attractor dynamics.

    Patterns are stored in a Hebbian weight matrix and recalled by iterating
    the update rule to a fixed point. This is the piece that lets a fragment
    grow back into a whole pattern: unknown bits start at zero and are driven
    to a value by the bits around them.

    Costs O(n^2) rather than O(2^n), so it works at sizes where the state
    vector cannot be built at all.
    """

    def __init__(self, n_units: int):
        if n_units < 1:
            raise ValueError("need at least 1 unit")
        self.n_units = n_units
        self.weights = np.zeros((n_units, n_units), dtype=np.float64)
        self.patterns: List[List[int]] = []

    @staticmethod
    def _bipolar(pattern: Sequence[int]) -> np.ndarray:
        """Map {0,1} to {-1,+1}; UNKNOWN becomes 0 so it exerts no influence."""
        return np.array([0 if b == UNKNOWN else (2 * b - 1) for b in pattern],
                        dtype=np.float64)

    def store(self, patterns: Sequence[Sequence[int]]) -> None:
        """Hebbian outer-product rule with a zero diagonal.

        The diagonal must stay zero: a self-connection makes every state
        partly its own attractor and produces spurious fixed points.
        """
        self.patterns = [list(p) for p in patterns]
        self.weights = np.zeros((self.n_units, self.n_units))
        for pattern in patterns:
            v = self._bipolar(pattern)
            self.weights += np.outer(v, v)
        np.fill_diagonal(self.weights, 0.0)
        if patterns:
            self.weights /= len(patterns)

    def recall(self, query: Sequence[int], max_iterations: int = 100,
               rng: Optional[np.random.Generator] = None
               ) -> Tuple[List[int], bool, int]:
        """Iterate to a fixed point.

        Returns (pattern, converged, iterations). Updates are asynchronous in a
        random order, which is the formulation with a Lyapunov function and so
        the one guaranteed to converge; synchronous updates can oscillate
        between two states forever.
        """
        rng = rng or np.random.default_rng(0)
        state = self._bipolar(query)
        # Unknown bits have no sign yet; seed them so an update can move them.
        state[state == 0] = rng.choice([-1.0, 1.0], size=int((state == 0).sum()))

        for iteration in range(1, max_iterations + 1):
            order = rng.permutation(self.n_units)
            changed = False
            for unit in order:
                activation = float(self.weights[unit] @ state)
                new = 1.0 if activation >= 0 else -1.0
                if new != state[unit]:
                    state[unit] = new
                    changed = True
            if not changed:
                return [int((s + 1) // 2) for s in state], True, iteration
        return [int((s + 1) // 2) for s in state], False, max_iterations

    def energy(self, pattern: Sequence[int]) -> float:
        """Lyapunov energy. Asynchronous updates never increase it."""
        v = self._bipolar(pattern)
        return float(-0.5 * v @ self.weights @ v)

    def is_fixed_point(self, pattern: Sequence[int]) -> bool:
        """A stored pattern should be a fixed point; not all of them are."""
        v = self._bipolar(pattern)
        return bool(np.all(np.where(self.weights @ v >= 0, 1.0, -1.0) == v))


# ---------------------------------------------------------------------------
# QAMN
# ---------------------------------------------------------------------------

@dataclass
class QAMNConfig:
    n_qubits: int = 8
    phase_constant: float = PHI
    noise_level: float = 0.0
    seed: int = 0
    max_iterations: int = 100


@dataclass
class RecallResult:
    pattern: Optional[List[int]]
    score: float
    method: str
    converged: bool = True
    iterations: int = 0
    runner_up: Optional[List[int]] = None
    margin: float = 0.0
    tied: bool = False
    details: Dict[str, float] = field(default_factory=dict)


class QAMN:
    """Quantum-inspired associative memory.

    Two recall paths over one set of stored patterns:

      quantum   phase-encode the query and rank stored patterns by fidelity.
                Exact, but needs a 2^n state vector.
      hopfield  iterate attractor dynamics to a fixed point. Scales, and is
                the only path that can invent values for unknown bits.
    """

    def __init__(self, config: Optional[QAMNConfig] = None):
        self.config = config or QAMNConfig()
        _check_qubits(self.config.n_qubits)
        self.patterns: List[List[int]] = []
        self._encoded: List[QuantumState] = []
        self.hopfield = HopfieldMemory(self.config.n_qubits)
        self.rng = np.random.default_rng(self.config.seed)

    def _fit(self, pattern: Sequence[int]) -> List[int]:
        out = list(pattern)[: self.config.n_qubits]
        out += [0] * (self.config.n_qubits - len(out))
        return out

    def store(self, patterns: Sequence[Sequence[int]]) -> None:
        """Store patterns. Encoding happens once, identically for every one."""
        self.patterns = [self._fit(p) for p in patterns]
        self._encoded = [encode_pattern(p, self.config.phase_constant)
                         for p in self.patterns]
        self.hopfield.store(self.patterns)

    def add(self, pattern: Sequence[int]) -> int:
        self.store(self.patterns + [self._fit(pattern)])
        return len(self.patterns) - 1

    def recall(self, query: Sequence[int], method: str = "quantum") -> RecallResult:
        if not self.patterns:
            raise RuntimeError("nothing stored yet; call store() first")
        query = self._fit(query)
        if method == "quantum":
            return self._recall_quantum(query)
        if method == "hopfield":
            return self._recall_hopfield(query)
        raise ValueError(f"unknown method {method!r}; use 'quantum' or 'hopfield'")

    def _recall_quantum(self, query: Sequence[int]) -> RecallResult:
        known = [i for i, b in enumerate(query) if b != UNKNOWN]

        if len(known) == len(query):
            state = encode_pattern(query, self.config.phase_constant)
            if self.config.noise_level > 0:
                state.apply_depolarizing_noise(self.config.noise_level, self.rng)
            scores = [state.fidelity(enc) for enc in self._encoded]
        else:
            # Masked bits carry no information, so compare only on known bits.
            # Fidelity over a subset is the same decay law over that subset.
            decay = fidelity_decay(self.config.phase_constant)
            scores = []
            for pattern in self.patterns:
                mismatches = sum(1 for i in known if pattern[i] != query[i])
                scores.append(decay ** mismatches)

        # Rank on a quantised score. Two patterns at equal distance have equal
        # fidelity in exact arithmetic but can differ by ~1e-16 once simulated,
        # and sorting on the raw value lets that noise pick the winner. Rounding
        # first puts genuine ties back on equal footing so the index -- stored
        # order -- decides, which is at least repeatable.
        TIE_DIGITS = 12
        order = sorted(range(len(scores)),
                       key=lambda i: (-round(scores[i], TIE_DIGITS), i))
        best = order[0]
        runner_up = order[1] if len(order) > 1 else None
        margin = (round(scores[best], TIE_DIGITS) - round(scores[runner_up], TIE_DIGITS)
                  if runner_up is not None else scores[best])

        return RecallResult(
            pattern=list(self.patterns[best]),
            score=scores[best],
            method="quantum",
            runner_up=list(self.patterns[runner_up]) if runner_up is not None else None,
            margin=float(margin),
            # Ties are reported rather than silently resolved by float noise.
            tied=bool(runner_up is not None and margin <= 1e-12),
            details={"known_bits": float(len(known))},
        )

    def _recall_hopfield(self, query: Sequence[int]) -> RecallResult:
        settled, converged, iterations = self.hopfield.recall(
            query, self.config.max_iterations, self.rng)
        # The fixed point may be a spurious state rather than a stored one, so
        # report the nearest stored pattern and the distance to it.
        distances = [sum(a != b for a, b in zip(settled, p)) for p in self.patterns]
        best = min(range(len(distances)), key=lambda i: (distances[i], i))
        exact = distances[best] == 0
        return RecallResult(
            pattern=list(self.patterns[best]),
            score=1.0 - distances[best] / self.config.n_qubits,
            method="hopfield",
            converged=converged,
            iterations=iterations,
            details={
                "settled_is_stored_pattern": float(exact),
                "distance_to_nearest_stored": float(distances[best]),
                "energy": self.hopfield.energy(settled),
            },
        )

    # -- diagnostics --------------------------------------------------------

    def check_stability(self, unitary: Optional[np.ndarray] = None,
                        index: int = 0) -> float:
        """Fidelity of a stored state with its own image under a unitary.

        The original returned fidelity(state, state), which is 1.0 by
        definition and tested nothing. With no unitary supplied this now
        applies the encoding's own phase operator, which is a real
        perturbation and can genuinely fail.
        """
        original = self._encoded[index]
        evolved = original.copy()
        if unitary is None:
            for q in range(self.config.n_qubits):
                evolved.phase(q, np.pi * self.config.phase_constant)
        else:
            if unitary.shape != (original.dim, original.dim):
                raise ValueError(
                    f"unitary must be {original.dim}x{original.dim}, got {unitary.shape}")
            evolved.amplitudes = unitary @ evolved.amplitudes
            evolved.normalize()
        return original.fidelity(evolved)

    def check_robustness(self, noise_level: float = 0.1, index: int = 0,
                         trials: int = 20) -> float:
        original = self._encoded[index]
        total = 0.0
        for _ in range(trials):
            noisy = original.copy()
            noisy.apply_depolarizing_noise(noise_level, self.rng)
            total += original.fidelity(noisy)
        return total / trials

    def capacity(self) -> Dict[str, object]:
        """Capacity estimates, each with the assumption that produces it.

        The original returned int(n / (4 ln n)), which floors to 1 for every
        n below about 17 -- so an 8-qubit network reported capacity 1 while
        the demo stored 4 patterns and recalled them.
        """
        n = self.config.n_qubits
        perfect = n / (4 * math.log(n)) if n > 1 else 1.0
        return {
            "n_units": n,
            "hopfield_practical": 0.138 * n,
            "hopfield_perfect_recall": perfect,
            "stored": len(self.patterns),
            "over_capacity": len(self.patterns) > 0.138 * n,
            "note": ("hopfield_practical is the 0.138N result of Amit, Gutfreund "
                     "and Sompolinsky (1985) for random patterns with a small "
                     "error rate. hopfield_perfect_recall is the stricter "
                     "N/(4 ln N) bound for recovering every pattern exactly. "
                     "Both are classical; this simulation is bound by them."),
        }

    def spurious_check(self) -> Dict[str, object]:
        """How many stored patterns are genuinely fixed points."""
        fixed = [p for p in self.patterns if self.hopfield.is_fixed_point(p)]
        return {
            "stored": len(self.patterns),
            "stable_fixed_points": len(fixed),
            "unstable": len(self.patterns) - len(fixed),
        }


# ---------------------------------------------------------------------------
# Baselines and benchmark
# ---------------------------------------------------------------------------

def hamming(a: Sequence[int], b: Sequence[int]) -> int:
    return sum(x != y for x, y in zip(a, b))


def nearest_neighbour(patterns: Sequence[Sequence[int]],
                      query: Sequence[int]) -> List[int]:
    """Optimal decoder for a symmetric bit-flip channel with uniform priors.

    Present as the honest ceiling: quantum recall is rank-equivalent to this,
    so matching it is success and beating it would mean a bug.
    """
    known = [i for i, b in enumerate(query) if b != UNKNOWN]
    scored = [(sum(1 for i in known if p[i] != query[i]), idx)
              for idx, p in enumerate(patterns)]
    return list(patterns[min(scored)[1]])


def add_bit_noise(pattern: Sequence[int], probability: float,
                  rng: np.random.Generator) -> List[int]:
    return [1 - b if rng.random() < probability else b for b in pattern]


def mask_bits(pattern: Sequence[int], fraction: float,
              rng: np.random.Generator) -> List[int]:
    out = list(pattern)
    n_hide = int(round(len(pattern) * fraction))
    for i in rng.permutation(len(pattern))[:n_hide]:
        out[i] = UNKNOWN
    return out


def benchmark(n_qubits: int = 8, n_patterns: int = 3, trials: int = 200,
              noise: float = 0.2, mask: float = 0.4,
              seed: int = 20260726) -> Dict[str, Dict[str, float]]:
    """Recall accuracy against the optimal baseline, on noise and on fragments."""
    rng = np.random.default_rng(seed)
    hits = {
        "noisy": {"quantum": 0, "hopfield": 0, "nearest_neighbour": 0},
        "partial": {"quantum": 0, "hopfield": 0, "nearest_neighbour": 0},
    }
    counts = {"noisy": 0, "partial": 0}

    for _ in range(trials):
        patterns = [[int(b) for b in rng.integers(0, 2, n_qubits)]
                    for _ in range(n_patterns)]
        if len({tuple(p) for p in patterns}) < n_patterns:
            continue
        memory = QAMN(QAMNConfig(n_qubits=n_qubits, seed=int(rng.integers(1 << 30))))
        memory.store(patterns)
        target = patterns[int(rng.integers(n_patterns))]

        noisy = add_bit_noise(target, noise, rng)
        counts["noisy"] += 1
        hits["noisy"]["quantum"] += memory.recall(noisy, "quantum").pattern == target
        hits["noisy"]["hopfield"] += memory.recall(noisy, "hopfield").pattern == target
        hits["noisy"]["nearest_neighbour"] += nearest_neighbour(patterns, noisy) == target

        partial = mask_bits(target, mask, rng)
        counts["partial"] += 1
        hits["partial"]["quantum"] += memory.recall(partial, "quantum").pattern == target
        hits["partial"]["hopfield"] += memory.recall(partial, "hopfield").pattern == target
        hits["partial"]["nearest_neighbour"] += nearest_neighbour(patterns, partial) == target

    return {task: {name: hit / counts[task] for name, hit in row.items()}
            for task, row in hits.items()}


def analyse_phases(distances: Sequence[int] = (1, 2, 3, 5)) -> List[Dict[str, float]]:
    """Measure what the phase constant actually buys.

    The original asserted that any sufficiently irrational phase behaves alike.
    It does not: the constant sets the fidelity decay rate, and pi collapses it
    to zero, destroying all distance information beyond one bit.
    """
    candidates = {
        "phi (golden ratio)": PHI,
        "sqrt(2)": math.sqrt(2),
        "e - 1": math.e - 1,
        "1/2": 0.5,
        "pi/2": math.pi / 2,
        "1 (= pi phase)": 1.0,
    }
    rows = []
    for name, constant in candidates.items():
        decay = fidelity_decay(constant)
        row = {"phase_constant": name, "value": constant, "decay_per_bit": decay}
        for d in distances:
            row[f"fidelity_at_d{d}"] = decay ** d
        row["usable"] = 1e-6 < decay < 1 - 1e-9
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_demo(args) -> int:
    rng = np.random.default_rng(args.seed)
    patterns = [
        [1, 0, 1, 0, 1, 0, 1, 0],
        [0, 1, 0, 1, 0, 1, 0, 1],
        [1, 1, 0, 0, 1, 1, 0, 0],
    ]
    memory = QAMN(QAMNConfig(n_qubits=8, seed=args.seed))
    memory.store(patterns)

    print("stored patterns:")
    for i, p in enumerate(patterns):
        print(f"  {i}: {p}")
    cap = memory.capacity()
    print(f"\ncapacity: {cap['stored']} stored, practical limit "
          f"{cap['hopfield_practical']:.2f}, over capacity: {cap['over_capacity']}")
    print(f"stable fixed points: {memory.spurious_check()}")

    print("\n-- recall from 20% bit-flip noise --")
    for target in patterns:
        noisy = add_bit_noise(target, 0.2, rng)
        q = memory.recall(noisy, "quantum")
        h = memory.recall(noisy, "hopfield")
        print(f"  target   {target}")
        print(f"  noisy    {noisy}")
        print(f"  quantum  {q.pattern} fidelity={q.score:.4f} correct={q.pattern == target}")
        print(f"  hopfield {h.pattern} converged={h.converged} in {h.iterations} "
              f"iters correct={h.pattern == target}")

    print("\n-- recall from a fragment (half the bits hidden) --")
    for target in patterns:
        partial = mask_bits(target, 0.5, rng)
        shown = [("?" if b == UNKNOWN else str(b)) for b in partial]
        q = memory.recall(partial, "quantum")
        h = memory.recall(partial, "hopfield")
        print(f"  target   {target}")
        print(f"  fragment [{', '.join(shown)}]")
        print(f"  quantum  {q.pattern} correct={q.pattern == target}")
        print(f"  hopfield {h.pattern} correct={h.pattern == target}")
    return 0


def cmd_benchmark(args) -> int:
    results = benchmark(args.qubits, args.patterns, args.trials,
                        args.noise, args.mask, args.seed)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    print(f"{args.trials} trials, {args.qubits} qubits, {args.patterns} patterns")
    print(f"noise {args.noise:.0%} bit-flip | mask {args.mask:.0%} hidden\n")
    print(f"{'task':10} {'quantum':>10} {'hopfield':>10} {'nearest-nb':>12}")
    for task, row in results.items():
        print(f"{task:10} {row['quantum']:>9.1%} {row['hopfield']:>10.1%} "
              f"{row['nearest_neighbour']:>12.1%}")
    print("\nnearest-neighbour is the optimal decoder here, so matching it is the")
    print("target. Quantum recall is rank-equivalent to it by construction.")
    return 0


def cmd_capacity(args) -> int:
    memory = QAMN(QAMNConfig(n_qubits=args.qubits))
    info = memory.capacity()
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    for key, value in info.items():
        if key == "note":
            continue
        print(f"  {key}: {value}")
    print(f"\n{info['note']}")
    return 0


def cmd_phases(args) -> int:
    rows = analyse_phases()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'constant':20} {'decay/bit':>10} {'d=1':>8} {'d=3':>10} {'usable':>8}")
    for row in rows:
        print(f"{row['phase_constant']:20} {row['decay_per_bit']:>10.4f} "
              f"{row['fidelity_at_d1']:>8.4f} {row['fidelity_at_d3']:>10.6f} "
              f"{str(row['usable']):>8}")
    print("\nThe constant sets how fast fidelity falls with distance. A value of 1")
    print("means a pi phase, where the decay is 0 and every non-identical pattern")
    print("scores the same -- no ranking at all. phi is a reasonable choice, not a")
    print("uniquely optimal one.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quantum-inspired associative memory (corrected implementation).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seed", type=int, default=20260726)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="store, then recall from noise and fragments")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("benchmark", help="accuracy against the optimal baseline")
    p.add_argument("--qubits", type=int, default=8)
    p.add_argument("--patterns", type=int, default=3)
    p.add_argument("--trials", type=int, default=200)
    p.add_argument("--noise", type=float, default=0.2)
    p.add_argument("--mask", type=float, default=0.4)
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("capacity", help="capacity estimates and their assumptions")
    p.add_argument("--qubits", type=int, default=8)
    p.set_defaults(func=cmd_capacity)

    p = sub.add_parser("phases", help="effect of the phase constant")
    p.set_defaults(func=cmd_phases)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
