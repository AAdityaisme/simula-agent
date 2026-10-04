"""(b) Model-based: swap ONLY the character on real held-out requests and see if bundle B's top creative moves.

Run:  uv run --project /Users/aadi/simula-ctr python b_model.py [n_requests]
Writes results_b.md and b_results.json next to this file.
"""
import json, sys, time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from lib import *
from simula.data import split, load_characters
from simula.train import load_bundle, predict
from simula.features import CONTRACT

OUT = Path(__file__).parent
N_REQ = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
K_MENU = 20
R_DRAWS = 2                      # independent character draws per (genre, tier) cell
TIERS = ["sfw", "suggestive", "mature"]
rng = np.random.default_rng(20261003)
t0 = time.time()

bundle = load_bundle("/Users/aadi/simula-ctr/bundle/B")
df = prep_full()
train, test = split(df, "refit"), split(df, "test")
chars = load_characters(DATA)
chars["genre"] = chars["character_name"].str.split("_", n=1).str[0]
GENRES = sorted(chars["genre"].unique())
print("loaded", round(time.time() - t0), "s; train", len(train), "test", len(test), flush=True)

# --- fixed menu: K most common real candidate tuples in the refit (training) window
menu_counts = train["tuple"].value_counts()
menu_keys = list(menu_counts.index[:K_MENU])
menu = train.drop_duplicates("tuple").set_index("tuple").loc[menu_keys, CAND].reset_index(drop=True)

# --- requests: random held-out rows, character-owned columns replaced per scenario
req = test.sample(N_REQ, random_state=20261003).reset_index(drop=True)
N, K = len(req), len(menu)
CHAR_COLS = ["character_id", "character_name", "safety_tier", "creator_type", "created_at", "num_interactions", "genre", "character_age_days"]
imp_date = pd.to_datetime(req["day"].astype(str), format="%y%m%d")
rep = req.loc[np.repeat(np.arange(N), K)].reset_index(drop=True)          # request i repeated K times
for col in CAND:
    rep[col] = np.tile(menu[col].to_numpy(), N)


def score_with(char_rows):
    """pCTR matrix (N x K) when request i is shown with character row char_rows[i] (DataFrame, len N)."""
    f = rep
    idx = np.repeat(np.arange(N), K)
    age = (imp_date.to_numpy() - char_rows["created_at"].dt.normalize().to_numpy()) / np.timedelta64(1, "D")
    f = f.assign(
        genre=char_rows["genre"].to_numpy()[idx], safety_tier=char_rows["safety_tier"].to_numpy()[idx],
        creator_type=char_rows["creator_type"].to_numpy()[idx], character_age_days=age.astype(int)[idx],
    )
    return predict(bundle, f).reshape(N, K)


# fast character-swap path must equal the repo's own add_flags path (rank._assemble does merge + add_flags)
chk = req.head(50).copy()
swap = chars.sample(50, random_state=1).reset_index(drop=True)
slow = chk.drop(columns=[c for c in CHAR_COLS if c in chk.columns]).assign(
    character_id=swap["character_id"].to_numpy()).merge(chars, on="character_id", how="left")
slow = add_flags(slow)
fast_age = ((imp_date.head(50).to_numpy() - swap["created_at"].dt.normalize().to_numpy()) / np.timedelta64(1, "D")).astype(int)
assert (slow["character_age_days"].to_numpy() == fast_age).all() and (slow["genre"].to_numpy() == swap["genre"].to_numpy()).all()

# --- actual character baseline
actual = req[["genre", "safety_tier", "creator_type", "created_at"]]
S_act = score_with(actual)

# --- character swaps: each (genre, tier) cell, R independent draws, one random character per request
cells = [(g, t) for g in GENRES for t in TIERS]
pools = {c: chars[(chars.genre == c[0]) & (chars.safety_tier == c[1])].reset_index(drop=True) for c in cells}
print({c: len(p) for c, p in list(pools.items())[:4]}, "min pool", min(len(p) for p in pools.values()), flush=True)
S_cell = np.zeros((len(cells), R_DRAWS, N, K))
for ci, c in enumerate(cells):
    for r in range(R_DRAWS):
        pick = pools[c].iloc[rng.integers(0, len(pools[c]), N)].reset_index(drop=True)
        S_cell[ci, r] = score_with(pick)
print("cells scored", round(time.time() - t0), "s", flush=True)

# --- feature-only overrides (hold tier/creator/age fixed, change one character feature)
S_gonly = np.zeros((len(GENRES), N, K)); S_tonly = np.zeros((len(TIERS), N, K))
for gi, g in enumerate(GENRES):
    S_gonly[gi] = score_with(actual.assign(genre=g))
for ti, t in enumerate(TIERS):
    S_tonly[ti] = score_with(actual.assign(safety_tier=t))

top = lambda S: S.argmax(-1)
logit = lambda p: np.log(p / (1 - p))
cell_idx = {c: i for i, c in enumerate(cells)}
act_g, act_t = req["genre"].to_numpy(), req["safety_tier"].to_numpy()
res = dict(n_requests=N, K_menu=K, menu=[dict(tuple=k, train_n=int(menu_counts[k])) for k in menu_keys])


def pair_stats(A, B, ref, kmax):
    """A, B: N x K score matrices under two characters; ref: scores under the true character for regret."""
    A, B, ref = A[:, :kmax], B[:, :kmax], ref[:, :kmax]
    ta, tb = A.argmax(1), B.argmax(1)
    changed = ta != tb
    ra, rb = rankdata(A, axis=1), rankdata(B, axis=1)
    ra, rb = ra - ra.mean(1, keepdims=True), rb - rb.mean(1, keepdims=True)
    rho = (ra * rb).sum(1) / np.sqrt((ra ** 2).sum(1) * (rb ** 2).sum(1))
    reg = (ref[np.arange(len(A)), ta] - ref[np.arange(len(A)), tb]) / ref[np.arange(len(A)), ta]   # loss from using B's pick under A's character
    return changed, rho, reg


# ---------- headline pairs, K = 20 and sensitivity K = 5, 10 ----------
def run(kmax, nsub=None):
    sel = slice(None) if nsub is None else slice(0, nsub)
    S0 = S_act[sel]
    out = {}
    # (1) actual -> other genre (any tier): all 27 other cells, draw 0
    ch, rho, reg = [], [], []
    for (g, t), ci in cell_idx.items():
        m = act_g[sel] != g
        c, r, g_ = pair_stats(S0[m], S_cell[ci, 0][sel][m], S0[m], kmax)
        ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["actual_to_other_genre"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    # (2) control: actual -> a different random character of the SAME genre and SAME tier
    ch, rho, reg = [], [], []
    for (g, t), ci in cell_idx.items():
        m = (act_g[sel] == g) & (act_t[sel] == t)
        c, r, g_ = pair_stats(S0[m], S_cell[ci, 0][sel][m], S0[m], kmax)
        ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["actual_to_same_cell_other_char"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    # (3) same tier, other genre (cell vs cell, draw 0 vs draw 0) -- pure genre contrast among real characters
    ch, rho, reg = [], [], []
    for t in TIERS:
        for ga, gb in [(a, b) for a in range(len(GENRES)) for b in range(a + 1, len(GENRES))]:
            A = S_cell[cell_idx[(GENRES[ga], t)], 0][sel]; B = S_cell[cell_idx[(GENRES[gb], t)], 1][sel]
            c, r, g_ = pair_stats(A, B, A, kmax)
            ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["cell_pairs_same_tier_other_genre"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    # (4) control for (3): same genre AND tier, two independent character draws
    ch, rho, reg = [], [], []
    for ci in range(len(cells)):
        A = S_cell[ci, 0][sel]; B = S_cell[ci, 1][sel]
        c, r, g_ = pair_stats(A, B, A, kmax)
        ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["cell_pairs_same_cell_two_chars"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    # (5) same genre, other tier (cell pairs)
    ch, rho, reg = [], [], []
    for g in GENRES:
        for ta, tb in [(0, 1), (0, 2), (1, 2)]:
            A = S_cell[cell_idx[(g, TIERS[ta])], 0][sel]; B = S_cell[cell_idx[(g, TIERS[tb])], 1][sel]
            c, r, g_ = pair_stats(A, B, A, kmax)
            ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["cell_pairs_same_genre_other_tier"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    # (6) feature-only genre override: pairs of genres, everything else fixed
    ch, rho, reg = [], [], []
    for ga in range(len(GENRES)):
        for gb in range(ga + 1, len(GENRES)):
            c, r, g_ = pair_stats(S_gonly[ga][sel], S_gonly[gb][sel], S_gonly[ga][sel], kmax)
            ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["genre_feature_only_pairs"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    ch, rho, reg = [], [], []
    for ta in range(3):
        for tb in range(ta + 1, 3):
            c, r, g_ = pair_stats(S_tonly[ta][sel], S_tonly[tb][sel], S_tonly[ta][sel], kmax)
            ch.append(c); rho.append(r); reg.append(g_)
    ch, rho, reg = map(np.concatenate, (ch, rho, reg))
    out["tier_feature_only_pairs"] = (ch.mean(), np.nanmean(rho), np.nanmedian(rho), reg.mean(), reg[ch].mean() if ch.any() else 0.0, len(ch), float((reg >= 0.02).mean()))
    return out


res["by_kmax"] = {}
for kmax in (5, 10, 20):
    res["by_kmax"][kmax] = run(kmax)
    print("K", kmax, {k: [round(float(x), 4) for x in v] for k, v in res["by_kmax"][kmax].items()}, flush=True)

# ---------- how many requests would see the SAME top creative under every genre? ----------
def all_same_top(S_stack):                 # S_stack: (n_variants, N, K)
    t = S_stack.argmax(-1)
    return float((t == t[0]).all(0).mean()), float(np.mean([len(set(t[:, i])) for i in range(t.shape[1])]))

res["all_genres_same_top"] = {
    "feature_only_genre": all_same_top(S_gonly),
    "full_character_swap_draw0_sfw": all_same_top(np.stack([S_cell[cell_idx[(g, "sfw")], 0] for g in GENRES])),
}
# distinct tops by genre: tier-matched (sfw) real-character swap vs two draws of ONE genre
t_g = np.stack([S_cell[cell_idx[(g, "sfw")], 0] for g in GENRES]).argmax(-1)           # genres x N
t_ctl = np.stack([S_cell[cell_idx[("horror", "sfw")], r] for r in range(R_DRAWS)]).argmax(-1)
res["distinct_tops_per_request_genre_vs_control"] = (float(np.mean([len(set(t_g[:, i])) for i in range(N)])), float(np.mean([len(set(t_ctl[:, i])) for i in range(N)])))

# ---------- decomposition on the logit scale (feature-only genre override) ----------
L = logit(S_gonly)                                  # G x N x K
# per-request double centering over (genre, creative)
inter = L - L.mean(2, keepdims=True) - L.mean(0, keepdims=True) + L.mean((0, 2), keepdims=True)
sd_inter = float(np.sqrt((inter ** 2).mean()))
sd_creative = float(np.sqrt(((L.mean(0, keepdims=True) - L.mean((0, 2), keepdims=True)) ** 2).mean() * 1.0))
sd_genre = float(np.sqrt(((L.mean(2, keepdims=True) - L.mean((0, 2), keepdims=True)) ** 2).mean()))
res["logit_decomp_genre_feature_only"] = dict(sd_creative_main=sd_creative, sd_genre_main=sd_genre, sd_creative_x_genre=sd_inter,
                                              ratio_inter_to_creative=sd_inter / sd_creative)
# same for real-character swaps within the sfw tier, draw 0 (includes age/creator noise) and the control (draw 1 minus draw 0)
Lc = logit(np.stack([S_cell[cell_idx[(g, "sfw")], 0] for g in GENRES]))
ic = Lc - Lc.mean(2, keepdims=True) - Lc.mean(0, keepdims=True) + Lc.mean((0, 2), keepdims=True)
res["logit_decomp_sfw_character_swap"] = dict(sd_creative_main=float(np.sqrt(((Lc.mean(0, keepdims=True) - Lc.mean((0, 2), keepdims=True)) ** 2).mean())),
                                              sd_creative_x_genre=float(np.sqrt((ic ** 2).mean())))
# noise floor of that same statistic: draws of the SAME cell (horror/sfw) instead of different genres
Ln = logit(np.stack([S_cell[cell_idx[("horror", "sfw")], r] for r in range(R_DRAWS)]))
inn = Ln - Ln.mean(2, keepdims=True) - Ln.mean(0, keepdims=True) + Ln.mean((0, 2), keepdims=True)
res["logit_decomp_same_cell_noise_floor"] = dict(sd_creative_x_draw=float(np.sqrt((inn ** 2).mean())))
# how big is the creative spread itself
res["creative_logit_spread_actual_char"] = dict(sd_across_creatives=float(logit(S_act).std(1).mean()),
                                                mean_pctr_top=float(S_act.max(1).mean()), mean_pctr_median_creative=float(np.median(S_act, 1).mean()),
                                                mean_rel_gap_top1_top2=float(np.mean((np.sort(S_act, 1)[:, -1] - np.sort(S_act, 1)[:, -2]) / np.sort(S_act, 1)[:, -1])),
                                                share_gap_lt_2pct=float(np.mean((np.sort(S_act, 1)[:, -1] - np.sort(S_act, 1)[:, -2]) / np.sort(S_act, 1)[:, -1] < 0.02)))

# ---------- per-genre: how often each creative wins (feature-only) ----------
wins = np.zeros((len(GENRES), K))
for gi in range(len(GENRES)):
    t = S_gonly[gi].argmax(1)
    wins[gi] = np.bincount(t, minlength=K) / N
res["win_share_by_genre_feature_only"] = {g: [round(float(x), 3) for x in wins[i]] for i, g in enumerate(GENRES)}
res["win_share_by_tier_feature_only"] = {t: [round(float(x), 3) for x in (np.bincount(S_tonly[i].argmax(1), minlength=K) / N)] for i, t in enumerate(TIERS)}

# ---------- pair matrices (feature-only overrides, K = 20) and which characters the model treats as identical ----------
def pair_matrix(S):
    n = len(S)
    ch = np.zeros((n, n)); rh = np.ones((n, n))
    for a in range(n):
        for b in range(n):
            if a != b:
                c_, r_, _ = pair_stats(S[a], S[b], S[a], K)
                ch[a, b] = c_.mean(); rh[a, b] = np.nanmean(r_)
    return ch, rh

def classes(S):
    cl = []
    for i in range(len(S)):
        for c in cl:
            if np.allclose(S[c[0]], S[i], rtol=0, atol=1e-12):
                c.append(i); break
        else:
            cl.append([i])
    return cl

chg, rhg = pair_matrix(S_gonly)
cht, rht = pair_matrix(S_tonly)
cg = classes(S_gonly); ct = classes(S_tonly)
res["genre_pair_top_change"] = {"genres": GENRES, "matrix": np.round(chg, 3).tolist(), "rho": np.round(rhg, 3).tolist()}
res["tier_pair_top_change"] = {"tiers": TIERS, "matrix": np.round(cht, 3).tolist(), "rho": np.round(rht, 3).tolist()}
res["model_genre_classes"] = [[GENRES[i] for i in c] for c in cg]
res["model_tier_classes"] = [[TIERS[i] for i in c] for c in ct]
# among genre pairs the model actually separates (different class)
sep = [(a, b) for a in range(len(GENRES)) for b in range(a + 1, len(GENRES)) if not any(a in c and b in c for c in cg)]
res["genre_pairs_model_separates"] = dict(n_pairs=len(sep), n_pairs_total=len(GENRES) * (len(GENRES) - 1) // 2,
                                          mean_top_change=float(np.mean([chg[a, b] for a, b in sep])), mean_rho=float(np.mean([rhg[a, b] for a, b in sep])))
Lt = logit(S_tonly)
it = Lt - Lt.mean(2, keepdims=True) - Lt.mean(0, keepdims=True) + Lt.mean((0, 2), keepdims=True)
res["logit_decomp_tier_feature_only"] = dict(sd_creative_main=float(np.sqrt(((Lt.mean(0, keepdims=True) - Lt.mean((0, 2), keepdims=True)) ** 2).mean())),
                                             sd_tier_main=float(np.sqrt(((Lt.mean(2, keepdims=True) - Lt.mean((0, 2), keepdims=True)) ** 2).mean())),
                                             sd_creative_x_tier=float(np.sqrt((it ** 2).mean())))

# ---------- same flips, but counting only changes of the creative LOOK (tuple without C14): ten of the 20 menu items are one ad under 10 C14 ids ----------
look_id = pd.factorize(menu[[c for c in CAND if c != "C14"]].astype(str).agg("|".join, axis=1))[0]
res["menu_n_distinct_looks"] = int(look_id.max() + 1)

def look_change(A, B):
    return look_id[A.argmax(1)] != look_id[B.argmax(1)]

gl = [look_change(S_gonly[a], S_gonly[b]).mean() for a, b in sep]
tl = [look_change(S_tonly[a], S_tonly[b]).mean() for a in range(3) for b in range(a + 1, 3)]
res["look_level_flips"] = dict(
    genre_pairs_model_separates_mean=float(np.mean(gl)), genre_pairs_max=float(np.max(gl)), genre_pairs_min=float(np.min(gl)),
    tier_pairs_mean=float(np.mean(tl)), mature_vs_sfw=float(look_change(S_tonly[0], S_tonly[2]).mean()),
    genre_pair_matrix=[[round(float(look_change(S_gonly[a], S_gonly[b]).mean()), 3) for b in range(len(GENRES))] for a in range(len(GENRES))],
    # real-character swaps: actual character -> another genre of the same tier / another tier of the same genre
    actual_to_other_genre_same_tier=float(np.mean(np.concatenate([look_change(S_act[(act_g != g) & (act_t == t)], S_cell[cell_idx[(g, t)], 0][(act_g != g) & (act_t == t)]) for g in GENRES for t in TIERS]))),
    actual_to_other_tier_same_genre=float(np.mean(np.concatenate([look_change(S_act[(act_g == g) & (act_t != t)], S_cell[cell_idx[(g, t)], 0][(act_g == g) & (act_t != t)]) for g in GENRES for t in TIERS]))),
    all_genres_same_top_look=float((look_id[S_gonly.argmax(-1)] == look_id[S_gonly.argmax(-1)][0]).all(0).mean()),
)

# ---------- out-of-time check of the model's creative-specific character effects, on ALL held-out test rows (actual character, actual creative) ----------
def heterogeneity_check(rows_mask_fn, make_ref, name, min_cell=1):
    """h_i = model's effect of the character feature on logit pCTR for row i's own context and creative, centred within the group.
    Split h into surface-level, creative-within-surface and residual (device/hour/...) parts, replace all three by 0 in the offset,
    then fit click ~ offset + a_s*h_surface + a_c*h_creative + a_r*h_resid. a = 1: that part of the model's heterogeneity is real
    out of time; a = 0: it is not."""
    t = test.reset_index(drop=True)
    p_act = predict(bundle, t); p_ref = predict(bundle, make_ref(t))
    h = logit(p_act) - logit(p_ref)
    m = rows_mask_fn(t).to_numpy()
    if min_cell > 1:                     # keep rows whose (surface, creative) cell has >= min_cell test rows, so cell means are not just the row itself
        size = t.groupby(["surface", "tuple"])["click"].transform("size").to_numpy()
        m = m & (size >= min_cell)
    d = pd.DataFrame({"h": h[m], "s": t.loc[m, "surface"].to_numpy(), "k": t.loc[m, "tuple"].to_numpy()})
    z = d["h"] - d["h"].mean()
    zs = z.groupby(d["s"]).transform("mean")
    zk = z.groupby([d["s"], d["k"]]).transform("mean")
    parts = np.column_stack([zs, zk - zs, z - zk])
    Z = np.zeros((len(t), 3)); Z[m] = parts
    off = logit(p_act) - Z.sum(1)
    y = t["click"].to_numpy(float)
    a = np.zeros(3)
    for _ in range(30):
        mu = 1 / (1 + np.exp(-(off + Z @ a)))
        info = (Z * (mu * (1 - mu))[:, None]).T @ Z
        a += np.linalg.solve(info, Z.T @ (y - mu))
    se = np.sqrt(np.diag(np.linalg.inv(info)))
    out = dict(name=name, n_rows=int(m.sum()), n_surfaces=int(d["s"].nunique()), n_surface_creative_cells=int(d.groupby(["s", "k"]).ngroups))
    for i, comp in enumerate(["surface_level", "creative_within_surface", "residual_device_hour_etc"]):
        out[comp] = dict(sd=float(parts[:, i].std()), a=float(a[i]), se=float(se[i]), ci95=[float(a[i] - 1.96 * se[i]), float(a[i] + 1.96 * se[i])])
    return out


res["heterogeneity_check_test_window"] = [
    heterogeneity_check(lambda t: t["safety_tier"].eq("mature"), lambda t: t.assign(safety_tier="sfw"), "mature rows: model's mature-vs-sfw effect, all cells"),
    heterogeneity_check(lambda t: t["genre"].isin(["horror", "romance", "mentor"]), lambda t: t.assign(genre="anime"), "horror/romance/mentor rows: effect vs 'anime' class, all cells"),
    heterogeneity_check(lambda t: t["safety_tier"].eq("mature"), lambda t: t.assign(safety_tier="sfw"), "mature rows, (surface, creative) cells with >= 30 test rows", 30),
    heterogeneity_check(lambda t: t["genre"].isin(["horror", "romance", "mentor"]), lambda t: t.assign(genre="anime"), "horror/romance/mentor rows, (surface, creative) cells with >= 30 test rows", 30),
]
print(json.dumps(res["heterogeneity_check_test_window"], indent=1), flush=True)

# ---------- request-level 95% intervals for the headline flip rates (unit = request; each request's flip share averaged over its pairs) ----------
def ci(f):
    f = np.asarray(f, float); m = f.mean(); h = 1.96 * f.std(ddof=1) / np.sqrt(len(f))
    return [float(m), float(m - h), float(m + h)]

tuple_flip = lambda A, B: A.argmax(1) != B.argmax(1)
sep_pairs = [(a, b) for a, b in sep]
other_same_tier = [(g, t) for g in GENRES for t in TIERS]
def per_request_actual_to(cond_cells, fn):
    """per request: share of target cells (satisfying cond for that request's own genre/tier) where fn(S_act, S_target) flips"""
    num = np.zeros(N); den = np.zeros(N)
    for (g, t) in cells:
        m = cond_cells(g, t)
        flip = fn(S_act, S_cell[cell_idx[(g, t)], 0])
        num += m * flip; den += m
    return num / np.maximum(den, 1)

res["flip_ci_request_level"] = {
    "genre_feature_only_tuple_level_42_separated_pairs": ci(np.mean([tuple_flip(S_gonly[a], S_gonly[b]) for a, b in sep_pairs], 0)),
    "genre_feature_only_look_level_42_separated_pairs": ci(np.mean([look_change(S_gonly[a], S_gonly[b]) for a, b in sep_pairs], 0)),
    "tier_feature_only_mature_vs_sfw_tuple_level": ci(tuple_flip(S_tonly[0], S_tonly[2])),
    "tier_feature_only_mature_vs_sfw_look_level": ci(look_change(S_tonly[0], S_tonly[2])),
    "actual_to_other_genre_same_tier_tuple_level": ci(per_request_actual_to(lambda g, t: (act_g != g) & (act_t == t), tuple_flip)),
    "actual_to_other_genre_same_tier_look_level": ci(per_request_actual_to(lambda g, t: (act_g != g) & (act_t == t), look_change)),
    "actual_to_other_tier_same_genre_look_level": ci(per_request_actual_to(lambda g, t: (act_g == g) & (act_t != t), look_change)),
    "control_same_genre_tier_other_character": ci(per_request_actual_to(lambda g, t: (act_g == g) & (act_t == t), tuple_flip)),
}

# ---------- can the model express creative x character interactions? parse the trees ----------
booster = bundle[0]
td = booster.trees_to_dataframe()
CHARF = {"genre", "safety_tier", "creator_type", "character_age_days"}
CANDF = set(CONTRACT["candidate"])
split_nodes = td[td["split_feature"].notna()].copy()
parent = td.set_index("node_index")["parent_index"].to_dict()
feat = td.set_index("node_index")["split_feature"].to_dict()
gain = td.set_index("node_index")["split_gain"].to_dict()
tree_of = td.set_index("node_index")["tree_index"].to_dict()
tot_gain = split_nodes["split_gain"].sum()
inter_gain = 0.0; inter_nodes = 0; trees_with = set()
for n in split_nodes["node_index"]:
    f = feat[n]; anc = []
    p = parent[n]
    while isinstance(p, str):
        anc.append(feat[p]); p = parent[p]
    if (f in CANDF and CHARF & set(anc)) or (f in CHARF and CANDF & set(anc)):
        inter_gain += gain[n]; inter_nodes += 1
        trees_with.add(tree_of[n])
res["trees"] = dict(n_trees=int(td["tree_index"].nunique()), n_splits=int(len(split_nodes)),
                    share_gain_on_char_x_candidate_paths=float(inter_gain / tot_gain), share_splits=float(inter_nodes / len(split_nodes)),
                    share_trees_with_such_path=float(len(trees_with) / td["tree_index"].nunique()),
                    gain_by_feature={k: float(v / tot_gain) for k, v in split_nodes.groupby("split_feature")["split_gain"].sum().sort_values(ascending=False).head(12).items()})
imp = dict(zip(booster.feature_name(), booster.feature_importance("gain")))
res["gain_share_char_features"] = {f: float(imp[f] / sum(imp.values())) for f in CHARF}

json.dump(res, open(OUT / "b_results.json", "w"), indent=1, default=float)
print(json.dumps({k: v for k, v in res.items() if k not in ("menu", "by_kmax", "win_share_by_genre_feature_only")}, indent=1, default=float))
print("done", round(time.time() - t0), "s")
