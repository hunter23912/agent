"""Bounded arithmetic tool: no eval, imports, names or function execution."""
import ast
import operator
import math
OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow}
def calculate(expression):
    if not isinstance(expression,str) or len(expression)>180: raise ValueError('表达式过长')
    try: tree=ast.parse(expression,mode='eval')
    except SyntaxError: raise ValueError('算式格式错误') from None
    if len(list(ast.walk(tree)))>60: raise ValueError('表达式过于复杂')
    def walk(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float): value=node.value
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=walk(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
        elif isinstance(node,ast.BinOp) and type(node.op) in OPS:
            left,right=walk(node.left),walk(node.right)
            if isinstance(node.op,ast.Pow) and abs(right)>12: raise ValueError('指数绝对值不能超过 12')
            value=OPS[type(node.op)](left,right)
        else: raise ValueError('仅支持数字、括号与 + - * / **')
        if not isinstance(value,(float,int)) or not math.isfinite(value) or abs(value)>1e15: raise ValueError('结果超出范围')
        return value
    try: return {'expression':expression,'value':walk(tree.body)}
    except (ZeroDivisionError,OverflowError): raise ValueError('除零或数值溢出') from None
