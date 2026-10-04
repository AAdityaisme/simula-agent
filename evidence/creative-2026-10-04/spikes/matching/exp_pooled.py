# Run once against an earlier a_data.pooled_test signature (inter=, three return values); does not run as committed.
import a_data as A
from lib import *
df = prep(cache=True)
for ckey in ("tuple", "look", "C14"):
    top = df[ckey].value_counts().head(20).index
    d = df[df[ckey].isin(top)]
    sv = d["surface"].value_counts()
    print(ckey, "rows", len(d), "surfaces", len(sv), "share rows in top30 surfaces", round(sv.head(30).sum()/len(d), 3), "top5", (sv.head(5)/len(d)).round(3).to_dict())
    for ts in (30, 100):
        A.TOP_SURF = ts
        dd = d[d["surface"].isin(sv.head(ts).index)]
        for gkey, ctrl in (("genre", "safety_tier"),):
            info, tab, aux = A.pooled_test(dd, ckey, gkey, ctrl, inter=True)
            print("  restrict to top", ts, "surfaces:", len(dd), "stat %.1f dof %d p %.2g" % (info["stat"], info["dof"], info["p"]))
