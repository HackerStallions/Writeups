#!/usr/bin/env python3
from pyparsing import Forward, Group, Optional, ParseResults, Word, alphanums, alphas, nums
import re
import typing

def Parse2(node: typing.Union[str, ParseResults], n: str="top"):
    if n == "top":
        return Parse2(node[0], "expr")
    if n == "expr":
        l = Parse2(node[0], "term")
        if len(node) > 1:
            t = Parse2(node[2], "expr")
            f = Parse2(node[4], "expr")

            return ("?", l, t, f)
        return l
    elif n == "term":
        l = Parse2(node[0], "term1")
        if len(node) > 1:
            t = Parse2(node[2], "expr")
            return ("|", l, t)
        return l
    elif n == "term1":
        l = Parse2(node[0], "term2")
        if len(node) > 1:
            t = Parse2(node[2], "expr")
            return ("^", l, t)
        return l
    elif n == "term2":
        l = Parse2(node[0], "factor")
        if len(node) > 1:
            t = Parse2(node[2], "expr")
            return ("&", l, t)
        return l
    elif n == "factor":
        d = node.asDict()

        if "num" in d:
            return node["num"]
        elif "ident" in d:
            return node["ident"]
        elif "not" in d:
            return ("!", Parse2(node["not"]["factor"], "factor"))
        elif "sub" in d:
            return Parse2(node["sub"]["subexpr"], "expr")

    raise Exception("f")


def escape(v: str):
    if re.search(r"[!|&\^]", v):
        return "(" + v + ")"
    return v


def is_const(n):
    return type(n) is str


def match_op(n, op):
    return (type(n) is tuple) and (n[0] == op)


def without(n, to_remove):
    return tuple(filter(lambda x: x != to_remove, n))


def in_op(n, match):
    return any(filter(lambda x: x == match, n[1:]))


def has_const(n, value):
    return any(map(lambda x: x == value, n[1:]))


def count_const(n, value):
    return sum(map(lambda x: 1 if x == value else 0, n[1:]))


def without(n, to_remove):
    return tuple(filter(lambda x: x != to_remove, n))


def break_shared_and(orig, n1, n2):
    if match_op(n1, "&") and match_op(n2, "&"):
        t1 = set(filter(lambda x: type(x) is str, n1[1:]))
        t2 = set(filter(lambda x: type(x) is str, n2[1:]))

        t = t1.intersection(t2)

        if t:
            n = t.pop()
            return optimize(("&", n, ("?", orig[1], without(n1, n), without(n2, n))))

    return orig


def optimize(l):
    if len(l) == 1:
        if l[0] == "&":
            return "1"
        if l[0] == "|":
            return "0"
        if l[0] == "^":
            raise Exception("Don't")

    if type(l) is tuple:
        l = (l[0],) + tuple(map(optimize, l[1:]))

    if l[0] == "?":
        if l[3] == "0":
            return optimize(("&", l[1], l[2])) # x?y:0 -> x&y
        if l[2] == "0":
            return optimize(("&", ("!", l[1]), l[3])) # x?0:y -> !x&y

        if l[3] == "1":
            return optimize(("|", ("!", l[1]), l[2])) # x?y:1 -> !x|y
        if l[2] == "1":
            return optimize(("|", l[1], l[3])) # x?1:y -> x|y

        if match_op(l[3], "!") and (l[2] == l[3][1]):
            return optimize(("!", ("^", l[1], l[2]))) # x?y:!y -> !x^y
        if match_op(l[2], "!") and (l[3] == l[2][1]):
            return optimize(("^", l[1], l[3])) # x?!y:y -> x^y

        if match_op(l[2], "!") and match_op(l[3], "!"):
            return optimize(("!", ("?", l[1], l[2][1], l[3][1]))) # x?!y:!z -> !(x?y:z)

        if is_const(l[2]) and match_op(l[3], "&") and in_op(l[3], l[2]):
            return optimize(("&", l[2], ("?", l[1], "1", without(l[3], l[2])))) # x?y:(y&z) -> y&(x?1:z)
        if is_const(l[3]) and match_op(l[2], "&") and in_op(l[2], l[3]):
            return optimize(("&", l[3], ("?", l[1], without(l[2], l[3]), "1"))) # x?(y&z):y -> y&(x?z:1)

        return break_shared_and(l, l[2], l[3]) # Generalization of above
        
    if l[0] == "!":
        if match_op(l[1], "!"):
            return optimize(l[1][1])
        if l[1] == "1":
            return "0"
        if l[1] == "0":
            return "1"
        if match_op(l[1], "|"):
            # De Morgan, prefer and
            return ("&",) + tuple(map(lambda x: ("!", x), l[1][1:]))

    if l[0] == "carry":
        if l[1] in ["0", "1"]:
            return ("carry", l[2], l[1], l[3])


    if l[0] == "^":
        if len(l) == 2:
            return l[1]
        got = False
        flip = False
        new = list(l)
        for i, n in enumerate(l[1:]):
            if match_op(n, "!"):
                got = True
                flip = not flip
                new[i+1] = n[1]
        if flip:
            return optimize(("!", tuple(new)))
        elif got:
            return optimize(tuple(new))
        
        cnt = count_const(l, "1")
        if cnt > 0:
            if (cnt % 2) != 0:
                return optimize(("!", without(l, "1")))
            else:
                return optimize(without(l, "1"))

        if match_op(l[-1], "^"):
            return optimize(("^",) + tuple(l[-1][1:]) + l[1:-1])
        
    if l[0] == "&":
        if has_const(l, "0"):
            return "0"
        if has_const(l, "1"):
            return optimize(without(l, "1"))

        if len(l) == 2:
            return l[1]
        if match_op(l[-1], "&"):
            return optimize(("&",) + tuple(l[-1][1:]) + l[1:-1])
        
        if all(map(lambda x: x[0] == "!", l[1:])):
            return optimize(("!", ("|",) + tuple(map(lambda x: x[1], l[1:]))))

    if l[0] == "|":
        if len(l) == 2:
            return l[1]
        if match_op(l[-1], "|"):
            return optimize(("|",) + tuple(l[-1][1:]) + l[1:-1])
        if all(map(lambda x: x[0] == "!", l[1:])):
            return optimize(("!", ("&",) + tuple(map(lambda x: x[1], l[1:]))))

    return l


def assemble(l):
    if type(l) is tuple:
        if len(l) == 2:
            return l[0] + escape(assemble(l[1]))
        if l[0] in ["&", "|", "^"]:
            return l[0].join(map(lambda x: escape(assemble(x)), l[1:]))
        if l[0] == "carry":
            return "carry(%s)" % ", ".join(map(lambda x: escape(assemble(x)), l[1:]))
        if l[0] == "fa":
            return "fa(%s)" % ", ".join(map(lambda x: escape(assemble(x)), l[1:]))
        if len(l) == 4:
            return "%s ? %s : %s" % (escape(assemble(l[1])), escape(assemble(l[2])), escape(assemble(l[3])))
    else:
        return l


GRAMMAR = None
def get_grammar():
    global GRAMMAR
    if not GRAMMAR:
        ident = Word(alphas, alphanums + "_")("ident")
        num = Word(nums)("num")

        value = (ident | num)
        factor = Forward()
        expr = Forward()
        factor << Group(value("value") | Group("!" + factor("factor"))("not") | Group("(" + expr("subexpr") + ")")("sub"))

        term2 = Group(factor + Optional("&" + expr))
        term1 = Group(term2 + Optional("^"  + expr))
        term  = Group(term1 + Optional("|"  + expr))

        expr << Group(term + Optional("?" + expr + ":" + expr))
        GRAMMAR = expr
    
    return GRAMMAR


def ParseExpr(x):
    if type(x) is tuple:
        return x

    expr = get_grammar()

    y = expr.parseString(x, True)
    return Parse2(y)
