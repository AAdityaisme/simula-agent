"""Shared loading + a small sparse logistic IRLS (statsmodels is not in the repo venv)."""
import sys
sys.path.insert(0, "/Users/aadi/simula-ctr")
import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.stats import chi2, norm
from simula.data import load, add_flags

DATA = "/Users/aadi/Desktop/Simula/ml-takehome/data"
CAND = ["banner_pos", "C14", "C15", "C16", "C17", "C18", "C19", "C20", "C21"]


CACHE = __import__("pathlib").Path(__file__).parent / "_cache.parquet"   # deleted at the end of the spike
A_COLS = ["click", "hour", "day", "hour_of_day", "surface", "character_id", "genre", "safety_tier", "tuple", "look", "C14"]


def prep(cache=False):
    if cache and CACHE.exists():
        return pd.read_parquet(CACHE)
    df = _prep()
    if cache:
        df[A_COLS].to_parquet(CACHE)
        return df[A_COLS]
    return df


CACHE_FULL = __import__("pathlib").Path(__file__).parent / "_cache_full.parquet"   # deleted at the end of the spike


def prep_full():
    if CACHE_FULL.exists():
        return pd.read_parquet(CACHE_FULL)
    df = _prep()
    df.to_parquet(CACHE_FULL)
    return df


def _prep():
    df = add_flags(load(DATA))
    df["tuple"] = df[CAND].astype(str).agg("|".join, axis=1)
    df["look"] = df[[c for c in CAND if c != "C14"]].astype(str).agg("|".join, axis=1)
    df["surface"] = np.where(df["is_site_side"], "site:" + df["site_id"], "app:" + df["app_id"])
    return df


def wilson(k, n, z=1.96):
    k, n = np.asarray(k, float), np.asarray(n, float)
    p = k / np.maximum(n, 1)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def onehot(codes, drop_first=True):
    """Sparse one-hot of integer codes 0..L-1 (-1 = no column)."""
    codes = np.asarray(codes)
    L = codes.max() + 1
    rows = np.arange(len(codes))
    m = codes >= 0
    X = sp.csr_matrix((np.ones(m.sum()), (rows[m], codes[m])), shape=(len(codes), L))
    return X[:, 1:] if drop_first else X


def irls(X, y, ridge=1e-8, iters=40, tol=1e-9):
    """Logistic MLE by Newton. Returns beta, loglik, inverse Hessian (for Wald contrasts)."""
    X = X.tocsr()
    XT = X.T.tocsr()
    p_dim = X.shape[1]
    beta = np.zeros(p_dim)
    eta = np.zeros(X.shape[0])
    ll_old = -np.inf
    for _ in range(iters):
        mu = 1 / (1 + np.exp(-eta))
        w = mu * (1 - mu)
        H = (XT @ X.multiply(w[:, None]).tocsr()).toarray() + ridge * np.eye(p_dim)
        g = XT @ (y - mu)
        step = np.linalg.solve(H, g)
        t = 1.0
        while True:
            b_new = beta + t * step
            e_new = X @ b_new
            ll = float(np.sum(y * e_new - np.logaddexp(0, e_new)))
            if ll >= ll_old - 1e-12 or t < 1e-4:
                break
            t /= 2
        beta, eta = b_new, e_new
        if abs(ll - ll_old) < tol * (1 + abs(ll)):
            ll_old = ll
            break
        ll_old = ll
    mu = 1 / (1 + np.exp(-eta))
    w = mu * (1 - mu)
    H = (XT @ X.multiply(w[:, None]).tocsr()).toarray() + ridge * np.eye(p_dim)
    return beta, ll_old, H


def rank_of(H, ridge=1e-8):
    """Numeric rank of the (unridged) information matrix; needed because nuisance blocks can overlap."""
    ev = np.linalg.eigvalsh(H - ridge * np.eye(len(H)))
    return int((ev > 1e-7 * ev.max()).sum())


def lr_test(ll0, ll1, rank0, rank1):
    stat = 2 * (ll1 - ll0)
    dof = rank1 - rank0
    return stat, dof, float(chi2.sf(stat, dof))


def null_basis(H, ridge=1e-8):
    """Orthonormal basis (p x m) of the null space of the information matrix: directions the data cannot identify."""
    ev, vec = np.linalg.eigh(H - ridge * np.eye(len(H)))
    return vec[:, ev <= 1e-7 * ev.max()]
