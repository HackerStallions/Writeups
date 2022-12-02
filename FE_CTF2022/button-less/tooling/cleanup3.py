#!/usr/bin/env python3
import numpy as np
import json
import tempfile
import os
import re
import typing
from pyverilog.ast_code_generator.codegen import ASTCodeGenerator
from pyverilog.vparser import parser

from expr import assemble, in_op, match_op, optimize, without
from module import Module, ClockedExpr


operator_mark = {
    'Uminus': '-', 'Ulnot': '!', 'Unot': '~', 'Uand': '&', 'Unand': '~&',
    'Uor': '|', 'Unor': '~|', 'Uxor': '^', 'Uxnor': '~^',
    'Power': '**', 'Times': '*', 'Divide': '/', 'Mod': '%',
    'Plus': '+', 'Minus': '-',
    'Sll': '<<', 'Srl': '>>', 'Sla': '<<<', 'Sra': '>>>',
    'LessThan': '<', 'GreaterThan': '>', 'LessEq': '<=', 'GreaterEq': '>=',
    'Eq': '==', 'NotEq': '!=', 'Eql': '===', 'NotEql': '!==',
    'And': '&', 'Xor': '^', 'Xnor': '~^',
    'Or': '|', 'Land': '&&', 'Lor': '||'
}


silent = set(["Source", "Description", "ModuleDef", "Paramlist", "Portlist", "Ioport", "Decl", "Pragma", "IfStatement", "NonblockingSubstitution", "Always", "SensList", "Sens", "IfStatement"])

unary_ops = set(["Uplus","Uminus","Ulnot","Unot","Uand","Unand","Uor","Unor","Uxor","Uxnor"])
binary_ops = set(["Power","Times","Divide","Mod","Plus","Minus","Sll","Srl","Sra","LessThan","GreaterThan","LessEq","GreaterEq","Eq","NotEq","Eql","NotEql","And","Xor","Xnor","Or","Land","Lor"])

def op2mark(op):
    if op not in operator_mark:
        return None
    return operator_mark[op]


class Cleaner:
    def __init__(self, lines: typing.List[str]):
        tmp = tempfile.NamedTemporaryFile("w")
        for l in lines:
            l = re.sub(r"^(.*)/\*\s*CARRY.+\*/", r"(* CARRY *)\1", l)
            tmp.write(l)
        tmp.flush()

        self.mod = Module([], [])
        nets = []
        pragma = None

        def visitUnary(n):
            op = op2mark(n.__class__.__name__)
            return (op, visit(n.right))
        
        def visitBinary(n):
            op = op2mark(n.__class__.__name__)
            return (op, visit(n.left), visit(n.right))
        
        def visitCarry(ast):
            a = visit(ast.left.left)
            b = visit(ast.left.right)
            c = visit(ast.right.right)
            return ("carry", a,b,c)

        def visit(n):
            nonlocal pragma
            name = n.__class__.__name__

            if name == "Input":
                self.mod.inputs.append(n.name)
                return
            if name == "Output":
                self.mod.outputs.append(n.name)
                return
            if name == "Wire":
                nets.append(n.name)
                assert len(n.children()) == 0
                return
            if name == "Reg":
                self.mod.add_register(n.name, "x")
                return
            if name == "Assign":
                dst = visit(n.left)
                if pragma == "CARRY":
                    src = visitCarry(n.right.var)
                else:
                    src = visit(n.right)
                if dst in nets:
                    self.mod.add_assignment(dst, src)
                else:
                    self.mod._registers[dst] = src
                pragma = None
                return
            if name == "Lvalue":
                return visit(n.var)
            if name == "Rvalue":
                return visit(n.var)
            if name == "Identifier":
                return n.name
            if name == "IntConst":
                return str(n.value).replace("1'b", "")
            if name in unary_ops:
                return visitUnary(n)
            if name in binary_ops:
                return visitBinary(n)
            if name == "Cond":
                return ("?", visit(n.cond), visit(n.true_value), visit(n.false_value))
            if name == "PragmaEntry":
                pragma = n.name
                return
            if name == "Always":
                edge = n.sens_list.list[0].type
                clk = visit(n.sens_list.list[0].sig)

                assert edge == "posedge"

                action = n.statement

                aname = action.__class__.__name__

                if aname == "IfStatement":
                    asgn = action.true_statement
                    asgn_name = asgn.__class__.__name__

                    assert action.false_statement == None
                    assert asgn_name == "NonblockingSubstitution"
                    
                    dst = visit(asgn.left)
                    src = visit(asgn.right)
                    self.mod.add_clocked(clk, visit(action.cond), dst, src)
                else:
                    raise NotImplementedError(aname)
            
            if name not in silent:
                print("%s [%s]" % (name, ", ".join(n.attr_names)))
            for c in n.children():
                visit(c)

        ast, dir = parser.parse([tmp.name], debug=False)
        gen = ASTCodeGenerator()
        visit(ast)

        self.rules = json.loads(open("cleanup_renames.json", "r").read())

    def format(self):
        f = []
        f.append("module top(%s);" % ", ".join([", ".join(["input " + x for x in self.mod.inputs]), ", ".join(["output " + x for x in self.mod.outputs])]))
        f.append("function carry(input a, input b, input c); carry = (a&b) | ((a|b) & c); endfunction")
        f.append("function fa(input a, input b, input c); fa = a^b^c; endfunction")

        for comb in self.mod.combinatorial:
            f.append("wire %s;" % comb)

        for proc in self.mod.clocked:
            f.append("reg %s = %s;" % (proc.dest, proc.init))

        for name, nets in self.mod.bundles.items():
            f.append("wire [%d-1:0] %s = {%s};" % (len(nets), name, ",".join(nets)))

        for net, expr in self.mod.combinatorial.items():
            f.append("assign %s = %s;" % (net, assemble(expr)))

        clks = {}
        for proc in self.mod.clocked:
            if proc.clock not in clks:
                clks[proc.clock] = {
                    "no_ce": [],
                    "ce": {},
                    "rst": {},
                    "ce_rst": {},
                }
            
            ce = assemble(proc.ce)

            if ce == "1":
                clks[proc.clock]["no_ce"].append("    %s <= %s;" % (proc.dest, assemble(proc.value)))
            else:
                if ce not in clks[proc.clock]["ce"]:
                    clks[proc.clock]["ce"][ce] = []

                clks[proc.clock]["ce"][ce].append("        %s <= %s;" % (proc.dest, assemble(proc.value)))
            
            if proc.reset != "0":
                if proc.reset not in clks[proc.clock]["rst"]:
                    clks[proc.clock]["rst"][proc.reset] = []
                clks[proc.clock]["rst"][proc.reset].append("        %s <= %s;" % (proc.dest, assemble(proc.reset_value)))

            if proc.ce_reset != "0":
                if (ce, proc.ce_reset) not in clks[proc.clock]["ce_rst"]:
                    clks[proc.clock]["ce_rst"][(ce, proc.ce_reset)] = []

                clks[proc.clock]["ce_rst"][(ce, proc.ce_reset)].append("        %s <= %s;" % (proc.dest, assemble(proc.reset_value)))

        for name, clk in clks.items():
            f.append("always @(posedge %s)" % name)
            f.append("begin")

            for nce in clk["no_ce"]:
                f.append(nce)

            for cond, nce in clk["ce"].items():
                f.append("    if (%s)" % cond)
                f.append("    begin")
                for exp in nce:
                    f.append(exp)
                f.append("    end")

            for cond, rste in clk["rst"].items():
                f.append("    if (%s)" % cond)
                f.append("    begin")
                for exp in rste:
                    f.append(exp)
                f.append("    end")

            for conds, rste in clk["ce_rst"].items():
                f.append("    if (%s & %s)" % (conds[0], conds[1]))
                f.append("    begin")
                for exp in rste:
                    f.append(exp)
                f.append("    end")

            f.append("end")

        f.append("endmodule")
        return "\n".join(f)

    def _pass_wire_forward(self):
        def is_simple(w):
            if type(w) is str:
                return True
            if len(w)==2 and is_simple(w[1]):
                return True
            return False

        replaced = False
        for net, expr in self.mod.combinatorial.items():
            if is_simple(expr):
                replaced |= self.mod.replace_net(net, expr)

        return replaced

    def _pass_optimize(self):
        for net, expr in self.mod.combinatorial.items():
            self.mod.combinatorial[net] = optimize(expr)

        for proc in self.mod.clocked:
            proc.ce = optimize(proc.ce)
            proc.value = optimize(proc.value)

    def _pass_unused(self):
        removed = 0
        for net in list(self.mod.combinatorial):
            if not self.mod.is_used(net):
                del self.mod.combinatorial[net]

        return removed

    def _pass_rename(self):
        for prev, new in self.rules["rename"].items():
            self.mod.replace_net(prev, new)
            
            if prev in self.mod.inputs:
                self.mod.inputs[self.mod.inputs.index(prev)] = new
            if prev in self.mod.outputs:
                self.mod.outputs[self.mod.outputs.index(prev)] = new

            if prev in self.mod.combinatorial:
                val = self.mod.combinatorial[prev]
                del self.mod.combinatorial[prev]
                self.mod.combinatorial[new] = val
            
            for proc in self.mod.clocked:
                if proc.clock == prev:
                    proc.clock = new
                if proc.dest == prev:
                    proc.dest = new
                    break

    def _pass_ff_reset_propagate(self):
        for rst, pol in self.rules["resets"].items():
            if pol != "0":
                raise Exception("Low polarity reset supported")

            for comb, expr in self.mod.combinatorial.items():
                if match_op(expr, "&") and in_op(expr, rst):
                    self.mod.combinatorial[comb] = optimize(without(expr, rst))
                    self.mod.replace_net(comb, ("&", comb, rst))

    def _pass_ff_promote_resets(self):
        for rst, pol in self.rules["resets"].items():
            if pol != "0":
                raise Exception("Low polarity reset supported")

            for proc in self.mod.clocked:
                if proc.reset != "0" or proc.ce_reset != "0":
                    break
            
                ce = proc.ce != "1"
                
                if match_op(proc.value, "&") and in_op(proc.value, rst):
                    proc.value = optimize(without(proc.value, rst))
                    if ce:
                        proc.ce_reset = assemble(("!", rst))
                    else:
                        proc.reset = assemble(("!", rst))

    def _invert_ff(self, name):
        proc = self.mod.find_ff(name)
        if not proc:
            return

        proc.init = "1" if proc.init == "0" else "0"
        proc.reset_value = "1" if proc.reset_value == "0" else "0"

        self.mod.replace_net(name, ("!", name))
        proc.value = optimize(("!", proc.value))

    def _pass_carry_full_adder(self):
        def is_sum0(netname, sources):
            expr = self.mod.combinatorial[netname]
            if match_op(expr, "^") and (len(expr) == len(sources)+1) and all(filter(lambda src: in_op(expr, src), sources)):
                return True
            return False

        def is_sum1(netname, sources):
            expr = self.mod.combinatorial[netname]
            if match_op(expr, "!"):
                expr = expr[1]
                if match_op(expr, "^") and (len(expr) == len(sources)+1) and all(filter(lambda src: in_op(expr, src), sources)):
                    return True
            return False

        def get_target(expr):
            if type(expr) == tuple:
                if expr[0] == "!":
                    return expr[1]
                return None
            if expr in ["0", "1"]:
                return None
            return expr

        for net, comb in list(self.mod.combinatorial.items()):
            if match_op(comb, "carry"):
                sources = list(filter(lambda x: x is not None, map(get_target, comb[1:])))
                neg = in_op(comb, "1")
                inv = match_op(comb[1], "!")
                uses = self.mod.find_uses(sources)

                for usage in uses:
                    if inv:
                        if not neg:
                            if is_sum1(usage, sources):
                                self.mod.combinatorial[usage] = ("fa",) + tuple(comb[1:])
                                break
                        else:
                            if is_sum0(usage, sources):
                                self.mod.combinatorial[usage] = ("fa",) + tuple(comb[1:])
                                break
                    else:
                        if not neg:
                            if is_sum0(usage, sources):
                                self.mod.combinatorial[usage] = ("fa",) + tuple(comb[1:])
                                break
                        else:
                            if is_sum1(usage, sources):
                                self.mod.combinatorial[usage] = ("fa",) + tuple(comb[1:])
                                break

    def _pass_invert_ffs(self):
        for net in self.rules["invert_ff"]:
            self._invert_ff(net)

    def _pass_trace_shifts(self):
        for net in self.rules["trace_shifts"]:
            src = self.mod.find_ff(net)
            if not src:
                continue
            orig = src

            visited = set()
            bits = []
            
            while True:
                neg = False
                inp = self.mod.find_dst_ff(src.dest)
                if (not inp) or (inp.ce != orig.ce) or (inp.ce_reset != orig.ce_reset) or (inp.reset != orig.reset) or (inp.clock != orig.clock):
                    print(f"First shift {src.dest}. Len {len(visited)}")
                    break
                if inp.dest in visited:
                    print(f"{net} Found loop: {len(visited)}")
                    break
                bits.append(src.reset_value)
                visited.add(inp.dest)

                src = inp

    def _pass_align_shifts(self):
        def decode(inv, order, bits):
            chars = len(bits) // 8
            b = np.array(bits[0:chars*8], np.uint8).reshape((-1, 8))
            if order:
                b = np.flip(b, axis=1)
            if inv:
                b ^= 1
            b = np.packbits(b.reshape((-1)), bitorder="little")
            s = bytes(b).decode("ascii", errors="ignore")
            return "'%s' '%s'" % (s, s[::-1])

        for net in self.rules["align_shifts"]:
            src = self.mod.find_ff(net)
            if not src:
                continue
            orig = src

            visited = set()
            bits = []
            
            while True:
                neg = False
                if match_op(src.value, "!"):
                    inp = self.mod.find_ff(src.value[1])
                    neg = True
                else:
                    inp = self.mod.find_ff(src.value)
                if (not inp) or (inp.ce != orig.ce) or (inp.ce_reset != orig.ce_reset) or (inp.reset != orig.reset) or (inp.clock != orig.clock):
                    break
                if inp.dest in visited:
                    break
                bits.append(src.reset_value)
                visited.add(inp.dest)

                if neg:
                    self._invert_ff(inp.dest)
                src = inp
            
            print("%s: %s" % (net, "".join(bits)))
            for inv in [0, 1]:
                for order in [0, 1]:
                    print(" %d,%d: %s" % (inv, order, decode(inv, order, bits)))

    def _pass_output(self):
        for net in self.rules["output"]:
            self.mod.outputs.append(net)

    def _pass_align_carrys(self):
        for net, expr in self.mod.combinatorial.items():
            if match_op(expr, "carry") and match_op(expr[1], "!") and self.mod.find_ff(expr[1][1]):
                self._invert_ff(expr[1][1])

    def _pass_bundle_wires(self):
        for name, bundle in self.rules["bundle_wires"].items():
            self.mod.bundles[name] = bundle

    def clean(self):
        self._pass_wire_forward()
        self._pass_optimize()
        self._pass_unused()

    def pass1(self):
        self.clean()
        self.clean()

    def pass2(self):
        self._pass_rename()
        self._pass_output()
        self._pass_ff_reset_propagate()
        self._pass_ff_promote_resets()

        self.clean()

        self._pass_carry_full_adder()

        self._pass_invert_ffs()
        self.clean()

        self._pass_align_shifts()
        self.clean()
        self._pass_trace_shifts()
    
    def pass3(self):
        self._pass_align_carrys()
        self.clean()

        self._pass_bundle_wires()
        self.clean()


with open("test.v", "r") as f:
    cleaner = Cleaner(f.readlines())
    cleaner.pass1()
    with open("top_clean_pass1.v", "w") as f:
        f.write(cleaner.format())
        
    cleaner.pass2()
    with open("top_clean_pass2.v", "w") as f:
        f.write(cleaner.format())
        
    cleaner.pass3()
    with open("top_clean_pass3.v", "w") as f:
        f.write(cleaner.format())
