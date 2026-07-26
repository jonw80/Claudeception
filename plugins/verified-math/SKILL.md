---
name: verified-math
description: |
  Compute mathematics by executing it rather than doing it mentally, then
  verify the result a second, independent way. Use when: (1) any arithmetic
  beyond a trivial sum appears in the work, including percentages, unit
  conversions, byte and duration totals, growth rates, or capacity estimates;
  (2) solving equations, simplifying algebra, differentiating, integrating, or
  taking limits; (3) checking whether an identity, inequality, or bound
  actually holds; (4) big-O or complexity arithmetic, probability, statistics,
  combinatorics, or number theory; (5) linear algebra, eigenvalues, matrix
  decompositions, curve fitting, optimization, or numerical integration;
  (6) exact rational arithmetic, arbitrary-precision decimals, or reasoning
  about floating-point error; (7) reviewing or debugging code whose
  correctness depends on a formula being right.
when_to_use: |
  Also use when asked to check someone else's number, when a computed constant
  is about to be written into code or documentation, when a benchmark result
  needs a sanity check, or whenever an answer would otherwise be produced by
  mental arithmetic. Trigger phrases: calculate, compute, solve, simplify,
  derivative, integral, limit, prove, verify, how many, what percentage,
  is this formula right, check my math.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py *)
author: Claudeception
version: 1.0.0
date: 2026-07-26
---

# Verified Math

## Problem

Model arithmetic fails quietly. Not on the hard parts, where the difficulty is
visible and care gets applied, but on a carried digit, a dropped sign, a
percentage taken of the wrong base, a unit left unconverted. The answer comes
out plausible, and plausible survives review. A number that is confidently
wrong is worse than no number, because it gets used.

The fix is not to try harder. It is to stop producing numbers that only one
method has ever seen.

## The Rule

**Never state a computed number that was not produced by executing code.**

This covers more than it first appears. Percentages, ratios, totals of a
column, "that's about 40% faster", byte counts, timeouts in aggregate,
complexity arithmetic, how many requests fit in a window. If a digit is being
asserted, it gets computed.

Mental estimation is fine when it is *labelled* as an estimate and no digit is
claimed. "Roughly an order of magnitude larger" needs no tool. "1.7x larger"
does.

## The Tool

`scripts/calc.py` computes and then re-checks by a second method that shares
no code path with the first. Symbolic results are cross-checked against
high-precision numerics, roots are back-substituted into the original
equation, and antiderivatives are differentiated back.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py eval  "2**100 / 3"
python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py check "(x+1)**2 == x**2 + 2*x + 1"
python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py solve "x**2 - 5*x + 6 = 0"
python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py deriv "x**3 * exp(x)"
python3 ${CLAUDE_SKILL_DIR}/scripts/calc.py integ "exp(-x**2)" --from=-oo --to=oo
```

Useful flags: `--digits N` for precision (default 30), `--json` for parsing,
`--timeout N` to bound a hard symbolic computation (default 30s).

**Read the exit code.** `0` means the result was verified. `1` means
verification failed, the claim is false, or the two methods disagreed. A
`CONFLICT` verdict means symbolic and numeric analysis contradict each other:
report that, do not pick the answer you prefer.

For anything the CLI does not cover, write Python directly. SymPy, mpmath,
NumPy, and SciPy are the working set:

```python
import sympy as sp        # exact symbolic algebra and calculus
import mpmath as mp       # arbitrary-precision floats
import numpy as np        # arrays, linear algebra
import scipy as sp_       # optimization, integration, statistics, signal
```

If an import fails: `pip install sympy mpmath numpy scipy`.

Only the bundled `calc.py` is pre-approved in `allowed-tools`. Ad-hoc Python
still goes through normal permission checks, deliberately: a skill that
granted blanket `Bash(python3 *)` would hand every future turn unprompted
arbitrary code execution, which is far more access than doing arithmetic
requires.

## Choosing the Right Instrument

| Situation | Use |
| --- | --- |
| Money, counts, anything that must not drift | `sp.Rational` or `decimal.Decimal`, never float |
| Very large integers, factorials, combinatorics | Python `int` (arbitrary precision by default) |
| An answer wanted in closed form | `sympy` |
| Precision beyond float64 (more than ~15 digits) | `mpmath` with `mp.mp.dps` set |
| Matrices, eigenvalues, least squares | `numpy.linalg` |
| Optimization, quadrature, distributions, ODEs | `scipy` |
| Checking a claim rather than producing a value | `calc.py check` |

## Traps That Produce Confident Wrong Answers

- **Float equality.** `0.1 + 0.2 != 0.3`. Compare with a tolerance, or use
  exact types. This is the single most common source of a wrong "verified".
- **`sympify` on decimals.** `sympify("0.1")` is a `Float`, carrying float
  error into what looks like symbolic work. Write `Rational(1,10)`.
- **`simplify()` returning nonzero is not a disproof.** It means SymPy did not
  find a reduction. Use `.is_zero`, or sample numerically, before concluding
  two expressions differ.
- **`solve()` returning `[]` is not "no solutions."** It can mean no closed
  form exists. Fall back to `nroots` or `nsolve`.
- **Branch cuts.** `sqrt(x**2) == x` is false for negative `x`; `log(a*b) ==
  log(a) + log(b)` fails for complex arguments. Declare assumptions:
  `sp.Symbol('x', positive=True)`.
- **Sampling proves nothing.** Passing 40 random points is evidence, not proof.
  When `calc.py` reports `LIKELY TRUE (sampling only)`, say so rather than
  upgrading it to certain.
- **Precision theatre.** Asking for 200 digits from inputs known to 3 does not
  produce 200 good digits.

## Verification Discipline

A result is verified when a second method that could have disagreed did not.
Re-running the same computation is not verification. In descending order of
strength:

1. **Exact symbolic proof.** `.is_zero` is `True`, or `simplify(a - b) == 0`.
2. **Inverse operation.** Differentiate the integral, substitute the root,
   expand the factorization, multiply back the quotient.
3. **Independent numeric method.** Symbolic integration against quadrature;
   closed form against a high-precision sum.
4. **Sampling.** Random points, or exhaustive small cases. Finds
   counterexamples; never establishes truth.
5. **Dimensional and magnitude sanity.** Units balance, sign is right, the
   answer sits in the expected order of magnitude.

Hunt for a counterexample *before* asserting a universal claim. It is cheap,
and it is the check most likely to catch a real error.

## Reporting

State what was computed, what verified it, and what remains uncertain.

> `2**100 / 3` = 422550200076076467165567735125.33... (exact form
> 1267650600228229401496703205376/3; `calc.py eval` exit 0, evalf and mpmath
> agree to 30 digits)

When verification fails, say that plainly and do not present the unverified
number as the answer. "Symbolic and numeric disagree here, so I don't trust
either yet" is a complete and useful report.

## Notes

- Exit code, not prose, is the machine-readable verdict. Check it.
- `--timeout` exists because symbolic algebra has no general bound on runtime.
  A timeout is a real result: it means try a different form, not that the
  answer is unobtainable.
- Prior art worth knowing: [googlarz/math-skill](https://github.com/googlarz/math-skill)
  ships a broader claim-checking harness covering antiderivatives, systems,
  and interval counterexample search. It carries no license file, so it is
  referenced here rather than vendored.

## References

- [SymPy documentation](https://docs.sympy.org/)
- [mpmath documentation](https://mpmath.org/doc/current/)
- [NumPy](https://numpy.org/doc/stable/) and [SciPy](https://docs.scipy.org/doc/scipy/)
- [What Every Computer Scientist Should Know About Floating-Point Arithmetic](https://docs.oracle.com/cd/E19957-01/806-3568/ncg_goldberg.html)
