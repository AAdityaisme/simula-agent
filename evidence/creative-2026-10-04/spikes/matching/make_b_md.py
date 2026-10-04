"""Turn b_results.json into results_b.md (tables only)."""
import json
from pathlib import Path
D = Path(__file__).parent
r = json.load(open(D / "b_results.json"))
L = []
L.append(f"Requests: {r['n_requests']} random held-out (test-window) rows; menu: {r['K_menu']} most common training-window candidate tuples ({r['menu_n_distinct_looks']} distinct looks = tuples without C14; ten items are one ad under 10 C14 ids).\n")
L.append("### B1. How often does the top-scoring creative change when only the character changes?\n")
L.append("Columns: change = share of (request, character-pair) cases where argmax creative differs; rho = mean Spearman correlation of the creative score vectors; regret = mean relative pCTR loss (under the first character's own scores) from using the second character's top creative, over all cases / over changed cases; >=2% = share of cases where that loss is at least 2%.\n")
L.append("| comparison | menu K | n cases | top-1 change | mean rho (median) | regret all / if changed | share with regret >= 2% |")
L.append("|---|---|---|---|---|---|---|")
names = {
    "actual_to_other_genre": "actual character -> a random character of another genre (any tier)",
    "actual_to_same_cell_other_char": "CONTROL: actual character -> another character, same genre and tier",
    "cell_pairs_same_tier_other_genre": "character pairs: same tier, different genre",
    "cell_pairs_same_cell_two_chars": "CONTROL: two different characters, same genre and tier",
    "cell_pairs_same_genre_other_tier": "character pairs: same genre, different tier",
    "genre_feature_only_pairs": "feature-only: swap just the genre value (all genre pairs)",
    "tier_feature_only_pairs": "feature-only: swap just the safety_tier value (all tier pairs)",
}
for km in ("20", "10", "5"):
    for k, name in names.items():
        v = r["by_kmax"][km][k]
        L.append(f"| {name} | {km} | {int(v[5]):,} | {v[0]:.3f} | {v[1]:.3f} ({v[2]:.3f}) | {v[3]:.4f} / {v[4]:.4f} | {v[6]:.3f} |")
gp = r["genre_pairs_model_separates"]
_m = r["genre_pair_top_change"]["matrix"]; _n = len(_m)
_eff = [_m[a][b] for a in range(_n) for b in range(a + 1, _n) if _m[a][b] > 0.05]
L.append(f"\nWhat the model separates: by exact score it has {len(r['model_genre_classes'])} genre classes {r['model_genre_classes']} and tiers {r['model_tier_classes']}, but in top-creative terms only horror, mentor and romance differ from the other seven genres (flip rate between any two of the seven is <= 0.003). The {len(_eff)} genre pairs with a real flip rate average {sum(_eff)/len(_eff):.3f} (range {min(_eff):.2f} to {max(_eff):.2f}, K=20); averaged over all {gp['n_pairs_total']} pairs it is {gp['mean_top_change']*gp['n_pairs']/gp['n_pairs_total']:.3f}. `creator_type` and `character_age_days` have zero split gain, which is why two different characters of the same genre and tier never change anything (the control rows are exactly 0).\n")
lf = r["look_level_flips"]
L.append("Counting only changes of the creative look (not a switch among the 10 C14-id twins), K=20: genre pairs the model separates mean %.3f (range %.3f to %.3f); mature vs sfw %.3f; actual -> other genre, same tier %.3f; actual -> other tier, same genre %.3f. Share of requests where all 10 genres get the same top look: %.3f (same top tuple: %.3f).\n" % (
    lf["genre_pairs_model_separates_mean"], lf["genre_pairs_min"], lf["genre_pairs_max"], lf["mature_vs_sfw"], lf["actual_to_other_genre_same_tier"], lf["actual_to_other_tier_same_genre"], lf["all_genres_same_top_look"], r["all_genres_same_top"]["feature_only_genre"][0]))
L.append("Request-level 95% intervals (unit = request, K=20; mean [low, high]):\n")
for k, v in r["flip_ci_request_level"].items():
    L.append(f"- {k.replace('_', ' ')}: {v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]")
L.append("")
L.append("Genre-pair top-change matrix (feature-only swap, K=20, tuple level):\n")
gm = r["genre_pair_top_change"]
L.append("| | " + " | ".join(gm["genres"]) + " |"); L.append("|---|" + "---|" * len(gm["genres"]))
for g, row in zip(gm["genres"], gm["matrix"]):
    L.append(f"| {g} | " + " | ".join(f"{x:.2f}" for x in row) + " |")
tm = r["tier_pair_top_change"]
L.append("\nTier-pair top-change (feature-only, K=20): " + "; ".join(f"{tm['tiers'][a]} vs {tm['tiers'][b]} change {tm['matrix'][a][b]:.3f}, rho {tm['rho'][a][b]:.3f}" for a in range(3) for b in range(a + 1, 3)) + "\n")
L.append("### B2. Size of the interaction in the model (logit scale, feature-only swaps, per-request double-centred over character value x creative)\n")
d, t = r["logit_decomp_genre_feature_only"], r["logit_decomp_tier_feature_only"]
L.append("| character feature | SD creative main effect | SD character main effect | SD creative x character interaction | interaction / creative |")
L.append("|---|---|---|---|---|")
L.append(f"| genre | {d['sd_creative_main']:.3f} | {d['sd_genre_main']:.3f} | {d['sd_creative_x_genre']:.3f} | {d['ratio_inter_to_creative']:.2f} |")
L.append(f"| safety_tier | {t['sd_creative_main']:.3f} | {t['sd_tier_main']:.3f} | {t['sd_creative_x_tier']:.3f} | {t['sd_creative_x_tier']/t['sd_creative_main']:.2f} |")
c = r["creative_logit_spread_actual_char"]
L.append(f"\nCreative spread under the actual character: SD of logit score across the 20 creatives {c['sd_across_creatives']:.3f}; mean pCTR of top creative {c['mean_pctr_top']:.4f} vs median creative {c['mean_pctr_median_creative']:.4f}; mean relative gap top-1 to top-2 {c['mean_rel_gap_top1_top2']:.3f}; share of requests with gap < 2%: {c['share_gap_lt_2pct']:.3f}.\n")
L.append("### B3. Can the model express creative x character interactions? Tree inspection (183 trees)\n")
tr = r["trees"]
L.append(f"- Feature set B contains genre, safety_tier, creator_type, character_age_days next to banner_pos and C14-C21; GBDT trees (31 leaves, no interaction constraints) can split on both along one path, so it can express them.")
L.append(f"- {tr['share_trees_with_such_path']:.1%} of trees contain a root-to-leaf path with both a character feature and a candidate feature; {tr['share_splits']:.1%} of all splits and {tr['share_gain_on_char_x_candidate_paths']:.1%} of total split gain sit on such a path (a split counts if one of its ancestors is from the other group).")
L.append("- Share of total gain by character feature: " + ", ".join(f"{k} {v:.2%}" for k, v in sorted(r["gain_share_char_features"].items(), key=lambda kv: -kv[1])) + ". Top features by gain: " + ", ".join(f"{k} {v:.1%}" for k, v in list(tr["gain_by_feature"].items())[:8]) + ".")
L.append("- What it learned: genres split into the classes above (horror, romance, mentor separate; most others tied), tier into mature vs not. Per-genre top-creative win shares (feature-only swap) are in b_results.json.\n")
L.append("### B4. Does the model's character-effect heterogeneity hold up on held-out rows? (all test-window rows, actual character and creative)\n")
L.append("h_i = model's effect of the character feature on row i's logit pCTR (actual value vs reference: mature->sfw; horror/romance/mentor->anime class), centred in the group, split into surface-level, creative-within-surface and residual (device/hour/...) parts. Offset = model logit with that heterogeneity removed; coefficient a on each part (1 = real, 0 = not; Wald 95% CI).\n")
L.append("| subset | rows | (surface, creative) cells | part | SD of part | a | 95% CI |")
L.append("|---|---|---|---|---|---|---|")
for h in r["heterogeneity_check_test_window"]:
    for k, nm in (("surface_level", "surface level"), ("creative_within_surface", "creative within surface"), ("residual_device_hour_etc", "residual (device, hour, ...)")):
        v = h[k]
        L.append(f"| {h['name']} | {h['n_rows']:,} | {h['n_surface_creative_cells']:,} | {nm} | {v['sd']:.3f} | {v['a']:.2f} | [{v['ci95'][0]:.2f}, {v['ci95'][1]:.2f}] |")
(D / "results_b.md").write_text("\n".join(L) + "\n")
print("\n".join(L)[:3000])
