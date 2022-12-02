import typing

from expr import ParseExpr, match_op


def _is_in_expr(expr, net):
    if type(expr) is tuple:
        return any([_is_in_expr(x, net) for x in expr[1:]])
    elif expr == net:
        return True
    return False


def _replace_expr(expr, net, new_value):
    if type(expr) is tuple:
        n = [expr[0]]
        replaced = False
        for l in expr[1:]:
            new, changed = _replace_expr(l, net, new_value)
            n.append(new)
            replaced |= changed
        
        if replaced:
            return tuple(n), True
    elif expr == net:
        return new_value, True

    return expr, False


class ClockedExpr:
    def __init__(self, clock, ce, dest, value, reset_value):
        self.init = reset_value
        self.ce = ce
        self.clock = clock
        self.dest = dest
        self.value = value
        self.reset = "0"
        self.ce_reset = "0"
        self.reset_value = reset_value


class Module:
    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs

        self._registers = {}
        self.combinatorial = {}
        self.bundles = {}
        self.clocked = []  # typing.List[ClockedExpr]
    
    def add_assignment(self, target, expr):
        self.combinatorial[target] = ParseExpr(expr)
    
    def add_register(self, name, init):
        self._registers[name] = ParseExpr(init)

    def add_clocked(self, clock, ce, dest, value):
        init_value = self._registers[dest]
        self.clocked.append(ClockedExpr(clock, ce, dest, ParseExpr(value), init_value))

    def replace_net(self, net, new_value):
        replaced = False

        for comb, expr in self.combinatorial.items():
            new, changed = _replace_expr(expr, net, new_value)
            if changed:
                self.combinatorial[comb] = new
                replaced = True

        for clk in self.clocked:
            new, changed = _replace_expr(clk.ce, net, new_value)
            if changed:
                clk.ce = new
                replaced = True

            new, changed = _replace_expr(clk.value, net, new_value)
            if changed:
                clk.value = new
                replaced = True

        return replaced

    def find_ff(self, name):
        ff = list(filter(lambda x: x.dest == name, self.clocked))
        if ff:
            return ff[0]
        return None

    def find_dst_ff(self, name):

        ff = list(filter(lambda x: (x.value == name) or (match_op(x.value, "!") and (x.value[1] == name)), self.clocked))
        if len(ff) == 1:
            return ff[0]
        return None

    def find_uses(self, nets):
        result = set()

        for src, expr in self.combinatorial.items():
            for net in nets:
                if net not in ["0", "1"]:
                    if _is_in_expr(expr, net):
                        result.add(src)

        return list(result)

    def is_used(self, net):
        if net in self.outputs:
            return True

        for expr in self.combinatorial.values():
            if _is_in_expr(expr, net):
                return True
        
        for proc in self.clocked:
            if _is_in_expr(proc.ce, net):
                return True
            if _is_in_expr(proc.value, net):
                return True

        return False
