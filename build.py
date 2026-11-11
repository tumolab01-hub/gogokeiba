#!/usr/bin/env python3
"""races.json の各レースについて、JRA・スポーツナビから登録馬を取り込み docs/<slug>.html を生成する。"""
import datetime as dt
import html
import json
import pathlib
import re
import sys

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "docs"
UA = "Mozilla/5.0 (compatible; keiba-weekly/1.0; personal use)"
JST = dt.timezone(dt.timedelta(hours=9))


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.content, "lxml")


def norm(s):
    return re.sub(r"[\s\u3000]+", "", s or "")


def parse_regist(soup):
    """スポーツナビ特別登録: 馬名/性齢・斤量・調教師・父・母(母父)"""
    horses = []
    for tr in soup.select("table tr"):
        td = tr.find_all("td")
        if len(td) < 5:
            continue
        lines = td[0].get_text("\n", strip=True).split("\n")
        m = re.search(r"([牡牝セ])\s*(\d+)", lines[1] if len(lines) > 1 else "")
        k = re.search(r"\d+(?:\.\d+)?", td[1].get_text())
        t = td[2].get_text(" ", strip=True)
        area = "栗" if "栗東" in t else "美" if "美浦" in t else ""
        dam_text = td[4].get_text("", strip=True)
        dm = re.match(r"(.+?)[（(](.+)[)）]\s*$", dam_text)
        horses.append({
            "name": norm(lines[0]),
            "age": f"{m.group(1)}{m.group(2)}" if m else "",
            "kg": f"{float(k.group()):g}" if k else "",
            "trainer": norm(re.sub(r"栗東|美浦", "", t)) + (f"（{area}）" if area else ""),
            "sire": norm(td[3].get_text("", strip=True)),
            "dam": norm(dm.group(1)) if dm else norm(dam_text),
            "damsire": norm(dm.group(2)) if dm else "",
        })
    return horses


def parse_prerating(soup):
    """JRAプレレーティング: 馬名 -> (数値, 該当レース)"""
    res = {}
    for tr in soup.select("table tr"):
        td = tr.find_all("td")
        if len(td) < 6:
            continue
        m = re.search(r"\d+", td[4].get_text())
        race = td[5].get_text(" ", strip=True)
        parts = [p for p in re.split(r"[、,]", race) if p]
        if len(parts) > 1:
            race = parts[0] + "ほか"
        res[norm(td[0].get_text())] = (int(m.group()) if m else 0, race if m else "")
    return res


def parse_rank(soup):
    """JRAレーティング順位のお知らせ: 馬名 -> 順位"""
    res = {}
    for tr in soup.select("table tr"):
        td = tr.find_all("td")
        if len(td) >= 3 and td[0].get_text(strip=True).isdigit():
            res[norm(td[1].get_text())] = int(td[0].get_text(strip=True))
    return res


def parse_analysis(soup):
    ps = [p.get_text(" ", strip=True) for p in soup.select("p")]
    return " ".join([p for p in ps if "過去10年" in p and len(p) > 40][:2])


def js_str(s):
    return "'" + s.replace("\\", "").replace("'", "’") + "'"


def build_race(cfg, today):
    horses = parse_regist(fetch(cfg["sportsnavi"]))
    if len(horses) < 4:
        raise RuntimeError("スポーツナビの登録馬を取得できませんでした（ページ構造が変わった可能性）")
    pre, rank, ana = {}, {}, ""
    for key, fn, name in (("jra_prerating", parse_prerating, "pre"),
                          ("jra_rank_news", parse_rank, "rank"),
                          ("jra_analysis", parse_analysis, "ana")):
        if cfg.get(key):
            try:
                v = fn(fetch(cfg[key]))
                if name == "pre": pre = v
                elif name == "rank": rank = v
                else: ana = v
            except Exception as e:  # 任意ソースは失敗しても続行
                print(f"  [warn] {key}: {e}")
    lines = []
    for h in horses:
        r, rc = pre.get(h["name"], (0, ""))
        flag = "X" if h["name"] in cfg.get("scratched", []) else "G" if h["name"] in cfg.get("g1_winners", []) else ""
        f = [h["name"], h["age"], h["kg"], h["trainer"], h["sire"], h["dam"], h["damsire"],
             str(r or ""), rc, str(rank.get(h["name"], "")), flag]
        lines.append("  " + js_str("|".join(x.replace("'", "’").replace("|", "／") for x in f)))
    box = "<b>見どころ（JRAデータ分析より）</b><br>" + html.escape(ana) if ana else "<b>メモ</b><br>登録馬段階のデータです。"
    sc = cfg.get("scratched", [])
    if sc:
        box += "<br><b>回避</b>　" + "、".join(html.escape(x) for x in sc)
    t = (ROOT / "template.html").read_text(encoding="utf-8")
    now = dt.datetime.now(JST)
    rep = {"{{TITLE}}": html.escape(cfg["title"]), "{{EYEBROW}}": html.escape(cfg.get("eyebrow", "")),
           "{{META}}": html.escape(cfg["meta"]), "{{HIGHLIGHT}}": box,
           "{{ASOF}}": f"{now.year}年{now.month}月{now.day}日 {now:%H:%M}", "{{RAW}}": ",\n".join(lines),
           "{{KEY}}": cfg["slug"] + "-marks"}
    for k, v in rep.items():
        t = t.replace(k, v)
    (OUT / f"{cfg['slug']}.html").write_text(t, encoding="utf-8")
    return len(horses)


def write_index(races):
    items = "".join(f'<li><a href="{r["slug"]}.html">{html.escape(r["title"])}</a>　<small>{r["date"]}</small></li>'
                    for r in sorted(races, key=lambda r: r["date"], reverse=True))
    (OUT / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>重賞ボード</title><body style="font:16px/1.8 serif;max-width:640px;margin:20px auto;padding:0 14px">'
        f"<h1>重賞ボード</h1><ul>{items}</ul></body>", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    races = json.loads((ROOT / "races.json").read_text(encoding="utf-8"))
    today = dt.datetime.now(JST).date()
    ok = fail = 0
    for cfg in races:
        out = OUT / f"{cfg['slug']}.html"
        if dt.date.fromisoformat(cfg["date"]) < today - dt.timedelta(days=1) and out.exists():
            print(f"skip（開催済み）: {cfg['slug']}")
            continue
        try:
            n = build_race(cfg, today)
            print(f"ok: {cfg['slug']} {n}頭")
            ok += 1
        except Exception as e:
            print(f"[error] {cfg['slug']}: {e}")
            fail += 1
    write_index(races)
    if fail and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
