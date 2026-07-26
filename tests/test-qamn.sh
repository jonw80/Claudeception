#!/usr/bin/env bash
#
# Test suite for the quantum-memory plugin.
#
# Most cases here are regressions against specific defects in the original
# implementation, each of which is named in the test that covers it. Skips
# cleanly when numpy is unavailable.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QAMN_DIR="$REPO_ROOT/plugins/quantum-memory/scripts"

if ! python3 -c "import numpy" 2>/dev/null; then
  echo "skipping qamn tests: numpy is not installed"
  echo "  install with: pip install numpy"
  exit 0
fi

run_case() {
  local name="$1" script="$2"
  if OUT="$(cd "$QAMN_DIR" && timeout 300 python3 -c "$script" 2>&1)"; then
    printf '  ok    %s\n' "$name"
    return 0
  fi
  printf '  FAIL  %s\n' "$name"
  printf '        %s\n' "$(printf '%s' "$OUT" | tail -4)"
  return 1
}

PASS=0
FAIL=0
check() { if run_case "$1" "$2"; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi; }

echo "== gate correctness =="

# The original cnot swapped each pair twice, making it an identity operation.
check "CNOT matches its truth table" '
import numpy as np, qamn
for c in (0,1):
    for t in (0,1):
        s = qamn.QuantumState(2); s.amplitudes[:] = 0
        idx = c + 2*t
        s.amplitudes[idx] = 1.0
        s.cnot(0, 1)
        expected = idx ^ (2 if c else 0)
        got = int(np.argmax(np.abs(s.amplitudes)))
        assert got == expected, f"c={c} t={t}: got {got} want {expected}"
'

check "CNOT creates a Bell state" '
import numpy as np, qamn
s = qamn.QuantumState(2)
s.hadamard(0); s.cnot(0, 1)
p = s.probabilities()
assert abs(p[0]-0.5) < 1e-12 and abs(p[3]-0.5) < 1e-12, p
assert p[1] < 1e-12 and p[2] < 1e-12, p
'

check "Hadamard is self-inverse and normalising" '
import numpy as np, qamn
s = qamn.QuantumState(3)
for q in range(3): s.hadamard(q)
assert abs(np.linalg.norm(s.amplitudes) - 1) < 1e-12
assert np.allclose(s.probabilities(), 1/8)
for q in range(3): s.hadamard(q)
assert abs(s.amplitudes[0] - 1) < 1e-12, s.amplitudes[0]
'

echo "== encoding =="

# Fidelity factorises over qubits, giving an exact closed form.
check "fidelity equals cos^2(pi*c/2)^hamming exactly" '
import qamn
for c in (qamn.PHI, 0.5, 1.4142135623730951):
    base = qamn.encode_pattern([0]*6, c)
    for d in range(7):
        p = [1]*d + [0]*(6-d)
        got = base.fidelity(qamn.encode_pattern(p, c))
        want = qamn.predicted_fidelity(d, c)
        assert abs(got-want) < 1e-9, f"c={c} d={d}: {got} vs {want}"
'

# If this ordering ever broke, quantum recall would stop tracking the optimal
# decoder and every accuracy claim below would be meaningless.
check "fidelity ranking matches Hamming ranking" '
import itertools, qamn
n = 5
states = {b: qamn.encode_pattern(list(b)) for b in itertools.product([0,1], repeat=n)}
ref = (0,)*n
for a in states:
    for b in states:
        da = sum(x!=y for x,y in zip(ref,a)); db = sum(x!=y for x,y in zip(ref,b))
        if da < db:
            fa = states[ref].fidelity(states[a]); fb = states[ref].fidelity(states[b])
            assert fa > fb + 1e-12, f"{a}(d={da}) scored below {b}(d={db})"
'

# The original claimed any irrational phase behaves alike. A pi phase makes the
# decay exactly zero, so every distinct pattern ties and ranking is destroyed.
check "phase analysis flags a pi phase as unusable" '
import qamn
rows = {r["phase_constant"]: r for r in qamn.analyse_phases()}
assert rows["1 (= pi phase)"]["usable"] is False
assert rows["1 (= pi phase)"]["decay_per_bit"] < 1e-12
assert rows["phi (golden ratio)"]["usable"] is True
'

echo "== recall =="

# The original applied entanglement to the query but not to stored patterns,
# costing ~15 points of accuracy. Encoding is now shared.
check "quantum recall matches the optimal decoder" '
import numpy as np, qamn
r = qamn.benchmark(n_qubits=10, n_patterns=2, trials=120, seed=99)
q, nn = r["noisy"]["quantum"], r["noisy"]["nearest_neighbour"]
assert abs(q-nn) < 0.05, f"quantum {q:.3f} vs nearest-neighbour {nn:.3f}"
qp, nnp = r["partial"]["quantum"], r["partial"]["nearest_neighbour"]
assert abs(qp-nnp) < 0.05, f"partial: quantum {qp:.3f} vs nn {nnp:.3f}"
'

# The original had no way to express an unknown bit, so this was impossible.
check "recall works from a partial cue" '
import qamn
m = qamn.QAMN(qamn.QAMNConfig(n_qubits=12, seed=3))
pats = [[1,0,1,0,1,0,1,0,1,0,1,0],[0,0,1,1,0,0,1,1,0,0,1,1]]
m.store(pats)
frag = list(pats[0]);
for i in (2,3,5,7,9,11): frag[i] = qamn.UNKNOWN
assert m.recall(frag, "quantum").pattern == pats[0]
assert m.recall(frag, "hopfield").pattern == pats[0]
'

check "ties are reported rather than resolved by float noise" '
import qamn
m = qamn.QAMN(qamn.QAMNConfig(n_qubits=4, seed=1))
m.store([[0,0,0,0],[1,1,1,1]])
res = m.recall([0,0,1,1], "quantum")   # equidistant from both
assert res.tied is True, res
assert res.pattern == [0,0,0,0], "ties must resolve by stored order, deterministically"
assert m.recall([0,0,1,1], "quantum").pattern == res.pattern, "must be repeatable"
'

echo "== attractor dynamics =="

# The original called its patterns attractors but never iterated anything.
check "hopfield converges and stored patterns are fixed points" '
import qamn
m = qamn.QAMN(qamn.QAMNConfig(n_qubits=20, seed=5))
pats = [[(i//3+j)%2 for i in range(20)] for j in range(2)]
m.store(pats)
info = m.spurious_check()
assert info["stable_fixed_points"] == info["stored"], info
for p in pats:
    r = m.recall(p, "hopfield")
    assert r.converged and r.pattern == p, r
'

check "energy never increases under the update rule" '
import qamn, numpy as np
h = qamn.HopfieldMemory(16)
pats = [[int(b) for b in np.random.default_rng(i).integers(0,2,16)] for i in range(2)]
h.store(pats)
rng = np.random.default_rng(0)
start = qamn.add_bit_noise(pats[0], 0.25, rng)
settled, converged, _ = h.recall(start, rng=rng)
assert converged
assert h.energy(settled) <= h.energy(start) + 1e-9, (h.energy(settled), h.energy(start))
'

echo "== honest reporting =="

# The original returned int(n/(4*ln n)), which floors to 1 for every n < 17,
# so an 8-qubit network reported capacity 1 while storing 4 patterns.
check "capacity does not floor to 1 and flags over-capacity" '
import qamn
for n in (4, 8, 16):
    info = qamn.QAMN(qamn.QAMNConfig(n_qubits=n)).capacity()
    assert isinstance(info["hopfield_practical"], float)
    assert abs(info["hopfield_practical"] - 0.138*n) < 1e-9, info
m = qamn.QAMN(qamn.QAMNConfig(n_qubits=8))
m.store([[i%2]*8 for i in range(4)][:3])
assert m.capacity()["over_capacity"] is True
'

# The original returned fidelity(state, state), which is 1.0 by definition.
check "stability check is not trivially 1.0" '
import qamn
m = qamn.QAMN(qamn.QAMNConfig(n_qubits=4, seed=2))
m.store([[1,0,1,0]])
s = m.check_stability()
assert s < 0.999, f"stability {s} looks like the vacuous self-fidelity check"
assert 0.0 <= s <= 1.0
'

# The original tried to allocate 64 GiB and died with a numpy MemoryError.
check "oversized register fails with a clear message" '
import qamn
try:
    qamn.QuantumState(32)
except ValueError as e:
    assert "GiB" in str(e) and "26" in str(e), str(e)
except MemoryError:
    raise AssertionError("raised MemoryError instead of refusing up front")
else:
    raise AssertionError("no error for 32 qubits")
'

check "hopfield scales past the state-vector ceiling" '
import qamn
h = qamn.HopfieldMemory(200)   # 2**200 amplitudes is impossible; O(n^2) is fine
h.store([[i%2 for i in range(200)]])
out, converged, _ = h.recall([i%2 for i in range(200)])
assert converged and out == [i%2 for i in range(200)]
'

echo "== cli =="
for sub in demo benchmark capacity phases; do
  if (cd "$QAMN_DIR" && timeout 300 ./qamn.py "$sub" >/dev/null 2>&1); then
    PASS=$((PASS+1)); printf '  ok    %s runs\n' "$sub"
  else
    FAIL=$((FAIL+1)); printf '  FAIL  %s runs\n' "$sub"
  fi
done

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
