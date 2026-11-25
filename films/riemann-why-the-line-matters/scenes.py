"""Why the Riemann hypothesis matters: eight scenes, each timed to its narration (durations.json)."""
import json
import math
from pathlib import Path

from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Circle, Create, DashedLine, Dot, FadeIn, FadeOut,
                   Line, Rectangle, Scene, Text, Transform, VGroup, VMobject, Write, config, rate_functions)

HERE = Path(__file__).parent
DATA = json.loads((HERE / "data.json").read_text())
DUR = json.loads((HERE / "durations.json").read_text())
PAD = 1.2  # seconds of quiet after each narration

BG, INK, DIM = "#162044", "#E8E6F0", "#6C7AA8"
AMBER, TEAL, CORAL = "#F2B05E", "#4FC1B5", "#EE7B6A"
config.background_color = BG
FONT = "DejaVu Sans"


def T(s, size=30, color=INK, **kw):
    return Text(s, font=FONT, font_size=size, color=color, **kw)


class Timed(Scene):
    key = ""

    def setup(self):
        self.total = DUR[self.key] + PAD
        self.used = 0.0

    def p(self, *anims, t=1.0, **kw):
        self.play(*anims, run_time=t, **kw)
        self.used += t

    def w(self, t):
        if t > 0:
            self.wait(t)
            self.used += t

    def finish(self):
        self.w(max(self.total - self.used, 0.1))


def axes(x_range, y_range, w=11, h=5.6, log_y=False):
    return Axes(x_range=x_range, y_range=y_range, x_length=w, y_length=h, tips=False,
                axis_config={"color": DIM, "stroke_width": 2, "include_ticks": False})


def poly(ax, xs, ys, color, width=4):
    m = VMobject(stroke_color=color, stroke_width=width)
    m.set_points_as_corners([ax.c2p(x, y) for x, y in zip(xs, ys)])
    return m


def stairs(ax, xs, ys, color, width=4):
    pts = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        if i:
            pts.append(ax.c2p(x, ys[i - 1]))
        pts.append(ax.c2p(x, y))
    m = VMobject(stroke_color=color, stroke_width=width)
    m.set_points_as_corners(pts)
    return m


class S1(Timed):
    key = "s1_primes"

    def construct(self):
        title = T("Why the line matters", 54).to_edge(UP, buff=0.7)
        sub = T("the Riemann hypothesis, for everyone", 28, DIM).next_to(title, DOWN, buff=0.25)
        self.p(FadeIn(title), FadeIn(sub), t=1.5)
        primes = set(DATA["primes100"])
        cells = VGroup(*[T(str(n), 30, DIM) for n in range(1, 61)]).arrange_in_grid(6, 10, buff=(0.55, 0.35))
        cells.next_to(sub, DOWN, buff=0.6)
        self.p(FadeIn(cells, lag_ratio=0.02), t=2.0)
        lit = [c for n, c in zip(range(1, 61), cells) if n in primes]
        for c in lit[:6]:  # two, three, five, seven, eleven, thirteen, as they are read
            self.p(c.animate.set_color(AMBER).scale(1.25), t=0.55)
        self.p(*[c.animate.set_color(AMBER).scale(1.25) for c in lit[6:]], t=2.0, lag_ratio=0.15)
        note = T("no simple rule for where the next one is", 28, TEAL).to_edge(DOWN, buff=0.6)
        self.w(max(DUR[self.key] - 11.0 - self.used, 0))
        self.p(FadeIn(note), t=1.2)
        self.finish()


class S2(Timed):
    key = "s2_count"

    def construct(self):
        d = DATA["stair"]
        ax = axes([0, 100, 10], [0, 30, 5]).shift(DOWN * 0.3)
        xl = T("numbers up to 100", 22, DIM).next_to(ax.x_axis, DOWN, buff=0.2)
        self.p(Create(ax), FadeIn(xl), t=1.5)
        st = stairs(ax, d["x"], d["pi"], AMBER)
        lab1 = T("primes counted so far", 26, AMBER).to_corner(UP + LEFT, buff=0.6)
        self.w(3.0)
        self.p(Create(st), FadeIn(lab1), t=5.0, rate_func=rate_functions.linear)
        cur = poly(ax, d["x"], d["li"], TEAL, 5)
        lab2 = T("Gauss's smooth prediction", 26, TEAL).next_to(lab1, DOWN, aligned_edge=LEFT)
        self.p(Create(cur), FadeIn(lab2), t=4.0)
        self.w(2.5)
        zoom = Rectangle(width=2.6, height=1.6, color=CORAL).move_to(ax.c2p(70, 19))
        lab3 = T("the wobble", 26, CORAL).next_to(zoom, UP, buff=0.15)
        self.p(Create(zoom), FadeIn(lab3), t=1.5)
        self.finish()


class S3(Timed):
    key = "s3_waves"

    def construct(self):
        d = DATA["psi"]
        ax = axes([2, 40, 5], [0, 45, 10]).shift(DOWN * 0.3)
        self.p(Create(ax), t=1.0)
        true = poly(ax, d["x"], d["true"], AMBER, 3).set_stroke(opacity=0.45)
        tl = T("the primes' staircase (weighted)", 22, AMBER).to_corner(UP + LEFT, buff=0.6)
        self.p(FadeIn(true), FadeIn(tl), t=1.5)
        self.w(5.0)
        cur = poly(ax, d["x"], d["K0"], TEAL, 5)
        lab = T("waves added: 0", 30, TEAL).to_corner(UP + RIGHT, buff=0.6)
        self.p(Create(cur), FadeIn(lab), t=1.5)
        self.w(max(DUR[self.key] * 0.45 - self.used, 0))
        for K in (1, 3, 10, 30, 100, 300):
            self.p(Transform(cur, poly(ax, d["x"], d[f"K{K}"], TEAL, 5)),
                   Transform(lab, T(f"waves added: {K}", 30, TEAL).to_corner(UP + RIGHT, buff=0.6)), t=1.6)
        note = T("each wave comes from one zero", 26, INK).to_edge(DOWN, buff=0.35)
        self.p(FadeIn(note), t=1.0)
        self.finish()


def zero_map(height=6.0, width=4.0):
    """The strip from left 0 to right 1, the halfway line, and the first zeros as dots."""
    strip = Rectangle(width=width, height=height, stroke_width=0, fill_color="#22306A", fill_opacity=1)
    edges = VGroup(Line(strip.get_corner(DOWN + LEFT), strip.get_corner(UP + LEFT), color=DIM),
                   Line(strip.get_corner(DOWN + RIGHT), strip.get_corner(UP + RIGHT), color=DIM))
    line = DashedLine(strip.get_bottom(), strip.get_top(), color=AMBER, dash_length=0.12)
    g = DATA["zeros"]
    ymax = g[-1] + 2

    def at(re, im):
        return strip.get_bottom() + RIGHT * (re - 0.5) * width + UP * im / ymax * height

    dots = VGroup(*[Dot(at(0.5, im), radius=0.06, color=AMBER) for im in g])
    return strip, edges, line, dots, at


class S4(Timed):
    key = "s4_line"

    def construct(self):
        strip, edges, line, dots, at = zero_map()
        grp = VGroup(strip, edges, line, dots).shift(LEFT * 4.3)
        hl = T("height: how fast its wave wiggles", 26, INK).move_to(UP * 2.9 + LEFT * 1.4, aligned_edge=LEFT)
        lr = T("left–right: how fast its wave grows", 26, INK).next_to(hl, DOWN, aligned_edge=LEFT)
        self.p(FadeIn(strip), Create(edges), t=1.5)
        self.p(FadeIn(hl), t=1.2)
        self.w(1.8)
        self.p(FadeIn(lr), t=1.2)
        self.p(Create(line), t=1.2)
        self.p(FadeIn(dots, lag_ratio=0.08), t=4.0)
        c1 = T("more than ten trillion checked:", 26, INK).next_to(lr, DOWN, buff=0.8, aligned_edge=LEFT)
        c2 = T("all on the halfway line", 26, AMBER).next_to(c1, DOWN, aligned_edge=LEFT)
        self.p(FadeIn(c1), FadeIn(c2), t=1.5)
        self.w(max(DUR[self.key] - 6.0 - self.used, 0))
        h1 = T("The hypothesis: all of them are.", 28, TEAL).next_to(c2, DOWN, buff=0.7, aligned_edge=LEFT)
        h2 = T("Not the primes. The zeros.", 28, CORAL).next_to(h1, DOWN, aligned_edge=LEFT)
        self.p(FadeIn(h1), t=1.2)
        self.w(1.5)
        self.p(FadeIn(h2), t=1.2)
        self.finish()


class S5(Timed):
    key = "s5_offline"

    def construct(self):
        strip, edges, line, dots, at = zero_map(height=5.0, width=3.0)
        left = VGroup(strip, edges, line, dots.set_opacity(0.35)).to_edge(LEFT, buff=0.7)
        self.p(FadeIn(left), t=1.2)
        im = 70.0
        off_r = Circle(radius=0.13, color=CORAL, stroke_width=4).move_to(at(0.8, im))
        off_l = Circle(radius=0.13, color=CORAL, stroke_width=4).move_to(at(0.2, im))
        mirror = DashedLine(off_l.get_right(), off_r.get_left(), color=CORAL, dash_length=0.08)
        hyp = T("a hypothetical pair, off the line", 22, CORAL).next_to(left, DOWN, buff=0.25).to_edge(LEFT, buff=0.4)
        self.w(2.0)
        self.p(Create(off_l), Create(off_r), Create(mirror), FadeIn(hyp), t=2.0)
        self.p(off_r.animate.scale(1.6), t=0.8)
        self.p(off_r.animate.scale(1 / 1.6), t=0.6)
        ax = axes([1, 400, 50], [-80, 80, 20], w=7.2, h=5.2).to_edge(RIGHT, buff=0.5)
        self.p(Create(ax), t=1.0)
        xs = [1 + i * 399 / 700 for i in range(701)]
        on = [x ** 0.5 * math.cos(14.13 * math.log(x)) for x in xs]
        off = [x ** 0.8 * math.cos(14.13 * math.log(x)) / 2.2 for x in xs]
        w_on = poly(ax, xs, on, TEAL, 3)
        l_on = T("a wave from a zero on the line", 24, TEAL).next_to(ax, UP, buff=0.1).align_to(ax, LEFT)
        self.w(max(DUR[self.key] * 0.5 - self.used, 0))
        self.p(Create(w_on), FadeIn(l_on), t=3.5, rate_func=rate_functions.linear)
        w_off = poly(ax, xs, off, CORAL, 3)
        l_off = T("from a zero off it: grows faster", 24, CORAL).next_to(l_on, DOWN, buff=0.08, aligned_edge=LEFT)
        self.p(Create(w_off), FadeIn(l_off), t=3.5, rate_func=rate_functions.linear)
        self.finish()


class S6(Timed):
    key = "s6_coins"

    def construct(self):
        c = DATA["coin"]
        ax = axes([0, 1_000_000, 100_000], [-1100, 1100, 250], w=11, h=5.0).shift(DOWN * 0.4)
        top = T("a million fair coin flips: heads minus half", 26, INK).to_edge(UP, buff=0.5)
        self.p(FadeIn(top), Create(ax), t=1.5)
        n = [1] + c["n"]
        band = [x ** 0.5 for x in n]
        b1 = poly(ax, n, band, DIM, 3)
        b2 = poly(ax, n, [-b for b in band], DIM, 3)
        walk = poly(ax, c["n"], c["dev"], AMBER, 3)
        self.w(3.0)
        self.p(Create(walk), t=6.0, rate_func=rate_functions.linear)
        bl = T("the square-root band", 22, DIM).next_to(ax.c2p(1_000_000, -700), LEFT, buff=0.1)
        self.p(Create(b1), Create(b2), FadeIn(bl), t=2.0)
        self.w(max(DUR[self.key] * 0.45 - self.used, 0))
        e = DATA["err"]
        ax2 = axes([0, 1_000_000, 100_000], [-700, 700, 100], w=11, h=5.0).shift(DOWN * 0.4)
        top2 = T("the primes: actual count minus prediction, up to a million", 26, INK).to_edge(UP, buff=0.5)
        e1 = poly(ax2, e["x"], e["band"], DIM, 3)
        e2 = poly(ax2, e["x"], [-b for b in e["band"]], DIM, 3)
        bl2 = T("the band the hypothesis promises", 22, DIM).next_to(ax2.c2p(1_000_000, 620), LEFT, buff=0.1)
        self.p(FadeOut(walk), FadeOut(b1), FadeOut(b2), FadeOut(bl), FadeOut(top), FadeOut(ax),
               FadeIn(ax2), FadeIn(top2), t=1.5)
        self.p(Create(e1), Create(e2), FadeIn(bl2), t=1.5)
        err = poly(ax2, e["x"], e["e"], TEAL, 4)
        self.p(Create(err), t=4.0, rate_func=rate_functions.linear)
        cap = T("inside the band: a fair-coin-sized wobble", 24, TEAL).to_edge(DOWN, buff=0.3)
        self.p(FadeIn(cap), t=1.2)
        self.finish()


class S7(Timed):
    key = "s7_why"

    def construct(self):
        one = T("1 · a guarantee", 34, AMBER).to_corner(UP + LEFT, buff=0.7)
        one_b = T("hundreds of theorems are written assuming it", 24, INK).next_to(one, DOWN, aligned_edge=LEFT)
        self.p(FadeIn(one), t=1.0)
        self.w(2.5)
        self.p(FadeIn(one_b), t=1.2)
        self.w(max(DUR[self.key] * 0.38 - self.used, 0))
        two = T("2 · a hidden pattern?", 34, TEAL).next_to(one_b, DOWN, buff=0.6, aligned_edge=LEFT)
        self.p(FadeIn(two), t=1.0)
        s = DATA["spacing"]
        ax = axes([0, 3, 0.5], [0, 1.1, 0.25], w=6.5, h=3.4).to_corner(DOWN + RIGHT, buff=0.6)
        bars = VGroup()
        for h, a, b in zip(s["h"], s["edges"][:-1], s["edges"][1:]):
            r = Rectangle(width=(ax.c2p(b, 0) - ax.c2p(a, 0))[0] * 0.92, height=max((ax.c2p(0, h) - ax.c2p(0, 0))[1], 0.001),
                          stroke_width=0, fill_color=AMBER, fill_opacity=0.85)
            r.move_to(ax.c2p((a + b) / 2, 0), aligned_edge=DOWN)
            bars.add(r)
        xs = [i * 3 / 200 for i in range(201)]
        gue = poly(ax, xs, [32 / math.pi**2 * x * x * math.exp(-4 * x * x / math.pi) for x in xs], TEAL, 4)
        poi = poly(ax, xs, [math.exp(-x) for x in xs], DIM, 3)
        lab = VGroup(T("gaps between 2,000 zeros", 20, AMBER),
                     T("energy-level statistics (heavy nuclei)", 20, TEAL),
                     T("if the zeros were scattered at random", 20, DIM)).arrange(DOWN, aligned_edge=LEFT, buff=0.12)
        lab.to_corner(DOWN + LEFT, buff=0.7)
        self.w(3.0)
        self.p(Create(ax), FadeIn(bars, lag_ratio=0.05), FadeIn(lab[0]), t=2.0)
        self.w(max(DUR[self.key] * 0.78 - self.used, 0))
        self.p(Create(gue), FadeIn(lab[1]), Create(poi), FadeIn(lab[2]), t=2.0)
        self.finish()


class S8(Timed):
    key = "s8_end"

    def construct(self):
        strip, edges, line, dots, at = zero_map(height=5.5, width=3.0)
        grp = VGroup(strip, edges, line, dots).shift(LEFT * 3)
        self.p(FadeIn(grp), t=1.5)
        a = T("proved: not yet", 30, INK).shift(RIGHT * 2.5 + UP * 0.5)
        b = T("zeros found off the line: none", 30, AMBER).next_to(a, DOWN, buff=0.35)
        self.p(FadeIn(a), t=1.0)
        self.w(2.0)
        self.p(FadeIn(b), t=1.0)
        self.w(max(self.total - self.used - 3.0, 0))
        self.p(FadeOut(VGroup(grp, a, b)), t=3.0)
        self.finish()
