import json, math, numpy as np, mpmath as mp
mp.mp.dps = 20
out = {}
# primes and counts
N = 1_000_000
sieve = np.ones(N + 1, bool); sieve[:2] = False
for i in range(2, int(N**0.5) + 1):
    if sieve[i]: sieve[i*i::i] = False
pi = np.cumsum(sieve)
out["primes100"] = [int(p) for p in np.nonzero(sieve[:110])[0]]
xs = list(range(2, 101))
out["stair"] = {"x": xs, "pi": [int(pi[x]) for x in xs], "li": [float(mp.li(x) - mp.li(2)) for x in xs]}
# error up to 1e6 vs the RH (Schoenfeld) band sqrt(x) ln x / (8 pi)
ex = np.unique(np.geomspace(3000, N, 400).astype(int))
out["err"] = {"x": ex.tolist(), "e": [float(pi[x] - mp.li(x)) for x in ex],
              "band": [math.sqrt(x) * math.log(x) / (8 * math.pi) for x in ex]}
# zeros
Z = 2000
gam = [float(mp.zetazero(n).imag) for n in range(1, Z + 1)]
out["zeros"] = gam[:40]
# explicit formula for Chebyshev psi on [2, 40]
xg = np.linspace(2, 40, 900)
def psi(x):
    s, p = 0.0, 2
    for p in range(2, int(x) + 1):
        if sieve[p]:
            q = p
            while q <= x: s += math.log(p); q *= p
    return s
out["psi"] = {"x": xg.tolist(), "true": [psi(x) for x in xg]}
for K in (0, 1, 3, 10, 30, 100, 300):
    vals = []
    for x in xg:
        s = x - math.log(2 * math.pi) - 0.5 * math.log(1 - x**-2)
        lx = math.log(x)
        for g in gam[:K]:
            r = complex(0.5, g)
            s -= 2 * (complex(math.exp(0.5 * lx) * math.cos(g * lx), math.exp(0.5 * lx) * math.sin(g * lx)) / r).real
        vals.append(s)
    out["psi"][f"K{K}"] = vals
# coin walk: heads - n/2 over a million flips
rng = np.random.default_rng(7)
walk = np.cumsum(rng.integers(0, 2, N)) - np.arange(1, N + 1) / 2
idx = np.unique(np.geomspace(1, N, 500).astype(int)) - 1
out["coin"] = {"n": (idx + 1).tolist(), "dev": walk[idx].tolist(), "final": float(walk[-1])}
# unfolded zero spacings
t = np.array(gam)
unf = t / (2 * math.pi) * np.log(t / (2 * math.pi * math.e))
s = np.diff(unf); s /= s.mean()
h, edges = np.histogram(s, bins=24, range=(0, 3), density=True)
out["spacing"] = {"h": h.tolist(), "edges": edges.tolist()}
json.dump(out, open("data.json", "w"))
print("ok", out["coin"]["final"], out["err"]["e"][-1], out["err"]["band"][-1], gam[:3])
