"""Validation for User Defined Function (UDF) text before it is compiled and evaluated.

pyeq3 compiles the UDF text and evaluates it with eval(). Its own checks are cosmetic,
so without this module a UDF such as  __import__('os').system('id')*X  runs arbitrary
code on the server. The validator re-parses the expression and accepts only arithmetic
over X (and Y for 3D), the coefficient names, and the numpy function and constant names
pyeq3 supplies. Attribute access ("a.b") and subscripting ("a[0]") are rejected outright,
which closes the ().__class__.__bases__... family of escapes.

Adapted from pyeq3/UdfSafety.py in https://github.com/equations-project/pyeq3
(contributed by the zunzun-ng fork, BSD licence), so the site is protected whichever
pyeq3 version is installed.

Always go through parse_and_compile_udf(); never call
equation.ParseAndCompileUserFunctionString() directly.
"""

import ast


class UnsafeUDFError(Exception):
    """A UDF expression contained something other than safe arithmetic."""


_allowedNodes = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv,
    ast.UAdd, ast.USub,
)


def validate_udf_expression(text, allowed_names):
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError as exc:
        raise UnsafeUDFError('could not parse the function (%s)' % exc) from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            raise UnsafeUDFError('attribute access (a "." after a name) is not allowed')
        if isinstance(node, ast.Subscript):
            raise UnsafeUDFError('subscripting is not allowed')
        if not isinstance(node, _allowedNodes):
            raise UnsafeUDFError('%s is not allowed' % type(node).__name__)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise UnsafeUDFError('only real numeric constants are allowed')
        if isinstance(node, ast.Name):
            if node.id.startswith('_'):
                raise UnsafeUDFError('the name %r is not allowed' % node.id)
            if node.id not in allowed_names:
                raise UnsafeUDFError('unknown name %r' % node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise UnsafeUDFError('only calls to the listed functions are allowed')
            if node.keywords:
                raise UnsafeUDFError('keyword arguments are not allowed')
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise UnsafeUDFError('starred arguments are not allowed')


def allowed_names(equation, dim):
    names = {'X'}
    if dim == 3:
        names.add('Y')
    names.update(equation._coefficientDesignators)
    for tokens in equation.functionDictionary.values():
        names.update(tokens)
    for tokens in equation.constantsDictionary.values():
        names.update(tokens)
    return names


def parse_and_compile_udf(equation, text):
    """Validate the UDF text, then let pyeq3 compile it into the equation.
    Raises UnsafeUDFError for anything but safe arithmetic, or pyeq3's own exception
    for text pyeq3 cannot use (no coefficients, '=' present, and so on)."""
    dim = equation.GetDimensionality()
    # the same cleaned text and coefficient list pyeq3 compiles from
    cleaned = equation.ProcessAndValidateFunctionString(text, dim)
    equation._coefficientDesignators = equation.GetSortedCoefficientsFromString(text, dim)
    validate_udf_expression(cleaned, allowed_names(equation, dim))
    equation.ParseAndCompileUserFunctionString(text, dim)


def test_evaluate_udf(equation, dim):
    """Evaluate the compiled UDF once with every value set to 1.0, without Python builtins,
    so syntax that only fails at run time is reported as a form error."""
    import numpy
    namespace = {'X': 1.0}
    if dim == 3:
        namespace['Y'] = 1.0
    for name in equation._coefficientDesignators:
        namespace[name] = 1.0
    for tokens in list(equation.functionDictionary.values()) + list(equation.constantsDictionary.values()):
        for token in tokens:
            namespace[token] = getattr(numpy, token)
    return eval(equation.userFunctionCodeObject, {'__builtins__': {}}, namespace)
