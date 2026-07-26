#!/usr/bin/env bash
#
# Test suite for the verified-math plugin's calc.py.
#
# The cases here are mostly regressions. Each one caught a real bug during
# development, and several are chosen specifically because a plausible-looking
# implementation gets them wrong:
#
#   atan(1)+atan(2)+atan(3) == pi   true, but simplify() cannot reduce it, so
#                                   treating "did not simplify" as a disproof
#                                   reports a true identity as false
#   exp(pi*sqrt(163)) == 2625...4   false, but the sides agree to ~30
#                                   significant figures, so a relative-
#                                   tolerance float compare says they match
#   x**2 > 0                        false only at x=0, which random sampling
#                                   never lands on
#   x**5 - x - 1 = 0                roots are CRootOf; verifying them the
#                                   obvious way costs ~37s each
#
# Skips cleanly when sympy is unavailable.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALC="$REPO_ROOT/plugins/verified-math/scripts/calc.py"

if ! python3 -c "import sympy, mpmath" 2>/dev/null; then
  echo "skipping calc.py tests: sympy and mpmath are not installed"
  echo "  install with: pip install sympy mpmath"
  exit 0
fi

PASS=0
FAIL=0

ok() { printf '  ok    %s\n' "$1"; PASS=$((PASS + 1)); }
no() { printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; FAIL=$((FAIL + 1)); }

# claim_is <expected-exit> <claim> [extra args...]
claim_is() {
  local expected="$1" claim="$2"; shift 2
  local output status
  output="$(timeout 90 python3 "$CALC" check "$claim" "$@" 2>&1)"
  status=$?
  if [ "$status" = "$expected" ]; then
    ok "check: $claim"
  else
    no "check: $claim" "expected exit $expected, got $status"
    printf '        %s\n' "$(printf '%s' "$output" | tail -3)"
  fi
}

echo "== check: true claims =="
claim_is 0 "2 + 2 == 4"
claim_is 0 "sin(x)**2 + cos(x)**2 == 1"
claim_is 0 "(x+1)**2 == x**2 + 2*x + 1"
claim_is 0 "exp(I*pi) + 1 == 0"
claim_is 0 "log(exp(x)) == x"
claim_is 0 "x**2 >= 0"
claim_is 0 "sin(x) <= 1"
# simplify() cannot reduce this; "did not simplify" must not mean "false"
claim_is 0 "atan(1) + atan(2) + atan(3) == pi"

echo "== check: false claims =="
claim_is 1 "2 + 2 == 5"
claim_is 1 "(x+1)**2 == x**2 + 1"
claim_is 1 "sqrt(x**2) == x"
# agrees to ~30 significant figures but is not equal
claim_is 1 "exp(pi*sqrt(163)) == 262537412640768744"
# fails only at x=0, which random sampling never hits
claim_is 1 "x**2 > 0"

echo "== check: simplify() is audited, not trusted =="
# sympy's simplify() is not value-preserving on small Floats: it returns
# exactly 0 for 1.380649e-23*300*log(2) (the Landauer limit at 300K, ~2.87e-21).
# Taking that at face value made check() report this false claim as TRUE, and
# the dual-method cross-check could not catch it because both methods ran on
# the already-corrupted expression.
claim_is 1 "1.380649e-23 * 300 * log(2) == 0"
claim_is 1 "1e-25 * pi == 0"
claim_is 0 "1.380649e-23 * 300 * log(2) > 0"

OUT="$(timeout 60 python3 "$CALC" eval "1.380649e-23 * 300 * log(2)" --digits 4 --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'simplify_unsafe' in d, 'lossy simplify was not flagged'
assert d['decimal'].startswith('2.871e-21'), d['decimal']
" 2>/dev/null; then
  ok "eval reports the true value and flags the lossy simplify"
else
  no "eval reports the true value and flags the lossy simplify" "$(printf '%s' "$OUT" | tail -4)"
fi

echo "== check: counterexamples are reported =="
OUT="$(timeout 60 python3 "$CALC" check "x**2 > 0" --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['verdict'] == 'FALSE', d['verdict']
assert d['counterexample']['at']['x'] == '0', d['counterexample']
" 2>/dev/null; then
  ok "counterexample for x**2 > 0 is exactly x=0"
else
  no "counterexample for x**2 > 0 is exactly x=0" "$(printf '%s' "$OUT" | tail -3)"
fi

echo "== eval =="
OUT="$(timeout 60 python3 "$CALC" eval "2**100 / 3" --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['exact'] == '1267650600228229401496703205376/3', d['exact']
assert d['verified'] is True
" 2>/dev/null; then
  ok "eval keeps large rationals exact"
else
  no "eval keeps large rationals exact" "$(printf '%s' "$OUT" | tail -3)"
fi

echo "== solve =="
# Regression: verifying CRootOf roots by substituting the symbolic root and
# evaluating afterwards took ~37s per root and blew any sane timeout.
START=$(date +%s)
OUT="$(timeout 90 python3 "$CALC" solve "x**5 - x - 1 = 0" --json 2>&1)"
ELAPSED=$(( $(date +%s) - START ))
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['verified'] is True, d
assert len(d['roots']) == 5, len(d['roots'])
assert 'rejected' not in d, d.get('rejected')
" 2>/dev/null; then
  ok "quintic: all 5 roots back-substitute (${ELAPSED}s)"
  if [ "$ELAPSED" -lt 45 ]; then
    ok "quintic completes well inside the timeout"
  else
    no "quintic completes well inside the timeout" "took ${ELAPSED}s"
  fi
else
  no "quintic: all 5 roots back-substitute" "$(printf '%s' "$OUT" | tail -3)"
fi

OUT="$(timeout 60 python3 "$CALC" solve "x**2 - 5*x + 6 = 0" --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
roots = sorted(r['root'] for r in d['roots'])
assert roots == ['2', '3'], roots
assert all(r['residual'] == '0 (exact)' for r in d['roots']), d['roots']
" 2>/dev/null; then
  ok "quadratic roots are exact with zero residual"
else
  no "quadratic roots are exact with zero residual" "$(printf '%s' "$OUT" | tail -3)"
fi

echo "== calculus =="
OUT="$(timeout 60 python3 "$CALC" deriv "x**3 * exp(x) * sin(x)" --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['verified'] is True, d
" 2>/dev/null; then
  ok "derivative agrees with numerical differentiation"
else
  no "derivative agrees with numerical differentiation" "$(printf '%s' "$OUT" | tail -3)"
fi

OUT="$(timeout 60 python3 "$CALC" integ "1/(1+x**2)" --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['verified'] is True, d
assert 'atan' in d['antiderivative'], d['antiderivative']
" 2>/dev/null; then
  ok "antiderivative differentiates back to the integrand"
else
  no "antiderivative differentiates back to the integrand" "$(printf '%s' "$OUT" | tail -3)"
fi

OUT="$(timeout 60 python3 "$CALC" integ "exp(-x**2)" --from=-oo --to=oo --json 2>&1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['verified'] is True, d
assert d['exact'] == 'sqrt(pi)', d['exact']
" 2>/dev/null; then
  ok "gaussian integral matches quadrature"
else
  no "gaussian integral matches quadrature" "$(printf '%s' "$OUT" | tail -3)"
fi

echo "== robustness =="
if timeout 30 python3 "$CALC" eval "this is not math ((" >/dev/null 2>&1; then
  no "unparseable input exits nonzero"
else
  ok "unparseable input exits nonzero"
fi

if timeout 30 python3 "$CALC" check "x + 1" >/dev/null 2>&1; then
  no "a claim with no relation is rejected"
else
  ok "a claim with no relation is rejected"
fi

# Shared options must work on either side of the subcommand.
if timeout 60 python3 "$CALC" eval "pi" --digits 50 >/dev/null 2>&1 \
   && timeout 60 python3 "$CALC" --digits 50 eval "pi" >/dev/null 2>&1; then
  ok "--digits accepted before and after the subcommand"
else
  no "--digits accepted before and after the subcommand"
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
