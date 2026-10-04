"""Is the raw creative x genre pattern (e.g. horror x C14=21611) a creative effect or a surface x genre effect?"""
import pandas as pd, numpy as np
from lib import prep
df = prep(cache=True)
CRE = ["0|21611|320|50|2480|3|297|100111|61", "1|4687|320|50|423|2|39|100148|32", "0|21189|320|50|2424|1|161|100193|71"]
for k in (0, 1, 2):
    cre = CRE[k]
    d = df[df["tuple"] == cre]
    print("\n== creative #%d  %s  n=%d" % (k + 1, cre, len(d)))
    sv = d["surface"].value_counts()
    print("surfaces:", len(sv), "top:", (sv.head(3) / len(d)).round(3).to_dict(), "days:", d["day"].value_counts().sort_index().to_dict())
    s = sv.index[0]
    allS = df[df["surface"] == s]
    t = pd.DataFrame({
        "this_creative_ctr": d[d["surface"] == s].groupby("genre")["click"].mean(),
        "n_this": d[d["surface"] == s].groupby("genre")["click"].size(),
        "other_creatives_same_surface_ctr": allS[allS["tuple"] != cre].groupby("genre")["click"].mean(),
        "n_other": allS[allS["tuple"] != cre].groupby("genre")["click"].size(),
    }).round(3)
    print("surface", s, "share of its rows that are this creative:", round(len(d[d['surface']==s]) / len(allS), 3))
    print(t.T.to_string())

# ---- compact table for results.md: group vs the rest, on the creative's own surface, this creative vs the other creatives there
from lib import wilson
print("\n| creative | surface | group | this creative: group CTR [95% CI] (n) | this creative: rest CTR [95% CI] (n) | other creatives, same surface: group CTR [95% CI] (n) | other creatives: rest CTR [95% CI] (n) |")
print("|---|---|---|---|---|---|---|")
def cell(d, m):
    n, k = int(m.sum()), int(d.loc[m, "click"].sum()); lo, hi = wilson(k, n); return f"{k/n:.3f} [{float(lo):.3f}, {float(hi):.3f}] ({n:,})"
for k, grp in ((0, "horror"), (1, "mentor"), (2, "horror")):
    cre = CRE[k]; d = df[df["tuple"] == cre]; s = d["surface"].value_counts().index[0]
    a, o = d[d["surface"] == s], df[(df["surface"] == s) & (df["tuple"] != cre)]
    print(f"| C14={cre.split('|')[1]} | {s} | {grp} | {cell(a, a.genre == grp)} | {cell(a, a.genre != grp)} | {cell(o, o.genre == grp)} | {cell(o, o.genre != grp)} |")
