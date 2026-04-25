"""
서울·수도권 교통 뉴스레터 — GitHub Actions 버전
환경변수에서 이메일 설정을 읽어옵니다.
"""

import feedparser
import smtplib
import urllib.parse
import html
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from time import mktime
import time

# ─────────────────────────────────────────────
# 설정 (GitHub Secrets에서 자동으로 읽어옴)
# ─────────────────────────────────────────────
CONFIG = {
    "sender_email":    os.environ["SENDER_EMAIL"],
    "sender_password": os.environ["SENDER_PASSWORD"],
    # 수신자 여러 명: "a@gmail.com,b@gmail.com" 형태로 Secret에 저장
    "recipients":      os.environ["RECIPIENT_EMAILS"].split(","),
    "hours_back":      24,
    "max_articles_per_topic": 5,
}

# ─────────────────────────────────────────────
# 뉴스 키워드 (주제별)
# ─────────────────────────────────────────────
NEWS_TOPICS = {
    "🚇 대중교통 (버스·지하철)": [
        "서울 지하철",
        "서울 버스 노선",
        "수도권 광역버스",
        "GTX 수도권",
    ],
    "🚗 도로·교통 혼잡": [
        "서울 교통 혼잡",
        "수도권 도로 정체",
        "서울 주차 교통",
    ],
    "🤖 자율주행·미래모빌리티": [
        "자율주행 서울",
        "모빌리티 서비스 한국",
        "전동킥보드 PM 교통",
        "UAM 도심항공",
    ],
    "📋 교통 정책·행정": [
        "서울시 교통정책",
        "국토부 교통 정책",
        "교통 요금 인상",
        "수도권 교통계획",
    ],
}

# ─────────────────────────────────────────────
# 학술논문 키워드 (arXiv)
# ─────────────────────────────────────────────
ARXIV_QUERIES = [
    ("urban transportation demand forecasting", "교통수요 예측"),
    ("autonomous vehicle urban planning",       "자율주행·도시계획"),
    ("public transit ridership Korea Seoul",    "서울 대중교통"),
    ("traffic congestion deep learning",        "교통 혼잡 AI"),
]


# ─────────────────────────────────────────────
# 수집 함수
# ─────────────────────────────────────────────
def fetch_google_news(query: str, hours_back: int, max_items: int) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    results = []
    for entry in feed.entries:
        try:
            pub = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
        except Exception:
            continue
        if pub < cutoff:
            continue
        results.append({
            "title":     html.unescape(entry.get("title", "(제목 없음)")),
            "link":      entry.get("link", ""),
            "source":    entry.get("source", {}).get("title", ""),
            "published": pub.strftime("%m/%d %H:%M"),
        })
        if len(results) >= max_items:
            break
    return results


def fetch_arxiv_papers(query: str, max_items: int = 2) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = (f"https://export.arxiv.org/api/query"
           f"?search_query=all:{encoded}"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={max_items}")
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        authors = ", ".join(a.get("name", "") for a in entry.get("authors", [])[:3])
        if len(entry.get("authors", [])) > 3:
            authors += " 외"
        results.append({
            "title":     entry.get("title", "").replace("\n", " ").strip(),
            "link":      entry.get("link", ""),
            "authors":   authors,
            "summary":   entry.get("summary", "")[:200].replace("\n", " ").strip() + "...",
            "published": entry.get("published", "")[:10],
        })
    return results


# ─────────────────────────────────────────────
# HTML 이메일 생성
# ─────────────────────────────────────────────
def build_html(news_data: dict, arxiv_data: list) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    total = sum(len(v) for v in news_data.values())

    parts = [f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><style>
  body {{ font-family: 'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
         background:#f5f6f8; margin:0; padding:20px; color:#222; }}
  .wrap {{ max-width:700px; margin:0 auto; background:#fff;
           border-radius:12px; overflow:hidden;
           box-shadow:0 2px 12px rgba(0,0,0,.08); }}
  .hdr  {{ background:linear-gradient(135deg,#1a3c6e,#2e6da4);
           color:#fff; padding:28px 32px; }}
  .hdr h1 {{ margin:0 0 4px; font-size:22px; }}
  .hdr p  {{ margin:0; font-size:13px; opacity:.8; }}
  .badge  {{ display:inline-block; background:rgba(255,255,255,.2);
             border-radius:20px; padding:3px 12px;
             font-size:12px; margin-top:10px; }}
  .sec  {{ padding:24px 32px 8px; }}
  .sec-title {{ font-size:16px; font-weight:700; margin:0 0 14px;
                border-left:4px solid #2e6da4; padding-left:10px; }}
  .art  {{ padding:10px 0; border-bottom:1px solid #f0f0f0; }}
  .art:last-child {{ border-bottom:none; }}
  .art a {{ color:#1a3c6e; text-decoration:none; font-size:14px;
            font-weight:600; line-height:1.4; }}
  .art a:hover {{ text-decoration:underline; }}
  .meta {{ font-size:11px; color:#888; margin-top:3px; }}
  .paper {{ background:#f8faff; border-radius:8px;
            padding:12px 14px; margin-bottom:10px; }}
  .paper a {{ color:#1a3c6e; font-size:13px;
              font-weight:700; text-decoration:none; }}
  .paper .auth {{ font-size:11px; color:#666; margin:4px 0 2px; }}
  .paper .abs  {{ font-size:11px; color:#555; line-height:1.5; }}
  .ptag {{ display:inline-block; background:#e8f0fe; color:#2e6da4;
           border-radius:4px; padding:1px 7px;
           font-size:10px; margin-bottom:6px; }}
  .none {{ color:#aaa; font-size:13px;
           font-style:italic; padding:8px 0; }}
  .ftr  {{ background:#f8f9fa; padding:16px 32px;
           text-align:center; font-size:11px; color:#aaa; }}
</style></head>
<body><div class="wrap">
  <div class="hdr">
    <h1>🚦 서울·수도권 교통 뉴스레터</h1>
    <p>{today} · 최근 24시간 수집</p>
    <span class="badge">뉴스 총 {total}건</span>
  </div>
"""]

    for topic, articles in news_data.items():
        parts.append(f'<div class="sec"><div class="sec-title">{topic}</div>')
        if articles:
            for a in articles:
                src = f" · {a['source']}" if a['source'] else ""
                parts.append(
                    f'<div class="art">'
                    f'<a href="{a["link"]}" target="_blank">{a["title"]}</a>'
                    f'<div class="meta">{a["published"]}{src}</div>'
                    f'</div>'
                )
        else:
            parts.append('<p class="none">최근 24시간 내 해당 뉴스가 없습니다.</p>')
        parts.append('</div>')

    parts.append('<div class="sec"><div class="sec-title">📄 학술논문 (arXiv)</div>')
    if arxiv_data:
        for label, papers in arxiv_data:
            parts.append(f'<span class="ptag">{label}</span>')
            for p in papers:
                parts.append(
                    f'<div class="paper">'
                    f'<a href="{p["link"]}" target="_blank">{p["title"]}</a>'
                    f'<div class="auth">✍ {p["authors"]} · {p["published"]}</div>'
                    f'<div class="abs">{p["summary"]}</div>'
                    f'</div>'
                )
    else:
        parts.append('<p class="none">최근 논문이 없습니다.</p>')
    parts.append('</div>')

    parts.append(
        f'<div class="ftr">자동 발송 뉴스레터 · GitHub Actions · '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M UTC")}</div>'
        f'</div></body></html>'
    )
    return "".join(parts)


# ─────────────────────────────────────────────
# 이메일 발송
# ─────────────────────────────────────────────
def send_email(html_body: str, subject: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = CONFIG["sender_email"]
    msg["To"]      = ", ".join(CONFIG["recipients"])
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(CONFIG["sender_email"], CONFIG["sender_password"])
        s.sendmail(CONFIG["sender_email"], CONFIG["recipients"], msg.as_string())


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 뉴스 수집 시작")

    news_data = {}
    seen_urls: set[str] = set()

    for topic, queries in NEWS_TOPICS.items():
        articles = []
        for q in queries:
            for a in fetch_google_news(q, CONFIG["hours_back"], CONFIG["max_articles_per_topic"]):
                if a["link"] not in seen_urls and len(articles) < CONFIG["max_articles_per_topic"]:
                    seen_urls.add(a["link"])
                    articles.append(a)
            time.sleep(0.5)
        news_data[topic] = articles
        print(f"  {topic}: {len(articles)}건")

    arxiv_data = []
    for query, label in ARXIV_QUERIES:
        papers = fetch_arxiv_papers(query)
        if papers:
            arxiv_data.append((label, papers))
        time.sleep(1)
    print(f"  arXiv 논문: {sum(len(p) for _,p in arxiv_data)}건")

    today_str = datetime.now().strftime("%Y.%m.%d")
    subject   = f"🚦 서울·수도권 교통 뉴스레터 [{today_str}]"
    html_body = build_html(news_data, arxiv_data)

    send_email(html_body, subject)
    print(f"  ✅ 발송 완료 → {', '.join(CONFIG['recipients'])}")


if __name__ == "__main__":
    main()
