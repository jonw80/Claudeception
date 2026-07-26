#!/usr/bin/env python3
"""Compute exactly, then verify by a second, independent method.

An agent's arithmetic failures are rarely dramatic. They are an off-by-one in a
carried digit, a dropped sign, a float comparison that looked fine. Those
survive review because the answer is plausible. The defence is not care, it is
refusing to produce a number that only one method has ever seen.

Every subcommand here computes a result and then checks it a second way:
symbolic results are cross-checked against high-precision numerics, solved
roots are back-substituted into the original equation, and antiderivatives are
differentiated back. Where the two methods disagree, that is reported loudly
rather than resolved silently.

Usage:
    calc.py eval  "2**100 / 3"                  exact value and decimal expansion
    calc.py check "sin(x)**2 + cos(x)**2 == 1"  verify a claim two ways
    calc.py solve "x**2 - 5*x + 6 = 0"          roots, each back-substituted
    calc.py deriv "x**3 * exp(x)"               derivative, verified numerically
    calc.py integ "1/(1+x**2)"                  antiderivative, differentiated back
    calc.py integ "exp(-x**2)" --from=-oo --to=oo   definite integral

Add --digits N for precision (default 30), --json for machine-readable output.

Exit codes: 0 result verified, 1 verification failed or methods disagreed,
2 usage or parse error.
"""

import argparse
import json
import random
import signal
import sys

try:
    import sympy as sp
    import mpmath as mp
except ImportError:
    sys.stderr.write(
        "calc.py needs sympy and mpmath:\n"
        "    pip install sympy mpmath\n")
    raise SystemExit(2)


# Numeric agreement is judged in relative terms so the tolerance means the same
# thing for 1e-9 and 1e9. Values that straddle zero fall back to absolute.
def close(a, b, tol=1e-18):
    try:
        a, b = mp.mpf(a), mp.mpf(b)
    except (TypeError, ValueError):
        return False
    if mp.isnan(a) or mp.isnan(b):
        return False
    if mp.isinf(a) or mp.isinf(b):
        return a == b
    scale = max(abs(a), abs(b), mp.mpf(1))
    return abs(a - b) <= tol * scale


def _numerically_zero(expr, digits=60):
    """Is this expression actually zero, judged from the untransformed form?

    Returns True, False, or None when it cannot be decided. Used to audit
    simplify(), which is not value-preserving on small Floats.
    """
    if expr.free_symbols:
        return None
    try:
        value = sp.N(expr, digits)
        if value.free_symbols:
            return None
        return abs(complex(value)) < 10 ** (-(digits // 2))
    except (TypeError, ValueError, ArithmeticError):
        return None


def parse(expr, label="expression"):
    try:
        return sp.sympify(expr)
    except (sp.SympifyError, SyntaxError, TypeError) as exc:
        sys.stderr.write(f"could not parse {label} {expr!r}: {exc}\n")
        raise SystemExit(2)


def free_symbols(*exprs):
    names = set()
    for expr in exprs:
        names |= {str(s) for s in expr.free_symbols}
    return sorted(names)


def pick_var(expr, requested):
    """Resolve which symbol to work in, erroring only on real ambiguity."""
    names = free_symbols(expr)
    if requested:
        return sp.Symbol(requested)
    if not names:
        return sp.Symbol("x")
    if len(names) == 1:
        return sp.Symbol(names[0])
    if "x" in names:
        return sp.Symbol("x")
    sys.stderr.write(
        f"expression has several symbols ({', '.join(names)}); "
        "say which one with --var\n")
    raise SystemExit(2)


# --------------------------------------------------------------------------
# eval: exact value plus decimal expansion
# --------------------------------------------------------------------------

def cmd_eval(args):
    expr = parse(args.expression)
    out = {"input": args.expression}

    exact = sp.simplify(expr)

    # Audit simplify() before trusting its output as the value. It is not
    # value-preserving on small Floats -- it returns exactly 0 for
    # 1.380649e-23*300*log(2) -- and reporting that as the answer is precisely
    # the silent wrong number this tool exists to prevent. Numbers below are
    # therefore evaluated from the original expression, never the simplified one.
    if not expr.free_symbols and not exact.free_symbols:
        original_zero = _numerically_zero(expr)
        simplified_zero = _numerically_zero(exact)
        if original_zero is not None and original_zero != simplified_zero:
            out["simplify_unsafe"] = (
                f"simplify() returned {exact}, which does not match the value of "
                "the original expression; showing the original form instead")
            exact = expr

    out["exact"] = str(exact)

    if exact.free_symbols:
        out["note"] = "symbolic result; supply values to get a number"
        out["verified"] = True
        return 0, out

    mp.mp.dps = args.digits + 10
    try:
        # Two independent evaluators: SymPy's evalf and mpmath's own. Both read
        # the original expression, so neither inherits a simplify() artefact.
        primary = sp.N(expr, args.digits + 5)
        secondary = mp.mpmathify(sp.sstr(sp.N(expr, args.digits + 15)))
        out["decimal"] = sp.sstr(sp.N(expr, args.digits))
        agree = close(mp.mpf(str(primary)), secondary, mp.mpf(10) ** (-args.digits))
        out["verified"] = bool(agree)
        if not agree:
            out["error"] = "evalf and mpmath disagree; treat this number as unsafe"
    except (ValueError, TypeError, ArithmeticError) as exc:
        out["decimal"] = None
        out["verified"] = False
        out["error"] = f"numeric evaluation failed: {exc}"

    if exact.is_Integer:
        out["digits_in_result"] = len(str(abs(int(exact))))

    return (0 if out["verified"] else 1), out


# --------------------------------------------------------------------------
# check: verify a claim symbolically and by sampling
# --------------------------------------------------------------------------

RELATIONS = ["==", "!=", "<=", ">=", "<", ">", "="]


def split_claim(claim):
    # Longest operators first so "<=" is not read as "<".
    for op in ["==", "!=", "<=", ">=", "<", ">"]:
        if op in claim:
            left, right = claim.split(op, 1)
            return left.strip(), op, right.strip()
    if "=" in claim:
        left, right = claim.split("=", 1)
        return left.strip(), "==", right.strip()
    sys.stderr.write(
        f"claim {claim!r} has no relation; expected one of {', '.join(RELATIONS)}\n")
    raise SystemExit(2)


def symbolic_verdict(lhs, rhs, op):
    """Symbolic verdict, or None when symbolic analysis cannot settle it.

    Critically, a difference that fails to reduce to zero is NOT evidence the
    two sides differ: it usually means no reduction was found. Treating that
    as a disproof reports true identities as false, so inequality is only
    claimed when SymPy positively establishes it via is_zero. Everything else
    falls through to sampling, which can produce an actual counterexample.
    """
    if op in ("==", "!="):
        raw_diff = lhs - rhs
        diff = sp.simplify(raw_diff)
        equal = None

        if diff == 0 or diff.is_zero is True:
            # simplify() saying zero is a claim, not a measurement. On small
            # Floats it can collapse a genuinely nonzero value: simplify() of
            # 1.380649e-23*300*log(2) returns exactly 0, and every downstream
            # check then agrees with it because they all see the same corrupted
            # expression. Confirm against the untransformed difference.
            equal = _numerically_zero(raw_diff)
        else:
            # A second reduction route before giving up on proving equality.
            harder = sp.simplify(sp.expand(sp.trigsimp(diff)))
            if harder == 0 or harder.is_zero is True:
                equal = True
            elif harder.is_zero is False:
                equal = False  # SymPy is positively sure these differ
            elif not harder.free_symbols:
                # A constant that would not reduce. Decide it at a precision
                # far beyond the tolerance any caller would apply.
                try:
                    value = sp.N(harder, 60)
                    if not value.free_symbols:
                        equal = abs(complex(value)) < 1e-50
                except (TypeError, ValueError, ArithmeticError):
                    equal = None

        if equal is None:
            return None
        return equal if op == "==" else (not equal)
    relation = {"<": sp.StrictLessThan, ">": sp.StrictGreaterThan,
                "<=": sp.LessThan, ">=": sp.GreaterThan}[op](lhs, rhs)
    result = sp.simplify(relation)
    if result in (sp.true, sp.false):
        return bool(result)
    return None


def numeric_verdict(lhs, rhs, op, names, samples, digits):
    """Sample the claim at random points. Returns (verdict, counterexample)."""
    mp.mp.dps = digits
    rng = random.Random(20260726)
    checked = 0

    # Claims break at boundaries far more often than at random interior
    # points. Random rationals never land exactly on 0, 1, or -1, which is
    # precisely where something like x**2 > 0 fails. Test those first.
    special = [sp.Integer(0), sp.Integer(1), sp.Integer(-1), sp.Integer(2),
               sp.Integer(-2), sp.Rational(1, 2), sp.Rational(-1, 2)]
    trial_points = [{sp.Symbol(n): value for n in names} for value in special]

    for _ in range(samples * 4):
        trial_points.append(
            {sp.Symbol(n): sp.Rational(rng.randint(-9973, 9973), rng.randint(1, 997))
             for n in names})

    for point in trial_points:
        if checked >= samples + len(special):
            break
        try:
            lv = sp.N(lhs.subs(point), digits)
            rv = sp.N(rhs.subs(point), digits)
            if lv.free_symbols or rv.free_symbols:
                continue
            lf, rf = mp.mpf(str(lv)), mp.mpf(str(rv))
            if mp.isnan(lf) or mp.isnan(rf):
                continue
        except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
            continue

        checked += 1
        tol = mp.mpf(10) ** (-(digits // 2))
        if op == "==":
            ok = close(lf, rf, tol)
        elif op == "!=":
            ok = not close(lf, rf, tol)
        elif op == "<":
            ok = lf < rf
        elif op == ">":
            ok = lf > rf
        elif op == "<=":
            ok = lf <= rf or close(lf, rf, tol)
        else:
            ok = lf >= rf or close(lf, rf, tol)

        if not ok:
            witness = {str(k): str(v) for k, v in point.items()}
            return False, {"at": witness, "left": str(lv), "right": str(rv)}

    if checked == 0:
        return None, None
    return True, None


def cmd_check(args):
    left, op, right = split_claim(args.claim)
    lhs, rhs = parse(left, "left side"), parse(right, "right side")
    names = free_symbols(lhs, rhs)

    out = {"claim": args.claim, "relation": op}

    sym = symbolic_verdict(lhs, rhs, op)
    out["symbolic"] = {True: "true", False: "false", None: "inconclusive"}[sym]
    if op in ("==", "!="):
        out["difference"] = str(sp.simplify(lhs - rhs))

    if names:
        num, witness = numeric_verdict(lhs, rhs, op, names, args.samples, args.digits)
        out["numeric"] = {True: "holds on all samples", False: "counterexample found",
                          None: "no valid sample points"}[num]
        if witness:
            out["counterexample"] = witness
    else:
        # No free symbols: evaluate both sides directly.
        #
        # mp.mp.dps must be raised first. Left at its default of 15, mpmath
        # truncates both sides to 15 significant digits, and any difference
        # below that threshold silently disappears -- which reports a false
        # claim as holding for values of large magnitude.
        mp.mp.dps = args.digits + 10
        try:
            lv, rv = sp.N(lhs, args.digits), sp.N(rhs, args.digits)
            out["left_value"], out["right_value"] = str(lv), str(rv)
            if op == "==":
                num = close(mp.mpf(str(lv)), mp.mpf(str(rv)),
                            mp.mpf(10) ** (-(args.digits // 2)))
                out["difference"] = str(sp.N(lhs - rhs, args.digits))
            else:
                num = None
            out["numeric"] = "holds" if num else ("differs" if num is False else "n/a")
        except (TypeError, ValueError, ArithmeticError):
            num = None
            out["numeric"] = "could not evaluate"

    # A genuine alarm: symbolic proved an identity, yet a concrete point
    # violates it. One of the two is wrong and the caller must not guess which.
    if sym is True and num is False:
        out["verdict"] = "CONFLICT"
        out["error"] = ("symbolic says true but sampling found a counterexample; "
                        "do not trust either until this is resolved")
        return 1, out

    # Symbolic now only reports False when SymPy positively established it, so
    # it outranks a float comparison. Numeric agreement at some tolerance is
    # not evidence against exact inequality: exp(pi*sqrt(163)) matches an
    # integer to 30 significant figures and is still not that integer.
    if sym is False:
        out["verdict"] = "FALSE"
        if num is True:
            out["note"] = ("the two sides agree numerically at this tolerance "
                           "but are not exactly equal; raise --digits to see "
                           "the difference")
        return 1, out

    if sym is True:
        out["verdict"] = "TRUE"
        return 0, out

    if num is True:
        out["verdict"] = "LIKELY TRUE (sampling only, not proved)"
        return 0, out
    if num is False:
        out["verdict"] = "FALSE"
        return 1, out

    out["verdict"] = "INCONCLUSIVE"
    return 1, out


# --------------------------------------------------------------------------
# solve: roots, each verified by substitution
# --------------------------------------------------------------------------

def cmd_solve(args):
    text = args.equation
    if "=" in text and "==" not in text:
        left, right = text.split("=", 1)
        equation = sp.Eq(parse(left, "left side"), parse(right, "right side"))
    else:
        equation = sp.Eq(parse(text.replace("==", "-(") + ")" if "==" in text else text), 0)

    var = pick_var(equation.lhs - equation.rhs, args.var)
    out = {"equation": str(equation), "variable": str(var)}

    try:
        roots = sp.solve(equation, var, dict=False)
    except (NotImplementedError, sp.SympifyError) as exc:
        out["error"] = f"symbolic solve failed: {exc}"
        roots = []

    if not isinstance(roots, list):
        roots = [roots]

    # Back-substitution is the whole point: a root that does not satisfy the
    # original equation is a bug, whatever the solver claimed.
    #
    # Deliberately no simplify() here. On roots that have no radical form
    # SymPy returns CRootOf objects, and simplify() on those is both slow and
    # worse at the job: it leaves the residual unreduced while is_zero answers
    # correctly in a fraction of the time.
    verified, rejected = [], []
    residual_expr = equation.lhs - equation.rhs
    precision = min(args.digits, 50)

    for root in roots:
        entry = {"root": str(root)}
        try:
            entry["decimal"] = str(sp.N(root, precision))
        except (TypeError, ValueError, ArithmeticError):
            entry["decimal"] = None

        ok = None
        try:
            residual = residual_expr.subs(var, root)
            ok = residual.is_zero  # True, False, or None when undecided
        except (TypeError, ValueError, ArithmeticError) as exc:
            entry["residual"] = f"could not substitute: {exc}"
            rejected.append(entry)
            continue

        if ok is True:
            entry["residual"] = "0 (exact)"
        else:
            # Undecided or reportedly nonzero: settle it numerically.
            #
            # Evaluate the root to a number and substitute that, rather than
            # substituting the symbolic root and evaluating the result. For a
            # CRootOf the second order costs ~37 seconds per root and the
            # first costs ~1, for the same answer.
            try:
                root_value = sp.N(root, precision)
                numeric = sp.N(residual_expr.subs(var, root_value), precision)
                entry["residual"] = str(numeric)
                if numeric.free_symbols:
                    ok = False
                else:
                    ok = abs(complex(numeric)) < 10 ** (-(precision // 2))
            except (TypeError, ValueError, ArithmeticError) as exc:
                entry["residual"] = f"could not evaluate: {exc}"
                ok = False

        (verified if ok else rejected).append(entry)

    out["roots"] = verified
    if rejected:
        out["rejected"] = rejected
        out["error"] = "some roots failed back-substitution"

    if not roots:
        # No closed form is not the same as no roots. Try numerically before
        # reporting nothing.
        try:
            poly = sp.Poly(residual_expr, var)
            numeric_roots = [str(r) for r in sp.nroots(poly)]
            out["numeric_roots"] = numeric_roots
            out["note"] = "no closed form; roots found numerically"
            return 0, out
        except (sp.PolynomialError, sp.GeneratorsNeeded, ValueError):
            out.setdefault("error", "no solutions found")
            return 1, out

    out["verified"] = not rejected
    return (0 if not rejected else 1), out


# --------------------------------------------------------------------------
# deriv / integ: calculus with the inverse operation as the check
# --------------------------------------------------------------------------

def cmd_deriv(args):
    expr = parse(args.expression)
    var = pick_var(expr, args.var)
    result = sp.diff(expr, var, args.order)

    out = {"input": args.expression, "variable": str(var), "order": args.order,
           "derivative": str(sp.simplify(result))}

    # Check the symbolic derivative against a central difference quotient.
    if args.order == 1:
        mp.mp.dps = 40
        try:
            f = sp.lambdify(var, expr, "mpmath")
            g = sp.lambdify(var, result, "mpmath")
            agreed, tested = 0, 0
            for point in (mp.mpf("0.37"), mp.mpf("1.61"), mp.mpf("-0.83")):
                try:
                    numeric = mp.diff(f, point)
                    symbolic = g(point)
                    tested += 1
                    if close(numeric, symbolic, mp.mpf("1e-20")):
                        agreed += 1
                except (ValueError, TypeError, ZeroDivisionError, ArithmeticError):
                    continue
            out["numeric_check"] = f"{agreed}/{tested} sample points agree"
            out["verified"] = tested > 0 and agreed == tested
            if tested and agreed < tested:
                out["error"] = "symbolic derivative disagrees with numerical differentiation"
        except (TypeError, ValueError, NotImplementedError):
            out["numeric_check"] = "not numerically checkable"
            out["verified"] = True
    else:
        out["verified"] = True

    return (0 if out.get("verified", True) else 1), out


def cmd_integ(args):
    expr = parse(args.expression)
    var = pick_var(expr, args.var)
    out = {"input": args.expression, "variable": str(var)}

    if args.frm is not None or args.to is not None:
        if args.frm is None or args.to is None:
            sys.stderr.write("a definite integral needs both --from and --to\n")
            raise SystemExit(2)
        lower, upper = parse(args.frm, "--from"), parse(args.to, "--to")
        out["bounds"] = [str(lower), str(upper)]

        exact = sp.integrate(expr, (var, lower, upper))
        out["exact"] = str(sp.simplify(exact))
        try:
            out["decimal"] = str(sp.N(exact, args.digits))
        except (TypeError, ValueError):
            out["decimal"] = None

        # Independent check: quadrature, which shares no code path with the
        # symbolic integrator.
        mp.mp.dps = 30
        try:
            f = sp.lambdify(var, expr, "mpmath")
            quad = mp.quad(f, [mp.mpmathify(str(sp.N(lower, 30))) if lower.is_finite else -mp.inf,
                               mp.mpmathify(str(sp.N(upper, 30))) if upper.is_finite else mp.inf])
            out["quadrature"] = str(quad)
            symbolic_value = sp.N(exact, 30)
            agree = (not symbolic_value.free_symbols
                     and close(mp.mpf(str(symbolic_value)), quad, mp.mpf("1e-15")))
            out["verified"] = bool(agree)
            if not agree:
                out["error"] = "symbolic integral and quadrature disagree"
        except (TypeError, ValueError, NotImplementedError, ArithmeticError) as exc:
            out["quadrature"] = f"not computable: {exc}"
            out["verified"] = True
        return (0 if out.get("verified", True) else 1), out

    antiderivative = sp.integrate(expr, var)
    out["antiderivative"] = str(antiderivative)

    # Differentiating the answer must return the integrand.
    back = sp.simplify(sp.diff(antiderivative, var) - expr)
    out["derivative_of_result_minus_input"] = str(back)
    out["verified"] = back == 0
    if not out["verified"]:
        out["error"] = "differentiating the antiderivative did not return the integrand"
    return (0 if out["verified"] else 1), out


# --------------------------------------------------------------------------

def render(payload, as_json):
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    width = max(len(k) for k in payload)
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key:<{width}} : {json.dumps(value)}")
        else:
            print(f"{key:<{width}} : {value}")


def main():
    # Shared options live on a parent parser so they are accepted both before
    # and after the subcommand. Requiring "calc.py --digits 50 eval ..." and
    # rejecting "calc.py eval ... --digits 50" is a trap worth closing.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--digits", type=int, default=30,
                        help="working precision in decimal digits (default 30)")
    common.add_argument("--timeout", type=int, default=30,
                        help="seconds before giving up (default 30, 0 disables)")

    parser = argparse.ArgumentParser(
        description="Compute exactly, then verify by an independent method.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("eval", parents=[common],
                       help="exact value and decimal expansion")
    p.add_argument("expression")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("check", parents=[common],
                       help="verify a claim symbolically and by sampling")
    p.add_argument("claim", help='for example "sin(x)**2 + cos(x)**2 == 1"')
    p.add_argument("--samples", type=int, default=40,
                   help="random points to test (default 40)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("solve", parents=[common],
                       help="solve an equation, back-substituting each root")
    p.add_argument("equation")
    p.add_argument("--var", help="variable to solve for")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("deriv", parents=[common],
                       help="differentiate, checked numerically")
    p.add_argument("expression")
    p.add_argument("--var")
    p.add_argument("--order", type=int, default=1)
    p.set_defaults(func=cmd_deriv)

    p = sub.add_parser("integ", parents=[common],
                       help="integrate, checked by the inverse operation")
    p.add_argument("expression")
    p.add_argument("--var")
    p.add_argument("--from", dest="frm", help="lower bound for a definite integral")
    p.add_argument("--to", dest="to", help="upper bound for a definite integral")
    p.set_defaults(func=cmd_integ)

    args = parser.parse_args()
    if args.digits < 1 or args.digits > 5000:
        sys.stderr.write("--digits must be between 1 and 5000\n")
        return 2

    # Symbolic algebra has no general bound on how long a simplification runs,
    # and an agent waiting forever on a subprocess is worse than a clear
    # failure. Cap it and say so.
    if args.timeout > 0 and hasattr(signal, "SIGALRM"):
        def give_up(_signum, _frame):
            raise TimeoutError(
                f"exceeded --timeout of {args.timeout}s; "
                "try a simpler form, fewer --digits, or raise --timeout")
        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(args.timeout)

    try:
        code, payload = args.func(args)
    except TimeoutError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 1
    finally:
        if args.timeout > 0 and hasattr(signal, "SIGALRM"):
            signal.alarm(0)

    render(payload, args.json)
    return code


if __name__ == "__main__":
    sys.exit(main())
