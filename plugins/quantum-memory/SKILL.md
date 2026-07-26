---
name: quantum-memory
description: |
  Quantum-inspired associative memory: store binary patterns and recall a whole
  pattern from a noisy or partial cue, using either phase-encoded quantum state
  simulation or Hopfield attractor dynamics. Use when: (1) building or debugging
  content-addressable or associative memory, (2) recalling a complete record
  from a fragment, (3) working with Hopfield networks, attractor dynamics,
  energy functions, or spurious states, (4) simulating small quantum circuits
  with Hadamard, Pauli, phase, CNOT, or controlled-Z gates, (5) reasoning about
  quantum state fidelity, superposition, or phase encoding, (6) checking whether
  a claimed quantum speedup or memory capacity is real.
when_to_use: |
  Also use when someone presents quantum machine learning code and asks whether
  it works, since this skill carries a measured baseline to compare against.
  Trigger phrases: associative memory, content-addressable memory, Hopfield
  network, attractor, pattern completion, recall from partial input, quantum
  state simulation, qubit, fidelity, superposition, QAMN, quantum memory.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/qamn.py *)
author: Claudeception
version: 1.0.0
date: 2026-07-26
---

# Quantum Memory

## Problem

Associative memory is the thing that recalls a whole record from a piece of
one: half a pattern in, the complete pattern out. It is easy to build something
that looks like one and does not work, because the failure is quiet. Recall
returns *a* pattern every time. Whether it returns the *right* one only shows
up against a baseline, and quantum-flavoured implementations frequently skip
that comparison.

This skill provides a working implementation and, more importantly, the
baseline to judge it against.

## The Tool

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/qamn.py demo        # noise and fragment recall
python3 ${CLAUDE_SKILL_DIR}/scripts/qamn.py benchmark   # accuracy vs optimal decoder
python3 ${CLAUDE_SKILL_DIR}/scripts/qamn.py capacity --qubits 64
python3 ${CLAUDE_SKILL_DIR}/scripts/qamn.py phases      # effect of the phase constant
```

As a library: `QAMN` stores patterns and recalls them two ways.

| Method | Mechanism | Cost | Use when |
| --- | --- | --- | --- |
| `quantum` | phase-encode the query, rank stored patterns by state fidelity | O(2^n) memory | n ≤ 26, and you want the quantum simulation |
| `hopfield` | iterate attractor dynamics to a fixed point | O(n²) | large n, or you need the settled state itself |

Mark unknown bits with `qamn.UNKNOWN` (`-1`) to recall from a fragment.

## The Result That Governs Everything Here

Phase encoding puts a pattern on a uniform superposition as per-qubit phases.
That factorises, so the overlap of two encoded patterns is a product of
per-qubit terms, and fidelity has an exact closed form:

```
fidelity = cos²(π·c / 2) ^ hamming_distance
```

with `c` the phase constant (φ by default, giving 0.681187 per mismatched bit).
This is verified against the simulator in the test suite to 9 decimal places.

Two consequences follow, and both matter:

**Quantum recall cannot beat nearest-neighbour matching.** Fidelity is strictly
decreasing in Hamming distance, so ranking by fidelity and ranking by distance
produce the same order. On a symmetric bit-flip channel with uniform priors,
nearest-neighbour is already the optimal decoder. Matching it is success;
beating it means a bug in the measurement.

**The phase constant is not arbitrary, and not magic.** It sets the decay rate.
A π phase (`c = 1`) makes the decay exactly zero, so every non-identical pattern
ties and ranking collapses entirely. φ works. So do √2 and 1/2, with different
discrimination curves. Run `qamn.py phases` rather than assuming.

## Measured Behaviour

200 trials, 20% bit-flip noise, 40% of bits hidden:

| Setup | Task | quantum | hopfield | nearest-neighbour |
| --- | --- | --- | --- | --- |
| 8 qubits, 3 patterns (≈3× over capacity) | noisy | 85.2% | 71.9% | 85.2% |
| 8 qubits, 3 patterns | fragment | 96.4% | 70.9% | 96.4% |
| 16 qubits, 2 patterns (within capacity) | noisy | 95.3% | 95.3% | 95.3% |
| 16 qubits, 2 patterns | fragment | 100% | 94.0% | 100% |

Quantum recall tracks the optimal decoder exactly, as the closed form requires.
Hopfield degrades when loaded past capacity, which is the expected behaviour and
the reason `capacity()` reports `over_capacity`.

## Capacity

`capacity()` returns two numbers with their assumptions attached:

- `hopfield_practical` — 0.138·N, the Amit–Gutfreund–Sompolinsky (1985) result
  for random patterns tolerating a small error rate.
- `hopfield_perfect_recall` — N/(4·ln N), the stricter bound for recovering
  every stored pattern exactly.

Both are classical bounds and this is a classical simulation, so both apply.
Eight units hold roughly **one** pattern reliably. Storing three is three times
over, and the measured degradation above is what that looks like.

Proposals for exponential quantum capacity (Ventura & Martinez 1999,
Trugenberger 2001) require actual quantum hardware with maintained coherence
plus specific oracle constructions. None of that is simulated here, and no
classical simulation can deliver it.

## Traps

- **Asymmetric encoding.** Queries and stored patterns must go through exactly
  the same transformation. Applying an operation to one and not the other
  silently destroys accuracy — it cost 15 points in the implementation this
  replaces, while still returning confident answers.
- **Ties decided by float noise.** Equidistant patterns have equal fidelity in
  exact arithmetic and differ by ~1e-16 once simulated. Sorting on the raw value
  lets rounding pick the winner. Quantise before ranking, then break ties by a
  fixed rule.
- **Calling something an attractor with no dynamics.** An attractor network that
  never iterates has no basins and cannot complete a partial pattern. If there
  is no update loop, the word is decoration.
- **Self-connections in the weight matrix.** A non-zero diagonal makes every
  state partly its own attractor and manufactures spurious fixed points. Zero it.
- **Synchronous updates.** Only the asynchronous rule has a Lyapunov function
  and is guaranteed to converge; synchronous updates can oscillate forever.
- **Vacuous verification.** `fidelity(state, state)` is 1.0 by definition and
  tests nothing. Check stability against an actual perturbation.
- **The 2^n wall.** Dense state vectors need 16·2^n bytes: 1 GiB at 26 qubits,
  64 GiB at 32. This is why `MAX_QUBITS` refuses early with the figure instead
  of dying in the allocator. Use `HopfieldMemory` beyond it.

## Verification

Before believing any associative memory, including this one:

1. Compare against nearest-neighbour on the same queries. If it loses, it is
   broken; if it wins, the measurement is broken.
2. Confirm stored patterns are actually fixed points (`spurious_check`).
3. Confirm energy never increases across an update (`energy`).
4. Check the load against capacity before interpreting any accuracy number.

## Notes

Adapted from a QAMN implementation whose gate simulation was sound but whose
memory layer was not. Four defects are fixed and covered by regression tests:
`cnot` double-swapped each pair and was a no-op, so no entanglement was possible
at all; the entanglement network was applied to queries but not stored patterns;
`check_stability` returned self-fidelity, which is always 1.0; and `capacity`
floored to 1 for every size below 17 units.

## References

- Hopfield, J. J. (1982). Neural networks and physical systems with emergent
  collective computational abilities. *PNAS* 79(8).
- Amit, Gutfreund & Sompolinsky (1985). Storing infinite numbers of patterns in
  a spin-glass model of neural networks. *Phys. Rev. Lett.* 55.
- Ventura & Martinez (1999). Quantum associative memory. arXiv:quant-ph/9807053
- Trugenberger, C. A. (2001). Probabilistic quantum memories.
  *Phys. Rev. Lett.* 87.
