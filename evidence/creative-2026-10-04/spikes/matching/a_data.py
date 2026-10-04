"""(a) Model-free: does the best creative depend on character genre / safety tier?

Run:  uv run --project /Users/aadi/simula-ctr python a_data.py [B_pooled_perm] [B_stratified_perm]
Writes results_a.md and a_results.json next to this file. Uses _cache.parquet (built on first run, deleted at the end).
"""
import json, sys, time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import chi2, norm
from lib import *

OUT = Path(__file__).parent
B_POOL = int(sys.argv[1]) if len(sys.argv) > 1 else 40
B_STRAT = int(sys.argv[2]) if len(sys.argv) > 2 else 100
K_TOP = 20          # creatives per definition (top by volume)
MIN_CELL = 200      # impressions a creative x group cell needs to be eligible as "best"
rng = np.random.default_rng(20261003)
GROUPS = {"genre": ["anime", "comedic", "fantasy", "historical", "horror", "mentor", "mystery", "romance", "sci", "slice"],
          "safety_tier": ["sfw", "suggestive", "mature"]}
# surface handling in the pooled model: ("other", 30) = 30 surface effects + one lumped "other" bucket (naive);
# ("exact", 100) = keep only rows on the 100 biggest surfaces, one effect each (primary)
SPECS = {"naive": dict(inter=False, surf=("other", 30)),
         "inter+other": dict(inter=True, surf=("other", 30)),
         "primary": dict(inter=True, surf=("exact", 100))}


def codes_for(sub, ckey, gkey):
    cre_order = sub[ckey].value_counts().index
    c = pd.Categorical(sub[ckey], categories=cre_order).codes
    g = pd.Categorical(sub[gkey], categories=GROUPS[gkey]).codes
    return c.astype(int), g.astype(int), list(cre_order), GROUPS[gkey]


def fe_codes(sub, ctrl_key, pooled, surf=("exact", 100)):
    """Integer codes of the nuisance factors: [surface], day, hour_of_day, control group."""
    cols = []
    if pooled:
        top = list(sub["surface"].value_counts().head(surf[1]).index)
        m = {v: i for i, v in enumerate(top)}
        cols.append(sub["surface"].map(m).fillna(len(top)).to_numpy(int))   # only used in "other" mode
    cols.append(pd.Categorical(sub["day"]).codes)
    cols.append(sub["hour_of_day"].to_numpy())
    cols.append(pd.Categorical(sub[ctrl_key], categories=GROUPS[ctrl_key]).codes)
    return [np.asarray(c, int) for c in cols]


def pair_dummies(a, b):
    """Interaction dummies for factor a x factor b, reference levels dropped on both sides."""
    lb = b.max() + 1
    codes = np.where((a > 0) & (b > 0), (a - 1) * (lb - 1) + (b - 1), -1)
    return onehot(codes, drop_first=False)


def nuisance(fe, g, pooled, inter=True):
    """Additive surface/day/hour/control effects, optionally PLUS surface x group and day x group.

    The interaction blocks let the null model say the group's effect differs by surface and by day,
    so creative x group only measures what is left within a surface/day.
    """
    mats = [onehot(c) for c in fe]
    if inter:
        if pooled:
            mats.append(pair_dummies(fe[0], g))     # surface x group
        mats.append(pair_dummies(fe[1] if pooled else fe[0], g))   # day x group
    X = sp.hstack(mats).tocsr()
    keep = np.asarray(X.sum(axis=0)).ravel() > 0
    return X[:, keep]


def cell_design(c, g, K, G, FE):
    n = len(c)
    C = sp.csr_matrix((np.ones(n), (np.arange(n), c * G + g)), shape=(n, K * G))
    cell_cols = np.where(np.asarray(C.sum(axis=0)).ravel() > 0)[0]
    return sp.hstack([C[:, cell_cols], FE]).tocsr(), cell_cols


def designs(c, g, K, G, fe, pooled, inter=True):
    """Null design (intercept + creative + group + nuisance) and alternative (full creative x group cell means + nuisance)."""
    FE = nuisance(fe, g, pooled, inter)
    X0 = sp.hstack([sp.csr_matrix(np.ones((len(c), 1))), onehot(c), onehot(g), FE]).tocsr()
    X1, cell_cols = cell_design(c, g, K, G, FE)
    return X0, X1, cell_cols


def cell_info(b1, H1, cell_cols, K, G):
    """theta (K x G), covariance (KG x KG) and null-space rows per cell (KG x m) from a cell-means fit."""
    nk = len(cell_cols)
    theta = np.full(K * G, np.nan); theta[cell_cols] = b1[:nk]
    cov = np.full((K * G, K * G), np.nan); cov[np.ix_(cell_cols, cell_cols)] = np.linalg.inv(H1)[:nk, :nk]
    Nb = null_basis(H1)
    nb = np.full((K * G, Nb.shape[1]), np.nan); nb[cell_cols] = Nb[:nk]
    return theta.reshape(K, G), cov, nb


def fit_pair(y, c, g, K, G, fe, pooled, want_cells=False, inter=True):
    """Fit null (additive creative + group) and alternative (full creative x group cell means), same nuisance."""
    X0, X1, cell_cols = designs(c, g, K, G, fe, pooled, inter)
    b0, ll0, H0 = irls(X0, y)
    b1, ll1, H1 = irls(X1, y)
    out = dict(ll0=ll0, ll1=ll1, p0=rank_of(H0), p1=rank_of(H1))
    out["stat"], out["dof"], out["p"] = lr_test(ll0, ll1, out["p0"], out["p1"])
    if want_cells:
        out.update(b0=b0, b1=b1, H1=H1, cell_cols=cell_cols, X0=X0)
    return out


def fit_cells(y, c, g, K, G, fe, pooled, inter=True):
    """Cell-means fit only: theta (K x G), covariance, null-space rows."""
    FE = nuisance(fe, g, pooled, inter)
    X1, cell_cols = cell_design(c, g, K, G, FE)
    b1, _, H1 = irls(X1, y)
    return cell_info(b1, H1, cell_cols, K, G)


def select_best(theta, nb, n_ok, w_g):
    """Pick the overall best creative k* and each group's best, using only comparisons the data can identify.

    Reference creative r = most common creative testable in every group. A creative is comparable to r in group g if the
    contrast cell(c,g) - cell(r,g) lies outside the null space (estimable). k* maximises the share-weighted mean contrast to r over
    creatives comparable in every group; a group's best is the max-theta creative among those comparable in that group.
    Returns (kstar, best[g] or -1)."""
    K, G = theta.shape
    full = np.where(n_ok.all(axis=1))[0]
    if not len(full):
        return None, None
    r = int(full[0])
    est = np.zeros((K, G), bool)
    for c in range(K):
        for g in range(G):
            if n_ok[c, g] and n_ok[r, g] and not np.isnan(theta[c, g]) and not np.isnan(theta[r, g]):
                est[c, g] = np.abs(nb[c * G + g] - nb[r * G + g]).max() < 1e-6 if nb.shape[1] else True
    cand = np.where(est.all(axis=1))[0]
    rel = (np.nan_to_num(theta) - np.nan_to_num(theta[r])[None, :]) * w_g[None, :]
    kstar = int(cand[np.argmax(rel[cand].sum(1))])
    best = np.full(G, -1)
    for g in range(G):
        pool = np.where(est[:, g])[0]
        if kstar in pool:
            best[g] = int(pool[np.argmax(theta[pool, g])])
    return kstar, best


def contrasts(theta, cov, nb, n_cell, w_g, min_cell=MIN_CELL):
    """Per group: adjusted best creative vs overall best, Wald z of the log-odds contrast (identified comparisons only)."""
    K, G = theta.shape
    kstar, best = select_best(theta, nb, n_cell >= min_cell, w_g)
    if kstar is None:
        return None, pd.DataFrame(columns=["g", "best", "kstar", "diff", "se", "z", "differs", "dist"])
    rows = []
    for g in range(G):
        if best[g] < 0:
            continue
        if best[g] == kstar:
            rows.append(dict(g=g, best=kstar, kstar=kstar, diff=0.0, se=np.nan, z=np.nan, differs=False, dist=False)); continue
        i, j = best[g] * G + g, kstar * G + g
        d = theta[best[g], g] - theta[kstar, g]
        se = np.sqrt(cov[i, i] + cov[j, j] - 2 * cov[i, j])
        rows.append(dict(g=g, best=int(best[g]), kstar=kstar, diff=d, se=se, z=d / se, differs=True, dist=abs(d / se) > 1.96))
    return kstar, pd.DataFrame(rows)


def holm(z_list, alpha=0.05):
    p = 2 * norm.sf(np.abs(np.array(z_list, float)))
    sig = np.zeros(len(p), bool)
    for r, idx in enumerate(np.argsort(p)):
        if p[idx] <= alpha / (len(p) - r):
            sig[idx] = True
        else:
            break
    return sig


def subset(df, ckey, surf):
    top = df[ckey].value_counts().head(K_TOP).index
    sub = df[df[ckey].isin(top)]
    if surf[0] == "exact":
        sub = sub[sub["surface"].isin(sub["surface"].value_counts().head(surf[1]).index)]
    return sub.reset_index(drop=True)


def strata_perm(char_label_code, strata_groups):
    lab = char_label_code.copy()
    for idx in strata_groups:
        lab[idx] = rng.permutation(lab[idx])
    return lab


def pooled_test(df, ckey, gkey, ctrl_key, spec="primary", n_perm=0):
    """Pooled LR test (creative x group) with the nuisance terms of the chosen spec; optional character-level permutation null."""
    cfg = SPECS[spec]
    n_all = int(df[ckey].isin(df[ckey].value_counts().head(K_TOP).index).sum())
    sub = subset(df, ckey, cfg["surf"])
    c, g, cre_list, g_list = codes_for(sub, ckey, gkey)
    K, G = len(cre_list), len(g_list)
    fe = fe_codes(sub, ctrl_key, True, cfg["surf"])
    y = sub["click"].to_numpy(float)
    res = fit_pair(y, c, g, K, G, fe, True, want_cells=True, inter=cfg["inter"])
    n_cell = np.zeros((K, G)); k_cell = np.zeros((K, G))
    np.add.at(n_cell, (c, g), 1); np.add.at(k_cell, (c, g), y)
    info = dict(spec=spec, ckey=ckey, gkey=gkey, rows=len(sub), row_share_kept=len(sub) / n_all, K=K, G=G,
                stat=res["stat"], dof=res["dof"], p=res["p"], n_small_cells=int((n_cell < MIN_CELL).sum()))
    if n_perm:
        # permute group labels among characters that share a modal surface (keeps the genre-surface link, character clusters intact)
        char = pd.Categorical(sub["character_id"]).codes
        lab = pd.Categorical(sub.groupby(char)[gkey].first().to_numpy(), categories=g_list).codes
        modal = sub.groupby(char)["surface"].agg(lambda s: s.value_counts().index[0])
        groups = [np.where(modal.to_numpy() == s)[0] for s in modal.unique()]
        stats = [fit_pair(y, c, strata_perm(lab, groups)[char], K, G, fe, True, inter=cfg["inter"])["stat"] for _ in range(n_perm)]
        info["perm"] = dict(B=n_perm, p_perm=(1 + sum(x >= res["stat"] for x in stats)) / (1 + n_perm), stat_mean=float(np.mean(stats)),
                            stat_q95=float(np.quantile(stats, .95)))
    return info, dict(sub=sub, c=c, g=g, cre=cre_list, glist=g_list, n_cell=n_cell, k_cell=k_cell, res=res, fe=fe, y=y, K=K, G=G, inter=cfg["inter"])


def heldout_loglik(aux, n_splits=10):
    """Does knowing the group help choose creatives out of sample? Fit null/alternative on half the characters, score the other half."""
    sub, c, g, y, fe, K, G = aux["sub"], aux["c"], aux["g"], aux["y"], aux["fe"], aux["K"], aux["G"]
    X0, X1, _ = designs(c, g, K, G, fe, True, aux["inter"])
    char = pd.Categorical(sub["character_id"]).codes
    out = []
    for s in range(n_splits):
        half = (rng.random(char.max() + 1) < 0.5)[char]
        for tr in (half, ~half):
            te = ~tr
            ll = []
            for X in (X0, X1):
                b, _, _ = irls(X[tr], y[tr])
                e = X[te] @ b
                ll.append(float(np.sum(y[te] * e - np.logaddexp(0, e))) / te.sum())
            out.append(dict(split=s, d=ll[1] - ll[0], ll0=ll[0], ll1=ll[1]))
    T = pd.DataFrame(out)
    return dict(n_eval=len(T), mean_gain_per_imp=float(T.d.mean()), sd=float(T.d.std()), frac_positive=float((T.d > 0).mean()),
                gain_per_10k_imps=float(1e4 * T.d.mean()), ll0=float(T.ll0.mean()), ll1=float(T.ll1.mean()))


def surface_parts(df, ckey, gkey, ctrl_key, min_creative_n=2000, n_surf=12):
    """Per-surface datasets: creatives with >= min_creative_n impressions on that surface (need >= 3). Creatives only
    compare cleanly when they run on the SAME surface, so everything below is done inside one surface at a time."""
    parts = []
    for s in df["surface"].value_counts().head(n_surf).index:
        d = df[df["surface"] == s]
        cnt = d[ckey].value_counts()
        keep = cnt[cnt >= min_creative_n].index
        if len(keep) < 3:
            continue
        sub = d[d[ckey].isin(keep)].reset_index(drop=True)
        c, g, cre_list, g_list = codes_for(sub, ckey, gkey)
        char = pd.Categorical(sub["character_id"]).codes
        lab = pd.Categorical(sub.groupby(char)[gkey].first().to_numpy(), categories=g_list).codes
        parts.append(dict(surface=s, rows=len(sub), c=c, g=g, K=len(cre_list), G=len(g_list), cre=cre_list, glist=g_list,
                          fe=fe_codes(sub, ctrl_key, False), y=sub["click"].to_numpy(float), char=char, lab=lab))
    return parts


def part_fit(P, g, min_cell):
    r = fit_pair(P["y"], P["c"], g, P["K"], P["G"], P["fe"], False, want_cells=True)
    n_cell = np.zeros((P["K"], P["G"])); np.add.at(n_cell, (P["c"], g), 1)
    theta, cov, nb = cell_info(r["b1"], r["H1"], r["cell_cols"], P["K"], P["G"])
    ks, tb = contrasts(theta, cov, nb, n_cell, n_cell.sum(0) / n_cell.sum(), min_cell)
    return r, n_cell, ks, tb, theta


def stratified_test(df, ckey, gkey, ctrl_key, n_perm=0, min_cell=100):
    """One LR test per big surface (day x group nuisance), statistics summed, plus the best-vs-overall-best count per surface.
    Permutation: shuffle group labels among the characters seen on that surface (clusters kept intact)."""
    parts = surface_parts(df, ckey, gkey, ctrl_key)
    rows = []
    for P in parts:
        r, n_cell, ks, tb, theta = part_fit(P, P["g"], min_cell)
        P["obs"] = (r, n_cell, ks, tb)
        rows.append(dict(surface=P["surface"], rows=P["rows"], K=P["K"], stat=r["stat"], dof=r["dof"], p=r["p"],
                         n_pairs=len(tb), n_differs=int(tb.differs.sum()), n_dist=int(tb.dist.sum())))
    T = pd.DataFrame(rows)
    comb = dict(stat=float(T.stat.sum()), dof=int(T.dof.sum()), p=float(chi2.sf(T.stat.sum(), T.dof.sum())), n_surfaces=len(T),
                n_pairs=int(T.n_pairs.sum()), n_differs=int(T.n_differs.sum()), n_dist=int(T.n_dist.sum()))
    if n_perm:
        sums, nd, ndist = [], [], []
        for b in range(n_perm):
            tot = d1 = d2 = 0
            for P in parts:
                gp = rng.permutation(P["lab"])[P["char"]]
                r, _, _, tb, _ = part_fit(P, gp, min_cell)
                tot += r["stat"]; d1 += int(tb.differs.sum()); d2 += int(tb.dist.sum())
            sums.append(tot); nd.append(d1); ndist.append(d2)
        comb.update(B=n_perm, p_perm=(1 + sum(x >= comb["stat"] for x in sums)) / (1 + n_perm), perm_mean=float(np.mean(sums)),
                    perm_q95=float(np.quantile(sums, .95)), perm_differs_mean=float(np.mean(nd)), perm_dist_mean=float(np.mean(ndist)), perm_dist_max=int(max(ndist)))
    return T, comb, parts


def tailoring_gain(parts, n_splits=10, min_cell=50):
    """Honest value of tailoring, within surface: pick each group's best creative on half the characters, score it on the other
    half against the same half's overall best; weights = surface rows x group share; independent surfaces so variances add.
    Only comparisons the data can identify are used (see select_best); skipped groups count as zero gain."""
    out = []
    tot_rows = sum(P["rows"] for P in parts)
    sig = lambda z: 1 / (1 + np.exp(-z))
    for s in range(n_splits):
        halves = [(rng.random(P["char"].max() + 1) < 0.5)[P["char"]] for P in parts]
        for X in ("A", "B"):
            d_out = d_in = ctr_out = ctr_in = var = 0.0
            n_pick = n_skip = 0
            for P, hA in zip(parts, halves):
                mX, mY = (hA, ~hA) if X == "A" else (~hA, hA)
                K, G = P["K"], P["G"]
                fit = {}
                for name, m in (("X", mX), ("Y", mY)):
                    th, cv, nb = fit_cells(P["y"][m], P["c"][m], P["g"][m], K, G, [f[m] for f in P["fe"]], False)
                    n = np.zeros((K, G)); np.add.at(n, (P["c"][m], P["g"][m]), 1)
                    fit[name] = (th, cv, nb, n)
                (thX, _, nbX, nX), (thY, cvY, nbY, nY) = fit["X"], fit["Y"]
                w_g = np.bincount(P["g"], minlength=G) / len(P["g"])
                p_g = np.bincount(P["g"], weights=P["y"], minlength=G) / np.bincount(P["g"], minlength=G)
                kstar, best = select_best(thX, nbX, (nX >= min_cell) & (nY >= min_cell), w_g)
                if kstar is None:
                    continue
                vec = np.zeros(K * G); dg_out = np.zeros(G); dg_in = np.zeros(G)
                for gi in range(G):
                    b = best[gi]
                    if b < 0 or b == kstar:
                        continue
                    n_pick += 1
                    i, j = b * G + gi, kstar * G + gi
                    if np.isnan(thY[b, gi]) or np.isnan(thY[kstar, gi]) or (nbY.shape[1] and np.abs(nbY[i] - nbY[j]).max() > 1e-6):
                        n_skip += 1; continue
                    dg_out[gi] = thY[b, gi] - thY[kstar, gi]; dg_in[gi] = thX[b, gi] - thX[kstar, gi]
                    vec[i] += w_g[gi]; vec[j] -= w_g[gi]
                w_s = P["rows"] / tot_rows
                lo = np.log(p_g / (1 - p_g))
                d_out += w_s * (w_g * dg_out).sum(); d_in += w_s * (w_g * dg_in).sum()
                ctr_out += w_s * (w_g * (sig(lo + dg_out) - p_g)).sum(); ctr_in += w_s * (w_g * (sig(lo + dg_in) - p_g)).sum()
                var += w_s ** 2 * float(vec @ np.nan_to_num(cvY) @ vec)
            out.append(dict(split=s, train=X, d_out=d_out, se_out=var ** .5, d_in=d_in, ctr_out=100 * ctr_out, ctr_in=100 * ctr_in, n_pick=n_pick, n_skip=n_skip))
    T = pd.DataFrame(out)
    return dict(n_eval=len(T), n_surfaces=len(parts), held_out_logodds=float(T.d_out.mean()), held_out_logodds_sd=float(T.d_out.std()), wald_se_logodds=float(T.se_out.mean()),
                held_out_ctr_pp=float(T.ctr_out.mean()), held_out_ctr_pp_sd=float(T.ctr_out.std()), frac_positive=float((T.d_out > 0).mean()),
                in_sample_logodds=float(T.d_in.mean()), in_sample_ctr_pp=float(T.ctr_in.mean()),
                mean_picks_differing_from_overall=float(T.n_pick.mean()), mean_picks_not_estimable_in_heldout=float(T.n_skip.mean()))


def power_sim(aux, deltas=(0, 0.05, 0.1, 0.2, 0.3), reps=8):
    """Plant +delta log-odds on one random creative per group in the pooled primary design, simulate clicks from the fitted null
    model, and count how often the LR test rejects at 5%."""
    res, c, g, fe = aux["res"], aux["c"], aux["g"], aux["fe"]
    K, G = len(aux["cre"]), len(aux["glist"])
    eta0 = res["X0"] @ res["b0"]
    out = {}
    for d in deltas:
        rej, stats = [], []
        for r in range(reps):
            planted = rng.integers(0, K, G)
            bump = d * (c == planted[g]).astype(float)
            y_sim = (rng.random(len(c)) < 1 / (1 + np.exp(-(eta0 + bump)))).astype(float)
            fr = fit_pair(y_sim, c, g, K, G, fe, True, inter=True)
            rej.append(fr["p"] < 0.05); stats.append(fr["stat"] - fr["dof"])
        out[str(d)] = dict(reject_rate=float(np.mean(rej)), reps=reps, mean_excess_stat=float(np.mean(stats)))
    return out


def fmt_p(p):
    return "<1e-300" if p < 1e-300 else ("%.2g" % p)


def short(t):
    pos, c14, w, h, c17, c18, c19, c20, c21 = t.split("|")
    return f"pos{pos} C14={c14} {w}x{h} C17={c17} C18={c18} C19={c19} C20={c20} C21={c21}"


def raw_best_table(n_cell, k_cell, min_cell=MIN_CELL):
    """Unadjusted: per group, raw-CTR best creative vs the overall raw-CTR best; two-proportion z for the gap."""
    K, G = n_cell.shape
    ctr = k_cell / np.maximum(n_cell, 1)
    overall = k_cell.sum(1) / n_cell.sum(1)
    elig_all = (n_cell >= min_cell).all(axis=1)
    kstar = int(np.argmax(np.where(elig_all, overall, -1)))
    rows = []
    for g in range(G):
        elig = n_cell[:, g] >= min_cell
        best = int(np.argmax(np.where(elig, ctr[:, g], -1)))
        if best == kstar:
            rows.append((g, best, kstar, False, False)); continue
        se = np.sqrt(ctr[best, g] * (1 - ctr[best, g]) / n_cell[best, g] + ctr[kstar, g] * (1 - ctr[kstar, g]) / n_cell[kstar, g])
        rows.append((g, best, kstar, True, abs(ctr[best, g] - ctr[kstar, g]) / se > 1.96))
    return kstar, pd.DataFrame(rows, columns=["g", "best", "kstar", "differs", "dist"])


def main():
    t0 = time.time()
    df = prep(cache=True)
    print("loaded", len(df), round(time.time() - t0, 1), "s", flush=True)
    results, md, primary, strat = {}, [], {}, {}
    combos = [("tuple", "genre", "safety_tier"), ("tuple", "safety_tier", "genre"), ("C14", "genre", "safety_tier"),
              ("C14", "safety_tier", "genre"), ("look", "genre", "safety_tier"), ("look", "safety_tier", "genre")]
    # 1. pooled LR ladder
    for spec in ("naive", "inter+other", "primary"):
        for ckey, gkey, ctrl in combos:
            if spec == "inter+other" and ckey != "tuple":
                continue
            do_perm = spec == "primary" and ckey in ("tuple", "look")
            t1 = time.time()
            info, aux = pooled_test(df, ckey, gkey, ctrl, spec=spec,
                                         n_perm=(B_POOL if gkey == "genre" else max(B_POOL // 2, 10)) if do_perm else 0)
            results[f"{ckey}_{gkey}_{spec}"] = info
            if spec == "primary":
                primary[(ckey, gkey)] = aux
            print("POOLED", ckey, gkey, spec, "stat=%.1f dof=%d p=%s rows=%d (%.2f)" % (info["stat"], info["dof"], fmt_p(info["p"]), info["rows"], info["row_share_kept"]),
                  info.get("perm"), "%ds" % (time.time() - t1), flush=True)
            json.dump(results, open(OUT / "a_results.json", "w"), indent=1, default=float)
    # 2. within-surface LR + best-vs-overall-best counts (+ permutation null)
    for ckey, gkey, ctrl in combos:
        do_perm = ckey in ("tuple", "look")
        t1 = time.time()
        T, comb, parts = stratified_test(df, ckey, gkey, ctrl, n_perm=(B_STRAT if gkey == "genre" else max(B_STRAT // 2, 20)) if do_perm else 0)
        results[f"strat_{ckey}_{gkey}"] = dict(comb=comb, rows=T.to_dict("records"))
        strat[(ckey, gkey)] = (T, comb, parts)
        print("WITHIN-SURFACE", ckey, gkey, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in comb.items()}, "%ds" % (time.time() - t1), flush=True)
        json.dump(results, open(OUT / "a_results.json", "w"), indent=1, default=float)
    # 3. naive raw-CTR best-vs-overall (pooled across surfaces, no adjustment) on the same top-K rows
    for (ckey, gkey), aux in primary.items():
        kst, rt = raw_best_table(aux["n_cell"], aux["k_cell"])
        results[f"rawbest_{ckey}_{gkey}"] = dict(n_groups=len(rt), n_differs=int(rt.differs.sum()), n_dist=int(rt.dist.sum()), kstar=kst)
    # 4. held-out tailoring value, within surface
    for ckey in ("tuple", "look"):
        for gkey in ("genre", "safety_tier"):
            t1 = time.time()
            results[f"gain_{ckey}_{gkey}"] = tailoring_gain(strat[(ckey, gkey)][2])
            print("GAIN", ckey, gkey, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in results[f"gain_{ckey}_{gkey}"].items()}, "%ds" % (time.time() - t1), flush=True)
    # 4b. out-of-sample log-likelihood gain from allowing creative x group
    for (ckey, gkey), aux in primary.items():
        if ckey == "C14":
            continue
        t1 = time.time()
        results[f"heldout_ll_{ckey}_{gkey}"] = heldout_loglik(aux)
        print("HELDOUT-LL", ckey, gkey, results[f"heldout_ll_{ckey}_{gkey}"], "%ds" % (time.time() - t1), flush=True)
    # 5. power
    results["power_tuple_genre"] = power_sim(primary[("tuple", "genre")])
    print("POWER", results["power_tuple_genre"], flush=True)
    json.dump(results, open(OUT / "a_results.json", "w"), indent=1, default=float)

    # ---- markdown ----
    md.append("### 1. Pooled likelihood-ratio tests of creative x group interaction (top %d creatives per definition)\n" % K_TOP)
    md.append("Specs: naive = additive surface(top30+other)/day/hour/control only; inter+other = adds surface x group and day x group nuisance terms; primary = same nuisance terms but surface effects exact (rows on the 100 biggest surfaces only, no lumped bucket).\n")
    md.append("| creative def | group | spec | rows (share of top-K rows kept) | K x G | LR stat | df | p (chi-sq) | permutation p (B) | perm null mean stat |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for spec in ("naive", "inter+other", "primary"):
        for ckey, gkey, ctrl in combos:
            if f"{ckey}_{gkey}_{spec}" not in results:
                continue
            r = results[f"{ckey}_{gkey}_{spec}"]; pm = r.get("perm")
            md.append(f"| {ckey} | {gkey} | {spec} | {r['rows']:,} ({r['row_share_kept']:.2f}) | {r['K']}x{r['G']} | {r['stat']:.1f} | {r['dof']} | {fmt_p(r['p'])} | "
                      + (f"{pm['p_perm']:.3f} ({pm['B']})" if pm else "-") + " | " + (f"{pm['stat_mean']:.1f}" if pm else "-") + " |")
    md.append("\n### 2. Within-surface tests (one model per big surface, creatives with >= 2,000 impressions there, day/hour/control + day x group nuisance; statistics summed across surfaces)\n")
    md.append("| creative def | group | surfaces used | LR sum stat / df | p (chi-sq) | permutation p (B) | perm null mean / 95th pct of sum | (surface x group) pairs | best != overall best | ... and 95%-distinguishable | perm null: mean differs / mean distinguishable (max) |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for ckey, gkey, ctrl in combos:
        c_ = strat[(ckey, gkey)][1]
        md.append(f"| {ckey} | {gkey} | {c_['n_surfaces']} | {c_['stat']:.1f} / {c_['dof']} | {c_['p']:.3f} | " + (f"{c_['p_perm']:.3f} ({c_['B']})" if 'B' in c_ else "-") + " | "
                  + (f"{c_['perm_mean']:.1f} / {c_['perm_q95']:.1f}" if 'B' in c_ else "-") + f" | {c_['n_pairs']} | {c_['n_differs']} | {c_['n_dist']} | "
                  + (f"{c_['perm_differs_mean']:.1f} / {c_['perm_dist_mean']:.2f} ({c_['perm_dist_max']})" if 'B' in c_ else "-") + " |")
    md.append("\nPer-surface detail (tuple and look definitions):\n")
    md.append("| creative def | group | surface | rows | K | LR stat / df | p | genres/tiers with best != overall best | ... distinguishable |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for (ckey, gkey), (T, c_, parts) in strat.items():
        if ckey == "C14":
            continue
        for _, r in T.iterrows():
            md.append(f"| {ckey} | {gkey} | {r.surface} | {int(r.rows):,} | {int(r.K)} | {r.stat:.1f} / {int(r.dof)} | {r.p:.3f} | {int(r.n_differs)}/{int(r.n_pairs)} | {int(r.n_dist)} |")
    md.append("\n### 3. Naive raw-CTR version of 'best creative differs' (pooled across surfaces, unadjusted, top-20 creatives, cells >= %d imps)\n" % MIN_CELL)
    md.append("| creative def | group | groups | raw best != overall raw best | ... gap significant (two-prop z, 95%) |")
    md.append("|---|---|---|---|---|")
    for ckey, gkey, ctrl in combos:
        r = results[f"rawbest_{ckey}_{gkey}"]
        md.append(f"| {ckey} | {gkey} | {r['n_groups']} | {r['n_differs']} | {r['n_dist']} |")
    for (ckey, gkey) in (("tuple", "genre"),):
        aux = primary[(ckey, gkey)]
        n_cell, k_cell, cre, gl = aux["n_cell"], aux["k_cell"], aux["cre"], aux["glist"]
        K, G = n_cell.shape
        ctr = k_cell / n_cell
        lo, hi = wilson(k_cell, n_cell)
        kst, rt = raw_best_table(n_cell, k_cell)
        md.append(f"\n### 4. Raw counts and CTRs, top creatives x genre (tuple definition, pooled over surfaces; unadjusted, so creative and genre mix are confounded here)\n")
        md.append("Creative index: " + "; ".join(f"#{i+1} = `{short(cr)}` (n={int(n_cell[i].sum()):,})" for i, cr in enumerate(cre)) + "\n")
        md.append(f"| genre | n | CTR | raw-best creative | its CTR [95% CI] | overall raw-best creative (#{kst + 1}) CTR [95% CI] | best differs / gap significant |")
        md.append("|---|---|---|---|---|---|---|")
        for gi in range(G):
            r = rt[rt.g == gi].iloc[0]; b = int(r.best)
            md.append(f"| {gl[gi]} | {int(n_cell[:, gi].sum()):,} | {k_cell[:, gi].sum() / n_cell[:, gi].sum():.3f} | #{b+1} | {ctr[b,gi]:.3f} [{lo[b,gi]:.3f}, {hi[b,gi]:.3f}] | "
                      f"{ctr[kst,gi]:.3f} [{lo[kst,gi]:.3f}, {hi[kst,gi]:.3f}] | {'yes' if r.differs else 'no'} / {'yes' if r.dist else 'no'} |")
        md.append("\nRaw CTR grid, top 8 creatives x genre (n / CTR):\n")
        md.append("| creative | " + " | ".join(gl) + " |"); md.append("|---|" + "---|" * G)
        for i in range(min(8, K)):
            md.append(f"| #{i+1} | " + " | ".join(f"{int(n_cell[i,gi]):,} / {ctr[i,gi]:.3f}" for gi in range(G)) + " |")
    # biggest within-surface detail: look definition, top used surface
    for (ckey, gkey) in (("look", "genre"), ("tuple", "genre")):
        T, c_, parts = strat[(ckey, gkey)]
        P = parts[0]
        r, n_cell, ks, tb = P["obs"]
        k_cell = np.zeros_like(n_cell); np.add.at(k_cell, (P["c"], P["g"]), P["y"])
        ctr = k_cell / np.maximum(n_cell, 1); lo, hi = wilson(k_cell, np.maximum(n_cell, 1))
        md.append(f"\n### 5. Within the biggest surface ({P['surface']}, {P['rows']:,} rows, {P['K']} {ckey} creatives): adjusted best creative per genre vs overall best\n")
        md.append(f"Creatives: " + "; ".join(f"#{i+1} = `{short(cr) if ckey == 'tuple' else cr}` (n={int(n_cell[i].sum()):,})" for i, cr in enumerate(P["cre"])) + f".  Overall best = #{ks+1}\n")
        md.append("| genre | n | CTR | adjusted best | log-odds gain over overall best [95% CI] | z | raw CTR of best [95% CI] | raw CTR of overall best [95% CI] |")
        md.append("|---|---|---|---|---|---|---|---|")
        for gi in range(P["G"]):
            row = tb[tb.g == gi]
            if row.empty:
                continue
            row = row.iloc[0]; b = int(row.best)
            adj = f"#{b+1}" if row.differs else "= overall"
            ci = f"{row['diff']:+.3f} [{row['diff']-1.96*row.se:+.3f}, {row['diff']+1.96*row.se:+.3f}]" if row.differs else "-"
            md.append(f"| {P['glist'][gi]} | {int(n_cell[:, gi].sum()):,} | {k_cell[:, gi].sum()/n_cell[:, gi].sum():.3f} | {adj} | {ci} | {'%.2f' % row.z if row.differs else '-'} | "
                      f"{ctr[b,gi]:.3f} [{lo[b,gi]:.3f}, {hi[b,gi]:.3f}] | {ctr[ks,gi]:.3f} [{lo[ks,gi]:.3f}, {hi[ks,gi]:.3f}] |")
    md.append("\n### 6. Held-out value of tailoring (within surface; pick each group's best creative on half the characters, score on the other half vs the half-A overall best; 10 random character splits x 2 directions)\n")
    md.append("| creative def | group | surfaces | in-sample gain (log-odds / CTR pp) | held-out gain log-odds (mean; SD across evals; mean Wald SE) | held-out gain CTR pp (mean; SD) | share of evals with gain > 0 |")
    md.append("|---|---|---|---|---|---|---|")
    for ckey in ("tuple", "look"):
        for gkey in ("genre", "safety_tier"):
            t = results[f"gain_{ckey}_{gkey}"]
            md.append(f"| {ckey} | {gkey} | {t['n_surfaces']} | {t['in_sample_logodds']:+.3f} / {t['in_sample_ctr_pp']:+.2f} | {t['held_out_logodds']:+.4f}; {t['held_out_logodds_sd']:.4f}; {t['wald_se_logodds']:.4f} | "
                      f"{t['held_out_ctr_pp']:+.3f}; {t['held_out_ctr_pp_sd']:.3f} | {t['frac_positive']:.2f} |")
    md.append("\n### 6b. Out-of-sample log-likelihood: does the creative x group model beat the additive one on held-out characters? (pooled primary design, 10 random character splits x 2 directions)\n")
    md.append("| creative def | group | held-out LL per impression, additive | ... with interaction | gain per 10k impressions (mean; SD) | share of evals where interaction wins |")
    md.append("|---|---|---|---|---|---|")
    for (ckey, gkey) in primary:
        k_ = f"heldout_ll_{ckey}_{gkey}"
        if k_ in results:
            t = results[k_]
            md.append(f"| {ckey} | {gkey} | {t['ll0']:.5f} | {t['ll1']:.5f} | {t['gain_per_10k_imps']:+.2f} ({1e4*t['sd']:.2f}) | {t['frac_positive']:.2f} |")
    md.append("\n### 7. Power: planted interaction (+delta log-odds on one random creative per genre), tuple x genre, pooled primary design, simulated clicks from the fitted null (independent impressions), 8 reps per delta\n")
    md.append("| delta (log-odds) | approx. CTR pp at 17% base | LR test reject rate (alpha 0.05) | mean excess LR stat over df |")
    md.append("|---|---|---|---|")
    for d, v in results["power_tuple_genre"].items():
        md.append(f"| {d} | {float(d)*0.17*0.83*100:.2f} | {v['reject_rate']:.2f} | {v['mean_excess_stat']:.1f} |")
    (OUT / "results_a.md").write_text("\n".join(md) + "\n")
    print("done", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
