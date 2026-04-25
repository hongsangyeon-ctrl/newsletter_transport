"""
서울·수도권 교통 뉴스레터 — GitHub Actions 버전 (v3)
추가: 서울 교통 지표 (열린데이터광장), 다산콜센터 블로그, 교통 전문 저널
"""

import feedparser
import smtplib
import urllib.parse
import urllib.request
import html
import os
import re
import json
import anthropic
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
    "recipients":      os.environ["RECIPIENT_EMAILS"].split(","),
    "anthropic_key":   os.environ["ANTHROPIC_API_KEY"],
    # 서울 열린데이터광장 API 키 (data.seoul.go.kr에서 무료 발급)
    "seoul_api_key":   os.environ.get("SEOUL_API_KEY", ""),
    "hours_back":      24,
    "max_articles_per_topic": 4,
}

claude = anthropic.Anthropic(api_key=CONFIG["anthropic_key"])

# ─────────────────────────────────────────────
# 뉴스 키워드
# ─────────────────────────────────────────────
NEWS_TOPICS = {
    "🚇 대중교통 (버스·지하철)": [
        "서울 지하철", "서울 버스 노선", "수도권 광역버스", "GTX 수도권",
    ],
    "🚗 도로·교통 혼잡": [
        "서울 교통 혼잡", "수도권 도로 정체", "서울 주차 교통",
    ],
    "🤖 자율주행·미래모빌리티": [
        "자율주행 서울", "모빌리티 서비스 한국", "전동킥보드 PM 교통", "UAM 도심항공",
    ],
    "📋 교통 정책·행정": [
        "서울시 교통정책", "국토부 교통 정책", "교통 요금 인상", "수도권 교통계획",
    ],
}

# ─────────────────────────────────────────────
# 학술논문 소스 (arXiv + 전문 저널 RSS)
# ─────────────────────────────────────────────
# arXiv: 교통계획·스마트모빌리티 중심으로 정밀화
ARXIV_QUERIES = [
    ("smart mobility public transit demand prediction", "스마트모빌리티·수요예측"),
    ("MaaS mobility as a service urban transport", "MaaS·통합교통"),
    ("transit oriented development urban planning", "대중교통 중심 개발"),
    ("traffic flow deep learning autonomous vehicle", "교통류·자율주행 AI"),
]

# 교통 전문 저널 RSS (Elsevier, TRB 등)
JOURNAL_RSS = [
    {
        "name": "Transportation Research Part A",
        "url": "https://rss.sciencedirect.com/publication/science/09658564",
        "label": "TR Part A (정책·실무)"
    },
    {
        "name": "Transportation Research Part C",
        "url": "https://rss.sciencedirect.com/publication/science/0968090X",
        "label": "TR Part C (신기술·자율주행)"
    },
    {
        "name": "Journal of Transport Geography",
        "url": "https://rss.sciencedirect.com/publication/science/09666923",
        "label": "교통지리학"
    },
    {
        "name": "Transport Policy",
        "url": "https://rss.sciencedirect.com/publication/science/0967070X",
        "label": "교통정책"
    },
]


# ─────────────────────────────────────────────
# AI 요약 함수
# ─────────────────────────────────────────────
def summarize_news(title: str, body: str) -> str:
    if not body.strip():
        return ""
    prompt = f"""다음 교통 관련 뉴스 기사를 교통 전문가 관점에서 핵심만 3~5줄로 요약해주세요.
불필요한 광고·기자 정보는 제외하고, 정책/수치/영향 위주로 간결하게 작성하세요.

제목: {title}
본문:
{body[:2000]}

요약:"""
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"    요약 실패: {e}")
        return ""


def summarize_paper(title: str, abstract: str) -> str:
    prompt = f"""다음 교통·도시계획 학술논문의 초록을 한국어로 3~5줄 요약해주세요.
연구 목적, 방법, 주요 결과 순으로 간결하게 작성하세요.

제목: {title}
초록: {abstract}

한국어 요약:"""
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"    논문 요약 실패: {e}")
        return ""


def summarize_blog_post(title: str, body: str) -> str:
    if not body.strip():
        return ""
    prompt = f"""다음은 서울 다산콜센터(120)에서 발행한 블로그 글입니다.
교통 관련 내용이 있다면 핵심 민원 사례나 정보를 2~3줄로 요약하세요.
교통과 무관한 내용이면 "교통 무관"이라고만 답하세요.

제목: {title}
본문: {body[:1500]}

요약:"""
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        result = resp.content[0].text.strip()
        return "" if "교통 무관" in result else result
    except Exception as e:
        return ""


# ─────────────────────────────────────────────
# 웹 본문 크롤링
# ─────────────────────────────────────────────
def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; newsletter-bot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


# ─────────────────────────────────────────────
# 뉴스 수집
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
            "summary":   "",
        })
        if len(results) >= max_items:
            break
    return results


# ─────────────────────────────────────────────
# 다산콜센터 네이버 블로그 RSS
# ─────────────────────────────────────────────
def fetch_dasan_blog(max_items: int = 5) -> list[dict]:
    """다산콜센터(120) 네이버 블로그 최신 포스팅 수집"""
    url = "https://rss.blog.naver.com/120seoulcall.xml"
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries[:max_items]:
        results.append({
            "title":     html.unescape(entry.get("title", "(제목 없음)")),
            "link":      entry.get("link", ""),
            "published": entry.get("published", "")[:16],
            "summary":   "",
        })
    return results


# ─────────────────────────────────────────────
# 서울 열린데이터광장 — 지하철 이용현황
# ─────────────────────────────────────────────
def fetch_seoul_subway_stats() -> list[dict]:
    """서울 지하철 호선별 이용현황 (이번 달 상위 혼잡 노선)"""
    if not CONFIG["seoul_api_key"]:
        return []
    try:
        yyyymm = datetime.now().strftime("%Y%m")
        key = CONFIG["seoul_api_key"]
        url = (f"http://openAPI.seoul.go.kr:8088/{key}/json/"
               f"CardSubwayStatsNew/1/9/{yyyymm}/")
        req = urllib.request.Request(url, headers={"User-Agent": "newsletter-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))

        rows = data.get("CardSubwayStatsNew", {}).get("row", [])
        # 승차+하차 합산 기준 정렬
        stats = []
        for row in rows:
            try:
                total = int(row.get("RIDE_PASGR_NUM", 0)) + int(row.get("ALIGHT_PASGR_NUM", 0))
                stats.append({
                    "line":  row.get("LINE_NUM", ""),
                    "station": row.get("SUB_STA_NM", ""),
                    "total": total,
                })
            except Exception:
                continue
        stats.sort(key=lambda x: x["total"], reverse=True)
        return stats[:9]
    except Exception as e:
        print(f"  지하철 통계 수집 실패: {e}")
        return []


def fetch_seoul_bus_stats() -> list[dict]:
    """서울 버스 노선별 이용현황 상위 5개"""
    if not CONFIG["seoul_api_key"]:
        return []
    try:
        key = CONFIG["seoul_api_key"]
        url = (f"http://openAPI.seoul.go.kr:8088/{key}/json/"
               f"CardBusStatisticsServiceNew/1/5/")
        req = urllib.request.Request(url, headers={"User-Agent": "newsletter-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))

        rows = data.get("CardBusStatisticsServiceNew", {}).get("row", [])
        results = []
        for row in rows:
            try:
                results.append({
                    "route": row.get("ROUTE_NM", ""),
                    "type":  row.get("ROUTE_TYPE_NM", ""),
                    "rides": int(row.get("RIDE_PASGR_NUM", 0)),
                })
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"  버스 통계 수집 실패: {e}")
        return []


# ─────────────────────────────────────────────
# 학술논문 수집
# ─────────────────────────────────────────────
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
        abstract = entry.get("summary", "").replace("\n", " ").strip()
        results.append({
            "title":     entry.get("title", "").replace("\n", " ").strip(),
            "link":      entry.get("link", ""),
            "authors":   authors,
            "abstract":  abstract,
            "summary":   "",
            "published": entry.get("published", "")[:10],
        })
    return results


def fetch_journal_papers(rss_url: str, max_items: int = 2) -> list[dict]:
    """Elsevier 저널 RSS 수집"""
    feed = feedparser.parse(rss_url)
    results = []
    for entry in feed.entries[:max_items]:
        abstract = entry.get("summary", "").replace("\n", " ").strip()
        # HTML 태그 제거
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()
        results.append({
            "title":     html.unescape(entry.get("title", "").replace("\n", " ").strip()),
            "link":      entry.get("link", ""),
            "authors":   "",
            "abstract":  abstract[:500],
            "summary":   "",
            "published": entry.get("published", "")[:10],
        })
    return results


# ─────────────────────────────────────────────
# HTML 이메일 생성
# ─────────────────────────────────────────────
def build_html(news_data, dasan_posts, subway_stats, bus_stats, arxiv_data, journal_data) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    total_news = sum(len(v) for v in news_data.values())
    yyyymm = datetime.now().strftime("%Y년 %m월")

    css = """
  body{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
       background:#f5f6f8;margin:0;padding:20px;color:#222;}
  .wrap{max-width:700px;margin:0 auto;background:#fff;border-radius:12px;
        overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);}
  .hdr{background:linear-gradient(135deg,#1a3c6e,#2e6da4);color:#fff;padding:28px 32px;}
  .hdr h1{margin:0 0 4px;font-size:22px;}
  .hdr p{margin:0;font-size:13px;opacity:.8;}
  .badges{margin-top:10px;}
  .badge{display:inline-block;background:rgba(255,255,255,.2);border-radius:20px;
         padding:3px 12px;font-size:12px;margin-right:6px;}
  .sec{padding:20px 32px 8px;}
  .sec-title{font-size:16px;font-weight:700;margin:0 0 14px;
             border-left:4px solid #2e6da4;padding-left:10px;}
  .art{padding:12px 0;border-bottom:1px solid #f0f0f0;}
  .art:last-child{border-bottom:none;}
  .art a{color:#1a3c6e;text-decoration:none;font-size:14px;
         font-weight:600;line-height:1.4;}
  .art a:hover{text-decoration:underline;}
  .meta{font-size:11px;color:#888;margin-top:3px;}
  .ai-summary{background:#f0f7ff;border-left:3px solid #2e6da4;border-radius:0 6px 6px 0;
              padding:8px 12px;margin-top:8px;font-size:12px;color:#333;
              line-height:1.7;white-space:pre-line;}
  .ai-label{font-size:10px;color:#2e6da4;font-weight:700;margin-bottom:3px;}
  /* 교통 지표 테이블 */
  .stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px;}
  .stat-card{background:#f8faff;border-radius:8px;padding:10px 12px;text-align:center;}
  .stat-card .line{font-size:11px;color:#2e6da4;font-weight:700;}
  .stat-card .stn{font-size:13px;font-weight:600;margin:2px 0;}
  .stat-card .num{font-size:11px;color:#666;}
  .bus-row{display:flex;justify-content:space-between;padding:6px 0;
           border-bottom:1px solid #f0f0f0;font-size:13px;}
  .bus-row:last-child{border-bottom:none;}
  .bus-type{font-size:10px;background:#e8f0fe;color:#2e6da4;border-radius:3px;
            padding:1px 5px;margin-left:6px;}
  /* 논문 */
  .paper{background:#f8faff;border-radius:8px;padding:12px 14px;margin-bottom:10px;}
  .paper a{color:#1a3c6e;font-size:13px;font-weight:700;text-decoration:none;}
  .paper .auth{font-size:11px;color:#666;margin:4px 0 6px;}
  .ptag{display:inline-block;background:#e8f0fe;color:#2e6da4;border-radius:4px;
        padding:1px 7px;font-size:10px;margin-bottom:8px;margin-right:4px;}
  .jtag{background:#e8f5e9;color:#2e7d32;}
  .none{color:#aaa;font-size:13px;font-style:italic;padding:8px 0;}
  .ftr{background:#f8f9fa;padding:16px 32px;text-align:center;font-size:11px;color:#aaa;}
    """

    parts = [f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><style>{css}</style></head>
<body><div class="wrap">
  <div class="hdr">
    <h1>🚦 서울·수도권 교통 뉴스레터</h1>
    <p>{today} · 최근 24시간 수집 · AI 요약 포함</p>
    <div class="badges">
      <span class="badge">뉴스 {total_news}건</span>
      <span class="badge">논문 {sum(len(p) for _,p in arxiv_data)+sum(len(p) for _,p in journal_data)}건</span>
      {"<span class='badge'>교통 지표 포함</span>" if subway_stats else ""}
    </div>
  </div>
"""]

    # ── 1. 서울 교통 지표 ──────────────────────────
    if subway_stats or bus_stats:
        parts.append(f'<div class="sec"><div class="sec-title">📊 서울 교통 지표 ({yyyymm})</div>')

        if subway_stats:
            parts.append('<p style="font-size:12px;color:#555;margin:0 0 8px;">🚇 지하철 이용객 상위 역 (승+하차 합산)</p>')
            parts.append('<div class="stat-grid">')
            for s in subway_stats:
                total_str = f"{s['total']:,}명"
                parts.append(
                    f'<div class="stat-card">'
                    f'<div class="line">{s["line"]}</div>'
                    f'<div class="stn">{s["station"]}</div>'
                    f'<div class="num">{total_str}</div>'
                    f'</div>'
                )
            parts.append('</div>')

        if bus_stats:
            parts.append('<p style="font-size:12px;color:#555;margin:12px 0 8px;">🚌 버스 노선별 이용객 상위</p>')
            for b in bus_stats:
                parts.append(
                    f'<div class="bus-row">'
                    f'<span>{b["route"]}<span class="bus-type">{b["type"]}</span></span>'
                    f'<span style="color:#1a3c6e;font-weight:600;">{b["rides"]:,}명</span>'
                    f'</div>'
                )

        parts.append('</div>')

    # ── 2. 뉴스 섹션 ──────────────────────────────
    for topic, articles in news_data.items():
        parts.append(f'<div class="sec"><div class="sec-title">{topic}</div>')
        if articles:
            for a in articles:
                src = f" · {a['source']}" if a['source'] else ""
                summary_html = (
                    f'<div class="ai-summary"><div class="ai-label">✦ AI 요약</div>'
                    f'{html.escape(a["summary"])}</div>'
                ) if a.get("summary") else ""
                parts.append(
                    f'<div class="art">'
                    f'<a href="{a["link"]}" target="_blank">{a["title"]}</a>'
                    f'<div class="meta">{a["published"]}{src}</div>'
                    f'{summary_html}</div>'
                )
        else:
            parts.append('<p class="none">최근 24시간 내 해당 뉴스가 없습니다.</p>')
        parts.append('</div>')

    # ── 3. 다산콜센터 블로그 ──────────────────────
    transport_posts = [p for p in dasan_posts if p.get("summary")]
    if transport_posts:
        parts.append('<div class="sec"><div class="sec-title">📢 다산콜센터(120) 교통 소식</div>')
        for p in transport_posts:
            parts.append(
                f'<div class="art">'
                f'<a href="{p["link"]}" target="_blank">{p["title"]}</a>'
                f'<div class="meta">{p["published"]} · 120 다산콜재단 블로그</div>'
                f'<div class="ai-summary"><div class="ai-label">✦ AI 요약</div>'
                f'{html.escape(p["summary"])}</div>'
                f'</div>'
            )
        parts.append('</div>')

    # ── 4. 학술논문 (arXiv) ───────────────────────
    parts.append('<div class="sec"><div class="sec-title">📄 학술논문</div>')

    if arxiv_data:
        parts.append('<span class="ptag">arXiv 프리프린트</span><br>')
        for label, papers in arxiv_data:
            if papers:
                parts.append(f'<span class="ptag">{label}</span>')
                for p in papers:
                    summary_html = (
                        f'<div class="ai-summary"><div class="ai-label">✦ AI 요약 (한국어)</div>'
                        f'{html.escape(p["summary"])}</div>'
                    ) if p.get("summary") else ""
                    parts.append(
                        f'<div class="paper">'
                        f'<a href="{p["link"]}" target="_blank">{p["title"]}</a>'
                        f'<div class="auth">✍ {p["authors"]} · {p["published"]}</div>'
                        f'{summary_html}</div>'
                    )

    if journal_data:
        parts.append('<br><span class="ptag jtag">교통 전문 저널</span><br>')
        for label, papers in journal_data:
            if papers:
                parts.append(f'<span class="ptag jtag">{label}</span>')
                for p in papers:
                    summary_html = (
                        f'<div class="ai-summary"><div class="ai-label">✦ AI 요약 (한국어)</div>'
                        f'{html.escape(p["summary"])}</div>'
                    ) if p.get("summary") else ""
                    parts.append(
                        f'<div class="paper">'
                        f'<a href="{p["link"]}" target="_blank">{p["title"]}</a>'
                        f'<div class="auth">{p["published"]}</div>'
                        f'{summary_html}</div>'
                    )

    if not arxiv_data and not journal_data:
        parts.append('<p class="none">최근 논문이 없습니다.</p>')
    parts.append('</div>')

    parts.append(
        f'<div class="ftr">자동 발송 뉴스레터 · GitHub Actions + Claude AI · '
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
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 뉴스레터 수집 시작")

    # ── 뉴스 수집 ──
    news_data: dict = {}
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

    # ── 뉴스 AI 요약 ──
    print("  뉴스 AI 요약 중...")
    for _, articles in news_data.items():
        for a in articles:
            body = fetch_page_text(a["link"])
            a["summary"] = summarize_news(a["title"], body)
            time.sleep(0.5)

    # ── 다산콜센터 블로그 ──
    print("  다산콜센터 블로그 수집 중...")
    dasan_posts = fetch_dasan_blog(max_items=6)
    for p in dasan_posts:
        body = fetch_page_text(p["link"])
        p["summary"] = summarize_blog_post(p["title"], body)
        time.sleep(0.5)
    transport_count = sum(1 for p in dasan_posts if p.get("summary"))
    print(f"  다산콜센터 교통 관련 포스팅: {transport_count}건")

    # ── 서울 교통 지표 ──
    print("  서울 교통 지표 수집 중...")
    subway_stats = fetch_seoul_subway_stats()
    bus_stats    = fetch_seoul_bus_stats()
    print(f"  지하철 상위역: {len(subway_stats)}개, 버스 노선: {len(bus_stats)}개")

    # ── arXiv 논문 ──
    arxiv_data = []
    for query, label in ARXIV_QUERIES:
        papers = fetch_arxiv_papers(query, max_items=2)
        if papers:
            arxiv_data.append((label, papers))
        time.sleep(1)

    # ── 전문 저널 RSS ──
    journal_data = []
    for j in JOURNAL_RSS:
        papers = fetch_journal_papers(j["url"], max_items=2)
        if papers:
            journal_data.append((j["label"], papers))
        time.sleep(0.5)

    total_papers = sum(len(p) for _, p in arxiv_data) + sum(len(p) for _, p in journal_data)
    print(f"  논문 수집: {total_papers}건 (arXiv+저널)")

    # ── 논문 AI 요약 ──
    print("  논문 AI 요약 중...")
    for _, papers in arxiv_data + journal_data:
        for p in papers:
            p["summary"] = summarize_paper(p["title"], p["abstract"])
            time.sleep(0.5)

    # ── 발송 ──
    today_str = datetime.now().strftime("%Y.%m.%d")
    subject   = f"🚦 서울·수도권 교통 뉴스레터 [{today_str}]"
    html_body = build_html(news_data, dasan_posts, subway_stats, bus_stats, arxiv_data, journal_data)
    send_email(html_body, subject)
    print(f"  ✅ 발송 완료 → {', '.join(CONFIG['recipients'])}")


if __name__ == "__main__":
    main()
