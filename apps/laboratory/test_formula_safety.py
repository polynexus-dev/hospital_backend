"""Locks in the AST allow-list of the calculated-test formula evaluator
(apps.laboratory.lab_extras). The formula text is hospital-configurable, so
`eval` here must only ever run numeric arithmetic over {CODE} inputs and the
named safe functions — never attribute access, subscripting, arbitrary calls,
comprehensions, or string/dunder tricks."""
import pytest

from apps.laboratory.lab_extras import FormulaError, evaluate, validate_formula

DANGEROUS = [
    "__import__('os').system('id')",     # arbitrary call / unknown name
    "{A}.__class__",                     # attribute access
    "().__class__.__bases__",            # attribute chain
    "{A}[0]",                            # subscript
    "open('x')",                         # unknown callable
    "'abc'",                             # string constant
    "[x for x in (1,2)]",                # comprehension
    "lambda: 1",                         # lambda
    "{A} if {A} else 0",                 # conditional expression (not allow-listed)
    "globals()",                         # unknown callable
]


@pytest.mark.parametrize("formula", DANGEROUS)
def test_dangerous_formula_is_rejected(formula):
    with pytest.raises(FormulaError):
        validate_formula(formula)


def test_legitimate_formula_validates_and_evaluates():
    formula = "round(({WT} / (({HT}/100) * ({HT}/100))), 1)"  # BMI
    validate_formula(formula)  # does not raise
    result = evaluate(formula, {"WT": 70, "HT": 170})
    assert result == pytest.approx(24.2, abs=0.05)


def test_safe_functions_are_available():
    assert evaluate("max({A}, {B})", {"A": 3, "B": 9}) == 9.0
    assert evaluate("sqrt({A})", {"A": 16}) == 4.0


def test_missing_input_returns_none_not_error():
    assert evaluate("{A} + {B}", {"A": 1}) is None  # B absent
