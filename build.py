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


def find_thisweek(cfg):
    """JRA『今週の注目レース』出走馬情報ページを探し、注目馬の名前一覧を返す。"""
    d = dt.date.fromisoformat(cfg["date"])
    core = re.sub(r"第\d+回\s*|[（(][^）)]*[）)]", "", cfg["title"]).strip()
    for n in (1, 2, 3):
        url = f"https://www.jra.go.jp/keiba/thisweek/{d.year}/{d:%m%d}_{n}/horse.html"
        try:
            soup = fetch(url)
        except Exception:
            continue
        if core and core in (soup.title.get_text() if soup.title else ""):
            return url, {norm(h.get_text()) for h in soup.select("h3") if h.get_text(strip=True)}
    return "", set()


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
    try:
        tw_url, notable = find_thisweek(cfg)
    except Exception as e:
        print(f"  [warn] thisweek: {e}")
        tw_url, notable = "", set()
    srcs = [("スポーツナビ 特別登録", len(horses)), ("JRA プレレーティング", len(pre)), ("JRA レーティング順位", len(rank)),
            ("JRA データ分析", 1 if ana else 0), ("JRA 今週の注目レース", len(notable))]
    chips = "".join(f'<span class="s {"ok" if n else "ng"}">{"✓" if n else "－"} {html.escape(k)}</span>' for k, n in srcs)
    rl = [("スポーツナビ 特別登録", cfg["sportsnavi"])] + [(n, cfg[k]) for n, k in (("JRA プレレーティング", "jra_prerating"), ("JRA レーティング順位", "jra_rank_news"), ("JRA データ分析", "jra_analysis")) if cfg.get(k)]
    if tw_url:
        rl.append(("JRA 注目レース出走馬情報", tw_url))
    lines = []
    for h in horses:
        r, rc = pre.get(h["name"], (0, ""))
        flag = ("X" if h["name"] in cfg.get("scratched", []) else "") + ("G" if h["name"] in cfg.get("g1_winners", []) else "") + ("P" if h["name"] in notable else "")
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
           "{{KEY}}": cfg["slug"] + "-marks", "{{SOURCES}}": chips, "{{LINKS}}": links_html(rl), "{{MEMO}}": html.escape(cfg.get("memo", "")), "{{PRESET}}": json.dumps(cfg.get("odds", {}), ensure_ascii=False)}
    for k, v in rep.items():
        t = t.replace(k, v)
    (OUT / f"{cfg['slug']}.html").write_text(t, encoding="utf-8")
    return len(horses), [(k, n) for k, n in srcs], tw_url


SRC_WHAT = {"スポーツナビ 特別登録": "登録馬・厩舎・血統", "JRA プレレーティング": "レーティングと該当レース", "JRA レーティング順位": "JRAの順位（1〜15位）",
            "JRA データ分析": "過去10年の傾向", "JRA 今週の注目レース": "JRA注目馬（出走馬情報）"}
EXT = [("他サイトで見る（リンクのみ）", [("netkeiba レース一覧", "https://race.netkeiba.com/top/race_list.html"), ("netkeiba データベース", "https://db.netkeiba.com/"),
        ("競馬ラボ", "https://www.keibalab.jp/"), ("ウマニティ", "https://umanity.jp/"), ("JBISサーチ", "https://www.jbis.or.jp/"),
        ("楽天競馬", "https://keiba.rakuten.co.jp/"), ("オッズパーク", "https://www.oddspark.com/keiba/")]),
       ("調教・ニュース", [("サンスポ競馬", "https://www.sanspo.com/race/"), ("デイリー競馬", "https://www.daily.co.jp/horse/"),
        ("東スポ競馬", "https://www.tokyo-sports.co.jp/race/"), ("中日スポーツ競馬", "https://www.chunichi.co.jp/chuspo/horserace")]),
       ("映像・地方競馬", [("JRA公式YouTube", "https://www.youtube.com/@JRAofficial"), ("地方競馬情報（NAR）", "https://www.keiba.go.jp/"),
        ("南関東競馬", "https://www.nankankeiba.com/")])]


def links_html(race_links):
    groups = ([("このレースの情報元", race_links)] if race_links else []) + EXT
    out = "".join(f'<h3>{html.escape(t)}</h3>' + "".join(f'<a class="lk" href="{html.escape(u)}" target="_blank" rel="noopener noreferrer">{html.escape(l)}</a>' for l, u in ls) for t, ls in groups)
    return f'<div class="links">{out}<p class="foot">リンク先は各サイトの規約に従ってご覧ください。このサイトは、これらのサイトのデータを取り込んでいません。</p></div>'


VENUES = {"札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
          "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10"}
VENUE_RE = re.compile(r"(\d+)回(" + "|".join(VENUES) + r")(\d+)日")
GRADE_RE = re.compile(r"[（(](?:J・)?G[ⅠⅡⅢ][）)]")
WD = "月火水木金土日"


def parse_day(soup, d):
    """JRA各日番組表から重賞（GⅠ・GⅡ・GⅢ）を拾う。"""
    out = []
    for table in soup.select("table"):
        if "レース番号" not in table.get_text():
            continue
        node = table.find_previous(string=VENUE_RE)
        m = VENUE_RE.search(node) if node else None
        if not m:
            continue
        kai, venue, nichi = int(m.group(1)), m.group(2), int(m.group(3))
        for tr in table.find_all("tr"):
            td = tr.find_all("td")
            rn = re.match(r"(\d+)レース", td[0].get_text(strip=True)) if len(td) >= 2 else None
            if not rn:
                continue
            lines = td[1].get_text("\n", strip=True).split("\n")
            gi = next((i for i, l in enumerate(lines) if GRADE_RE.search(l)), None)
            if gi is None:
                continue
            r = int(rn.group(1))
            cond = [l for l in lines[gi + 1:] if not re.fullmatch(r"[（(].*[）)]", l)]
            sid = f"{d.year % 100:02d}{VENUES[venue]}{kai:02d}{nichi:02d}{r:02d}"
            out.append({
                "slug": f"g{d:%Y%m%d}-{VENUES[venue]}{r:02d}",
                "date": d.isoformat(),
                "eyebrow": re.search(r"G[ⅠⅡⅢ]", lines[gi]).group(),
                "title": lines[gi],
                "meta": f"{d.year}年{d.month}月{d.day}日（{WD[d.weekday()]}）{venue}{r}R　" + "／".join(cond),
                "sportsnavi": f"https://sports.yahoo.co.jp/keiba/race/regist/{sid}",
                "auto": True,
            })
    return out


def discover(today, days=9):
    found = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        if d.weekday() not in (5, 6, 0):  # 土・日・月（祝日開催）
            continue
        url = f"https://www.jra.go.jp/keiba/calendar{d.year}/{d.year}/{d.month}/{d.month:02d}{d.day:02d}.html"
        try:
            found += parse_day(fetch(url), d)
        except Exception as e:
            print(f"  [info] 番組表なし/取得失敗 {d}: {e}")
    print(f"自動検出: 重賞{len(found)}件")
    return found


def write_index(archive, status):
    today = dt.datetime.now(JST).date().isoformat()
    def card(r):
        badge = f'<span class="g">{html.escape(r.get("eyebrow", ""))}</span>' if r.get("eyebrow") else ""
        ch = "".join(f'<i class="{"ok" if n else "ng"}">{html.escape(k.split(" ")[-1])}</i>' for k, n in r.get("sources", []))
        return (f'<a class="c" href="{r["slug"]}.html"><div class="t">{badge}<b>{html.escape(r["title"])}</b></div>'
                f'<div class="m">{html.escape(r.get("meta", r["date"]))}</div><div class="s">{ch}</div></a>')
    up = sorted([r for r in archive if r["date"] >= today], key=lambda r: r["date"])
    past = sorted([r for r in archive if r["date"] < today], key=lambda r: r["date"], reverse=True)
    st = "".join(f'<li><b>{html.escape(k)}</b>　<span class="{"ok" if v["ok"] else "ng"}">{"取得中" if v["ok"] else "未取得"}</span>　<small>{html.escape(v.get("what", ""))}　最終成功 {v.get("last", "-")}</small></li>' for k, v in status.items())
    css = ("*{box-sizing:border-box}body{margin:0;background:#f1f4f1;color:#1d2421;font:14px/1.6 'Hiragino Kaku Gothic ProN',Meiryo,sans-serif}"
           ".bar{background:#1f5f3f;color:#fff;padding:12px 14px;font-weight:700;font-size:17px;position:sticky;top:0}main{max-width:860px;margin:0 auto;padding:10px}"
           "h2{font-size:15px;border-left:4px solid #e8731a;padding-left:8px;margin:18px 0 8px}.c{display:block;background:#fff;border:1px solid #ccd6cf;border-radius:6px;padding:10px 12px;margin:8px 0;color:inherit;text-decoration:none}"
           ".c .t{font-size:16px}.g{background:#e8731a;color:#fff;font-size:11px;padding:0 7px;border-radius:3px;margin-right:6px}.m{color:#6b7670;font-size:12.5px}"
           ".s i{font-style:normal;font-size:11px;margin:4px 4px 0 0;display:inline-block;padding:0 6px;border-radius:3px}.ok{color:#1f5f3f}.ng{color:#999}.s i.ok{background:#e8efe9}.s i.ng{background:#eee}"
           "ul{list-style:none;padding:0;margin:0}li{background:#fff;border:1px solid #ccd6cf;border-radius:6px;padding:8px 12px;margin:6px 0}small{color:#6b7670}.n{color:#6b7670;font-size:12.5px}.links h3{font-size:13px;margin:10px 0 4px;color:#6b7670}.lk{display:inline-block;margin:3px 6px 3px 0;padding:8px 12px;border:1px solid #ccd6cf;border-radius:6px;background:#fff;color:#1f5f3f;font-size:13px;text-decoration:none}.foot{font-size:11.5px;color:#6b7670}")
    (OUT / "index.html").write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>重賞ボード</title>'
        f'<style>{css}</style><div class="bar">重賞ボード　<small style="color:#cfe3d6">JRAの重賞を毎週自動で整理</small></div><main>'
        f'<h2>これからの重賞</h2>{"".join(card(r) for r in up) or "<p class=n>いま公開できる重賞はありません。登録が出ると自動で追加されます。</p>"}'
        f'<h2>過去の重賞</h2>{"".join(card(r) for r in past) or "<p class=n>まだありません。</p>"}'
        f'<h2>取得元の状況</h2><ul>{st}</ul>'
        f'<h2>情報リンク</h2><div class="links">{links_html([])}</div>'
        '<p class="n">数値は目安であり、的中や利益を保証しません。馬券は自己責任でお楽しみください。</p></main></html>', encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    manual = json.loads((ROOT / "races.json").read_text(encoding="utf-8"))
    today = dt.datetime.now(JST).date()
    known = {c["sportsnavi"] for c in manual}
    races = manual + [c for c in discover(today) if c["sportsnavi"] not in known]
    af = OUT / "archive.json"
    archive = {a["slug"]: a for a in (json.loads(af.read_text(encoding="utf-8")) if af.exists() else [])}
    sf = OUT / "status.json"
    status = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {k: {"ok": 0, "last": "-", "what": w} for k, w in SRC_WHAT.items()}
    ok = fail = 0
    for cfg in races:
        out = OUT / f"{cfg['slug']}.html"
        if dt.date.fromisoformat(cfg["date"]) < today - dt.timedelta(days=1) and out.exists():
            print(f"skip（開催済み）: {cfg['slug']}")
            continue
        try:
            n, srcs, tw = build_race(cfg, today)
            print(f"ok: {cfg['slug']} {cfg['title']} {n}頭 / " + " ".join(f"{k}={v}" for k, v in srcs))
            archive[cfg["slug"]] = {"slug": cfg["slug"], "title": cfg["title"], "date": cfg["date"], "eyebrow": cfg.get("eyebrow", ""), "meta": cfg["meta"], "sources": srcs}
            for k, v in srcs:
                e = status.setdefault(k, {"ok": 0, "last": "-", "what": SRC_WHAT.get(k, "")})
                if v:
                    e["ok"] = 1
                    e["last"] = dt.datetime.now(JST).strftime("%m/%d %H:%M")
            ok += 1
        except Exception as e:
            # 自動検出分は、登録がまだ公開されていないだけの場合があるので警告にとどめる
            print(f"[{'warn' if cfg.get('auto') else 'error'}] {cfg['slug']} {cfg['title']}: {e}")
            if not cfg.get("auto"):
                fail += 1
    af.write_text(json.dumps(list(archive.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    sf.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    write_index(list(archive.values()), status)
    if fail and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
