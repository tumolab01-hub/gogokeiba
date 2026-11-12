#!/usr/bin/env python3
"""Kaggle「JRA日本中央競馬会 Horse Racing Dataset」(race_result.csv) を使った単勝の検証。

1) 人気別・オッズ帯別の的中率と回収率
2) 勝率予想モデルを過去データで学習し、学習に使っていない期間で
   「期待値（勝率×オッズ）が基準以上の馬だけ買う」場合の回収率を測る
結果は docs/backtest.html に出力する。
usage: python backtest.py <race_result.csv> [テスト開始年=2015]
"""
import html
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

OUT = pathlib.Path(__file__).parent / "docs"


def col(df, *names):
    for n in names:
        if n in df.columns:
            return n
    return None


def load(path):
    df = pd.read_csv(path, low_memory=False, encoding_errors="replace")
    df.columns = [c.split("/")[0].strip() for c in df.columns]  # 「列名/英訳」形式にも対応
    c = {k: col(df, *v) for k, v in {
        "date": ("レース日付",), "rid": ("レースID",), "venue": ("競馬場名",), "surf": ("芝・ダート区分",),
        "dist": ("距離(m)",), "going": ("馬場状態1",), "fp": ("着順",), "bk": ("枠番",), "pp": ("馬番",),
        "name": ("馬名",), "sex": ("性別",), "age": ("馬齢",), "kg": ("斤量",), "jockey": ("騎手",),
        "l3f": ("上り",), "odds": ("単勝",), "pop": ("人気",), "bw": ("馬体重",),
        "bwd": ("場体重増減", "馬体重増減"), "trainer": ("調教師",)}.items()}
    need = ["date", "rid", "fp", "name", "odds", "jockey", "trainer", "dist", "surf"]
    miss = [k for k in need if not c[k]]
    if miss:
        raise SystemExit(f"必要な列が見つかりません: {miss}\n列名: {list(df.columns)[:60]}")
    d = pd.DataFrame({k: df[v] for k, v in c.items() if v})
    d["date"] = pd.to_datetime(d["date"].astype(str).str[:10].str.replace("/", "-"), errors="coerce")
    for k in ("fp", "odds", "dist", "bk", "pp", "age", "kg", "l3f", "pop", "bw", "bwd"):
        if k in d:
            d[k] = pd.to_numeric(d[k], errors="coerce")
    d = d[d["date"].notna() & d["surf"].astype(str).str.contains("芝|ダ")].copy()  # 障害は除く
    if d["odds"].median() > 100:  # 円表記なら倍率に直す
        d["odds"] = d["odds"] / 100
    d = d[d["odds"] > 0].copy()
    return d.sort_values(["date", "rid", "pp" if "pp" in d else "rid"]).reset_index(drop=True)


def features(d):
    d["win"] = (d["fp"] == 1).astype(int)
    d["field"] = d.groupby("rid")["name"].transform("size")
    g = d.groupby("name", sort=False)
    d["prev1"] = g["fp"].shift(1)
    d["prev2"] = g["fp"].shift(2)
    d["prev3"] = g["fp"].shift(3)
    d["prev_avg3"] = pd.concat([d["prev1"], d["prev2"], d["prev3"]], axis=1).mean(axis=1)
    if "l3f" in d:
        d["prev_l3f"] = g["l3f"].shift(1)
    if "pop" in d:
        d["prev_pop"] = g["pop"].shift(1)
    d["rest"] = (d["date"] - g["date"].shift(1)).dt.days
    d["prev_dist"] = g["dist"].shift(1)
    d["dist_diff"] = d["dist"] - d["prev_dist"]
    d["n_runs"] = g.cumcount()
    d["n_wins"] = g["win"].cumsum() - d["win"]
    d["horse_wr"] = d["n_wins"] / d["n_runs"].replace(0, np.nan)
    for key in ("jockey", "trainer"):  # その時点より前の成績だけを使う（未来の情報を混ぜない）
        gg = d.groupby(key, sort=False)
        runs = gg.cumcount()
        wins = gg["win"].cumsum() - d["win"]
        d[f"{key}_wr"] = (wins + 7.0 * 0.08) / (runs + 7.0)  # 少数でも暴れにくい平滑化
        d[f"{key}_n"] = runs
    for k in ("sex", "surf", "venue", "going"):
        if k in d:
            d[k + "_c"] = d[k].astype("category").cat.codes
    d["logodds"] = np.log(d["odds"])
    base = ["dist", "bk", "pp", "age", "kg", "bw", "bwd", "field", "prev1", "prev2", "prev3", "prev_avg3",
            "prev_l3f", "prev_pop", "rest", "dist_diff", "n_runs", "horse_wr", "jockey_wr", "jockey_n",
            "trainer_wr", "trainer_n", "sex_c", "surf_c", "venue_c", "going_c"]
    return d, [f for f in base if f in d.columns]


def fit_predict(d, feats, test_start):
    tr = d[d["date"] < pd.Timestamp(f"{test_start}-01-01")]
    te = d[d["date"] >= pd.Timestamp(f"{test_start}-01-01")].copy()
    for label, fs in (("A", feats), ("B", feats + ["logodds"])):
        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                           early_stopping=False, random_state=0)
        m.fit(tr[fs], tr["win"])
        p = m.predict_proba(te[fs])[:, 1]
        te["p_" + label] = p / pd.Series(p, index=te.index).groupby(te["rid"]).transform("sum")  # レース内で合計1に
    return tr, te


def tbl(df):
    return '<div class="wrap">' + df.to_html(index=False, border=0, classes="t", float_format=lambda x: f"{x:,.1f}") + "</div>"


def roi_row(sub):
    n = len(sub)
    if n == 0:
        return dict(買った頭数=0, 的中率=np.nan, 回収率=np.nan, 下限=np.nan, 上限=np.nan)
    r = np.where(sub["win"] == 1, sub["odds"], 0.0)
    m, se = r.mean(), r.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return dict(買った頭数=n, 的中率=sub["win"].mean() * 100, 回収率=m * 100, 下限=(m - 1.96 * se) * 100, 上限=(m + 1.96 * se) * 100)


def report(d, te, test_start, nfeat):
    d2 = d[d["pop"].between(1, 18)] if "pop" in d else d
    pop = pd.DataFrame([{"人気": int(k), **roi_row(v)} for k, v in d2.groupby("pop")]) if "pop" in d else pd.DataFrame()
    bands = [0, 2, 3, 5, 7, 10, 15, 20, 30, 50, 100, 1e9]
    d["band"] = pd.cut(d["odds"], bands, right=False)
    band = pd.DataFrame([{"オッズ帯": str(k), **roi_row(v), "オッズ由来の勝率": (1 / v["odds"]).mean() * 100} for k, v in d.groupby("band", observed=True)])
    rows = []
    for label, name in (("A", "A：オッズを使わないモデル"), ("B", "B：オッズも使うモデル")):
        for th in (1.0, 1.1, 1.2, 1.3, 1.5, 2.0):
            sub = te[te["p_" + label] * te["odds"] >= th]
            rows.append({"モデル": name, "期待値の基準": f"{th:.1f}以上", **roi_row(sub)})
    ev = pd.DataFrame(rows)
    yr = []
    for label in ("A", "B"):
        sub = te[te["p_" + label] * te["odds"] >= 1.2]
        for y, v in sub.groupby(sub["date"].dt.year):
            yr.append({"モデル": label, "年": int(y), **roi_row(v)})
    yr = pd.DataFrame(yr)
    allbet = roi_row(te)
    css = "body{margin:0 auto;max-width:860px;font:14px/1.7 sans-serif;background:#f1f4f1;color:#1d2421;padding:0 10px 20px}h1{background:#1f5f3f;color:#fff;font-size:18px;margin:0 -10px 12px;padding:12px 14px}h2{font-size:16px;border-left:4px solid #e8731a;padding-left:8px;margin:20px 0 6px}.wrap{overflow-x:auto;background:#fff;border:1px solid #ccd6cf}table.t{border-collapse:collapse;width:100%;white-space:nowrap}.t th,.t td{border-bottom:1px solid #ccd6cf;padding:5px 9px;text-align:right}.t th{background:#e8efe9;font-size:12px}.t tr:nth-child(even) td{background:#f8faf8}.note{background:#fff;border:1px solid #ccd6cf;border-left:4px solid #e8731a;padding:8px 12px;border-radius:6px}"
    html_ = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>単勝バックテスト</title><style>{css}</style>
<h1>単勝バックテスト（無料データ）</h1>
<p class="note">データ：Kaggle「JRA日本中央競馬会 Horse Racing Dataset」（CC BY 4.0、作者 takamotoki、1986〜2021年7月）。モデルの学習は{test_start}年より前のレースだけで行い、<b>{test_start}年以降の{len(te):,}頭は学習に使っていない期間</b>で評価しています。回収率の「下限〜上限」は誤差を含めた95%の範囲です。過去の結果であり、将来の的中や利益を保証しません。</p>
<h2>1. 全頭に単勝を買った場合（基準）</h2>{tbl(pd.DataFrame([{"期間": f"{test_start}年以降", **allbet}]))}
<h2>2. 人気別（全期間）</h2>{tbl(pop)}
<h2>3. オッズ帯別（全期間）</h2>{tbl(band)}
<h2>4. 期待値（勝率×オッズ）が基準以上の馬だけ買う（{test_start}年以降）</h2>{tbl(ev)}
<h2>5. 期待値1.2以上の年別</h2>{tbl(yr)}
<p>特徴量{nfeat}個。馬の過去成績・騎手と調教師のその時点までの勝率・体重・距離・枠などを使っています。</p>"""
    OUT.mkdir(exist_ok=True)
    (OUT / "backtest.html").write_text(html_, encoding="utf-8")
    print(ev.to_string(index=False))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    test_start = int(sys.argv[2]) if len(sys.argv) > 2 else 2015
    d = load(sys.argv[1])
    print(f"読み込み: {len(d):,}行 {d['date'].min():%Y-%m-%d}〜{d['date'].max():%Y-%m-%d}")
    d, feats = features(d)
    tr, te = fit_predict(d, feats, test_start)
    print(f"学習{len(tr):,} テスト{len(te):,}")
    report(d, te, test_start, len(feats))


if __name__ == "__main__":
    main()
