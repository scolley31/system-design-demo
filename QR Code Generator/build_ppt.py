"""產生 QR Code Generator 系統設計簡報（.pptx）。

從 DESIGN.md 的決策、API、架構與流程整理成投影片。
執行：./.venv/bin/python build_ppt.py
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.oxml.ns import qn

# ---- palette ----
INK = RGBColor(0x0F, 0x17, 0x2A)
MUTED = RGBColor(0x64, 0x74, 0x8B)
ACCENT = RGBColor(0x25, 0x63, 0xEB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
CLIENT = RGBColor(0x64, 0x74, 0x8B)
GATEWAY = RGBColor(0x0E, 0xA5, 0xE9)
APP = RGBColor(0x25, 0x63, 0xEB)
DB = RGBColor(0x05, 0x96, 0x69)
CACHE = RGBColor(0xDC, 0x26, 0x26)
CDN = RGBColor(0x7C, 0x3A, 0xED)
ANALYTICS = RGBColor(0xD9, 0x77, 0x06)
GREEN = RGBColor(0x05, 0x96, 0x69)
RED = RGBColor(0xDC, 0x26, 0x26)
CJK = "PingFang TC"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = prs.slide_width, prs.slide_height


def _font(run, size, color, bold=False):
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.name = CJK
    rPr = run._r.get_or_add_rPr()
    ea = rPr.makeelement(qn("a:ea"), {"typeface": CJK})
    rPr.append(ea)


def textbox(slide, x, y, w, h, lines, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, (text, size, color, bold, *rest) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(rest[0] if rest else 4)
        r = p.add_run()
        r.text = text
        _font(r, size, color, bold)
    return tb


def box(slide, x, y, w, h, text, fill, fg=WHITE, size=12, bold=True, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    sp.line.color.rgb = fill
    sp.shadow.inherit = False
    tf = sp.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    for i, ln in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = ln
        _font(r, size, fg, bold if i == 0 else False)
    return sp


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.75):
    cn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    cn.line.color.rgb = color
    cn.line.width = Pt(width)
    ln = cn.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return cn


def header(slide, kicker, title):
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.18), SH)
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT; bar.line.fill.background(); bar.shadow.inherit = False
    textbox(slide, Inches(0.55), Inches(0.35), Inches(12), Inches(1.1),
            [(kicker, 13, ACCENT, True, 2), (title, 30, INK, True)])


def bullets(slide, x, y, w, h, items, size=15, gap=7):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame; tf.word_wrap = True
    for i, it in enumerate(items):
        lvl = 0
        if isinstance(it, tuple):
            it, lvl = it
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.level = lvl
        r = p.add_run()
        prefix = "•  " if lvl == 0 else "–  "
        r.text = prefix + it
        _font(r, size if lvl == 0 else size - 1, INK if lvl == 0 else MUTED, False)
    return tb


# ============ Slide 1: Title ============
s = prs.slides.add_slide(BLANK)
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
bg.fill.solid(); bg.fill.fore_color.rgb = INK; bg.line.fill.background(); bg.shadow.inherit = False
box(s, Inches(0.9), Inches(2.0), Inches(1.5), Inches(1.5), "▣", ACCENT, WHITE, 54)
textbox(s, Inches(0.9), Inches(3.7), Inches(11.5), Inches(2.5), [
    ("QR Code Generator", 44, WHITE, True, 6),
    ("System Design — 架構、API 與設計決策", 20, RGBColor(0xCB, 0xD5, 0xE1), False, 10),
    ("Dynamic QR · FastAPI 原型 · 對齊 PDF 設計題 + repo 練習", 14, MUTED, False),
])

# ============ Slide 2: Requirements ============
s = prs.slides.add_slide(BLANK)
header(s, "01 · REQUIREMENTS", "需求與規模")
cols = [
    ("功能性 (FR)", APP, [
        "提交 URL → 回 token + QR 圖",
        "掃描短碼 → 302 導向原始 URL",
        "可改目標 URL / 軟刪 / 設過期",
        "掃描分析（總數、每日）",
        "URL 驗證：正規化 + 惡意阻擋",
    ]),
    ("非功能性 (NFR)", DB, [
        "24/7 高可用 (HA)",
        "Redirect 延遲 < 100ms",
        "10 億 QR、1 億用戶",
        "讀多寫少 ≈ 100:1",
    ]),
    ("容量估算", ANALYTICS, [
        "1B × ~200B ≈ 200GB",
        "≈ 5,800 redirect / 秒",
        "1 億用戶 × 5 次/日",
        "圖片 + redirect 為主流量",
    ]),
]
cw = Inches(3.9); gap = Inches(0.25); x0 = Inches(0.6); y0 = Inches(1.8)
for i, (title, color, items) in enumerate(cols):
    x = x0 + i * (cw + gap)
    box(s, x, y0, cw, Inches(0.6), title, color, WHITE, 16)
    card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y0 + Inches(0.7), cw, Inches(4.4))
    card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = LIGHT; card.shadow.inherit = False
    bullets(s, x + Inches(0.2), y0 + Inches(0.9), cw - Inches(0.4), Inches(4.0), items, size=14, gap=10)

# ============ Slide 3: API ============
s = prs.slides.add_slide(BLANK)
header(s, "02 · API DESIGN", "API 設計")
rows = [
    ("方法", "路徑", "說明"),
    ("GET", "/", "管理頁前端"),
    ("POST", "/api/v1/qr/create", "建立 → {token, short_url, qr_code_url, original_url}"),
    ("GET", "/r/{token}", "掃描入口：302 / 404(不存在) / 410(刪除·過期)"),
    ("GET", "/api/v1/qr", "列出所有 QR（含掃描數，管理頁）"),
    ("GET", "/api/v1/qr/{token}", "取得 metadata"),
    ("PATCH", "/api/v1/qr/{token}", "改 url / expires_at（部分更新）"),
    ("DELETE", "/api/v1/qr/{token}", "軟刪除"),
    ("GET", "/api/v1/qr/{token}/image", "QR PNG（dimension·color·border）"),
    ("GET", "/api/v1/qr/{token}/analytics", "掃描統計"),
]
tbl = s.shapes.add_table(len(rows), 3, Inches(0.6), Inches(1.7), Inches(12.1), Inches(5.0)).table
tbl.columns[0].width = Inches(1.4); tbl.columns[1].width = Inches(4.0); tbl.columns[2].width = Inches(6.7)
for r in range(len(rows)):
    for c in range(3):
        cell = tbl.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = rows[r][c]
        _font(run, 12 if r else 13, WHITE if r == 0 else (ACCENT if c == 0 and r else INK), r == 0 or (c == 0))

# ============ Slide 4: Data model ============
s = prs.slides.add_slide(BLANK)
header(s, "03 · DATA MODEL", "資料模型（時間一律存 UTC）")
box(s, Inches(0.6), Inches(1.8), Inches(5.9), Inches(0.55), "url_mappings", APP, WHITE, 16)
c1 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(2.45), Inches(5.9), Inches(4.0))
c1.fill.solid(); c1.fill.fore_color.rgb = LIGHT; c1.line.color.rgb = LIGHT; c1.shadow.inherit = False
bullets(s, Inches(0.85), Inches(2.65), Inches(5.4), Inches(3.6), [
    "id  (PK)",
    "token  String(8)  UNIQUE · index",
    "original_url  Text",
    "created_at / updated_at",
    "expires_at  (nullable)",
    "is_deleted  Boolean  ← 軟刪除",
], size=14, gap=10)
box(s, Inches(6.85), Inches(1.8), Inches(5.9), Inches(0.55), "scan_events  (分析明細)", ANALYTICS, WHITE, 16)
c2 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.85), Inches(2.45), Inches(5.9), Inches(4.0))
c2.fill.solid(); c2.fill.fore_color.rgb = LIGHT; c2.line.color.rgb = LIGHT; c2.shadow.inherit = False
bullets(s, Inches(7.1), Inches(2.65), Inches(5.4), Inches(3.6), [
    "id (PK) · token · scanned_at",
    "user_agent · ip_address",
    "index (token, scanned_at)",
    "正式版 → 獨立分析庫 + 預聚合",
    "daily_counts(token, date, count)",
], size=14, gap=10)

# ============ Slide: DB Schema ============
def codebox(slide, x, y, w, h, code, size=11):
    sp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sp.fill.solid(); sp.fill.fore_color.rgb = LIGHT; sp.line.color.rgb = LIGHT; sp.shadow.inherit = False
    tf = sp.text_frame; tf.word_wrap = True
    tf.margin_left = Pt(12); tf.margin_right = Pt(6); tf.margin_top = Pt(8)
    for i, line in enumerate(code.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(1)
        r = p.add_run(); r.text = line if line else " "
        r.font.size = Pt(size); r.font.name = "Menlo"
        r.font.color.rgb = MUTED if line.strip().startswith("--") else INK
    return sp

s = prs.slides.add_slide(BLANK)
header(s, "03b · DB SCHEMA", "資料庫 Schema (DDL) · schema.sql")
ddl1 = """CREATE TABLE url_mappings (
  id            INTEGER  PRIMARY KEY,
  token         VARCHAR(8)   NOT NULL,
  original_url  TEXT     NOT NULL,
  created_at    DATETIME NOT NULL,
  updated_at    DATETIME NOT NULL,
  expires_at    DATETIME     NULL,
  is_deleted    BOOLEAN  NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ix_url_mappings_token
  ON url_mappings (token);"""
ddl2 = """CREATE TABLE scan_events (
  id          INTEGER  PRIMARY KEY,
  token       VARCHAR(8)   NOT NULL,
  scanned_at  DATETIME NOT NULL,
  user_agent  VARCHAR(500) NULL,
  ip_address  VARCHAR(45)  NULL
);
CREATE INDEX idx_token_scanned
  ON scan_events (token, scanned_at);"""
codebox(s, Inches(0.6), Inches(1.75), Inches(6.05), Inches(3.5), ddl1)
codebox(s, Inches(6.9), Inches(1.75), Inches(5.85), Inches(3.5), ddl2)
bullets(s, Inches(0.6), Inches(5.45), Inches(12.3), Inches(1.7), [
    "token：8 碼 Base62；UNIQUE 索引兼碰撞安全網（第 5 題）+ redirect O(log n) 查找（第 2 題）",
    "is_deleted 軟刪除（第 12 題）· expires_at 惰性過期（第 13 題）· 時間一律存 UTC",
    "scan_events：分析明細 + 複合索引；正式版改獨立分析庫 + 預聚合（第 14 題）",
], size=13, gap=6)

# ============ Slide 5: Architecture ============
s = prs.slides.add_slide(BLANK)
header(s, "04 · HIGH-LEVEL ARCHITECTURE", "高階架構")
y = Inches(2.3); bh = Inches(0.95)
b_client = box(s, Inches(0.6), y, Inches(1.8), bh, "Client\n掃描者/瀏覽器", CLIENT, WHITE, 13)
b_gw = box(s, Inches(2.9), y, Inches(1.8), bh, "API Gateway", GATEWAY, WHITE, 13)
b_app = box(s, Inches(5.2), y, Inches(2.4), bh, "QR Service\n(stateless, 水平擴展)", APP, WHITE, 13)
arrow(s, Inches(2.4), y + Inches(0.47), Inches(2.9), y + Inches(0.47))
arrow(s, Inches(4.7), y + Inches(0.47), Inches(5.2), y + Inches(0.47))
# right-side stores
rx = Inches(8.6); rw = Inches(4.1)
b_cache = box(s, rx, Inches(1.5), rw, Inches(0.8), "Redis  快取\n(redirect 熱路徑, TTL)", CACHE, WHITE, 12)
b_db = box(s, rx, Inches(2.5), rw, Inches(0.9), "Primary DB → Read Replicas\n(寫走主 · 讀走副 · failover)", DB, WHITE, 12)
b_cdn = box(s, rx, Inches(3.55), rw, Inches(0.8), "Object Store + CDN\n(QR 圖片靜態內容)", CDN, WHITE, 12)
b_an = box(s, rx, Inches(4.45), rw, Inches(0.8), "佇列 → 分析庫\n(非同步 scan · 預聚合)", ANALYTICS, WHITE, 12)
ax = Inches(7.6); ay = y + Inches(0.47)
for tb_, ty in [(b_cache, Inches(1.9)), (b_db, Inches(2.95)), (b_cdn, Inches(3.95)), (b_an, Inches(4.85))]:
    arrow(s, ax, ay, rx, ty)
textbox(s, Inches(0.6), Inches(6.0), Inches(12), Inches(1.0), [
    ("讀多寫少：redirect 先打 Redis，miss 才到 DB（read replica）；QR 圖片走 CDN；scan 非同步進分析庫。", 13, MUTED, False)])

# ============ Slide: Decision map ============
s = prs.slides.add_slide(BLANK)
header(s, "決策地圖", "三大關鍵架構決策（環環相扣）")
cards = [
    ("① 動態 vs 靜態", APP, "選：動態 QR", [
        "QR 編 short URL → server redirect",
        "可修改目標 + 可追蹤掃描",
        "代價：server = SPOF",
        "→ 逼出 cache / CDN / monitoring",
    ]),
    ("② Token 生成策略", DB, "SHA-256+nonce+Base62", [
        "隨機（碰撞靠運氣）",
        "→ 純 hash（同 URL 同 token）",
        "→ +nonce（確定性重試 + DB 兜底）",
        "高流量可換 Pre-gen Pool",
    ]),
    ("③ 301 vs 302", CACHE, "選：302（暫時）", [
        "301 永久快取 → 跳過 server",
        "→ 分析流失、改不了目標",
        "302 每次回源 → 可改/刪/分析",
        "代價：latency → cache + CDN",
    ]),
]
cw = Inches(3.9); gp = Inches(0.3); x0 = Inches(0.5); y = Inches(2.1)
for i, (title, color, choice, items) in enumerate(cards):
    x = x0 + i * (cw + gp)
    box(s, x, y, cw, Inches(0.6), title, color, WHITE, 15)
    box(s, x, y + Inches(0.68), cw, Inches(0.55), choice, GREEN, WHITE, 13)
    body = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y + Inches(1.32), cw, Inches(2.7))
    body.fill.solid(); body.fill.fore_color.rgb = LIGHT; body.line.color.rgb = LIGHT; body.shadow.inherit = False
    bullets(s, x + Inches(0.2), y + Inches(1.48), cw - Inches(0.4), Inches(2.4), items, size=12, gap=8)
    if i < 2:
        arrow(s, x + cw, y + Inches(0.3), x + cw + gp, y + Inches(0.3))
textbox(s, Inches(0.5), Inches(6.35), Inches(12.3), Inches(0.6), [
    ("①決定要 server、②決定短碼怎麼來、③決定 redirect 怎麼回 —— 三者串成主架構，其下才是 16 項實作決策。", 13, MUTED, False)])

# ============ Slide 6: Create flow ============
s = prs.slides.add_slide(BLANK)
header(s, "05 · CREATE FLOW", "建立流程")
steps = [
    ("① 驗證", "保守正規化\n+ 惡意/SSRF 檢查", APP),
    ("② 生 token", "SHA-256+nonce\n→Base62→8 碼", GATEWAY),
    ("③ 寫 DB", "直接插入\nUNIQUE 重試 ≤10", DB),
    ("④ 暖 cache", "token→URL\n寫進 Redis", CACHE),
    ("⑤ 生圖", "預生成 QR\n→ object store", CDN),
    ("⑥ 回應", "token / short_url\n/ qr_code_url", ANALYTICS),
]
n = len(steps); bw = Inches(1.85); gap = Inches(0.18); x0 = Inches(0.55); y = Inches(2.7)
for i, (t, d, color) in enumerate(steps):
    x = x0 + i * (bw + gap)
    box(s, x, y, bw, Inches(1.5), t + "\n" + d, color, WHITE, 13)
    if i < n - 1:
        arrow(s, x + bw, y + Inches(0.75), x + bw + gap, y + Inches(0.75))
textbox(s, Inches(0.55), Inches(4.7), Inches(12), Inches(1.2), [
    ("關鍵：第 ③ 步「直接插入 + UNIQUE 例外重試」取代 repo 的「先查再插」，消除高並發 race。", 14, INK, True, 6),
    ("token 碰撞在大規模下必然發生，靠 DB UNIQUE + 重試兜底（nonce 只防『同 URL 同 token』，不防碰撞）。", 13, MUTED, False)])

# ============ Slide 7: Redirect flow ============
s = prs.slides.add_slide(BLANK)
header(s, "06 · REDIRECT FLOW", "掃描 / Redirect 流程（< 100ms 關鍵路徑）")
box(s, Inches(0.6), Inches(1.7), Inches(2.3), Inches(0.8), "GET /r/{token}\n掃描者瀏覽器", CLIENT, WHITE, 12)
b_cache = box(s, Inches(3.3), Inches(1.7), Inches(2.6), Inches(0.8), "① 查 Cache (Redis)", CACHE, WHITE, 13)
arrow(s, Inches(2.9), Inches(2.1), Inches(3.3), Inches(2.1))
# hit path
box(s, Inches(3.3), Inches(2.9), Inches(2.6), Inches(0.7), "命中 + 未過期", GREEN, WHITE, 12)
# miss path
b_db = box(s, Inches(6.4), Inches(1.7), Inches(2.7), Inches(0.8), "② miss → 查 DB\n(read replica)", DB, WHITE, 12)
arrow(s, Inches(5.9), Inches(2.1), Inches(6.4), Inches(2.1))
outs = [
    ("找不到", "404", RED, Inches(9.5), Inches(1.35)),
    ("is_deleted", "410", RED, Inches(9.5), Inches(2.25)),
    ("已過期", "410", RED, Inches(9.5), Inches(3.15)),
    ("正常", "③ 回填 cache", GREEN, Inches(9.5), Inches(4.05)),
]
for label, code, color, x, yy in outs:
    box(s, x, yy, Inches(3.3), Inches(0.7), f"{label}  →  {code}", color, WHITE, 12)
    arrow(s, Inches(9.1), Inches(2.1), x, yy + Inches(0.35), color=MUTED, width=1.2)
# scan + 302
box(s, Inches(3.3), Inches(4.9), Inches(5.8), Inches(0.8),
    "④ 排程非同步記 scan (BackgroundTasks) → 不擋跳轉", ANALYTICS, WHITE, 12)
box(s, Inches(3.3), Inches(5.9), Inches(5.8), Inches(0.8),
    "⑤ 回 302  Location: 原始 URL  → 瀏覽器自動跟隨", APP, WHITE, 13)
arrow(s, Inches(4.6), Inches(3.6), Inches(4.6), Inches(4.9), color=GREEN)
arrow(s, Inches(11.1), Inches(4.75), Inches(9.1), Inches(5.3), color=GREEN, width=1.2)

# ============ Slide: Latency / two-layer lookup ============
s = prs.slides.add_slide(BLANK)
header(s, "06b · LATENCY", "redirect < 100ms — 兩層查找 + 索引")
box(s, Inches(0.6), Inches(2.05), Inches(2.4), Inches(0.9), "掃描\nGET /r/{token}", CLIENT, WHITE, 13)
box(s, Inches(3.5), Inches(2.05), Inches(3.35), Inches(0.9), "① Cache (Redis)\nO(1) · <1ms", CACHE, WHITE, 13)
arrow(s, Inches(3.0), Inches(2.5), Inches(3.5), Inches(2.5))
box(s, Inches(3.5), Inches(3.25), Inches(3.35), Inches(0.8), "命中 → 302 ✅ 不碰 DB", GREEN, WHITE, 12)
arrow(s, Inches(5.17), Inches(2.95), Inches(5.17), Inches(3.25), color=GREEN, width=1.2)
box(s, Inches(7.35), Inches(2.05), Inches(5.4), Inches(0.9), "② miss → SELECT WHERE token=?\n走 ix_url_mappings_token · O(log n)", DB, WHITE, 12)
arrow(s, Inches(6.85), Inches(2.5), Inches(7.35), Inches(2.5))
box(s, Inches(7.35), Inches(3.25), Inches(5.4), Inches(0.8), "回填 cache → 302", APP, WHITE, 12)
arrow(s, Inches(10.05), Inches(2.95), Inches(10.05), Inches(3.25), color=MUTED, width=1.2)
bullets(s, Inches(0.6), Inches(4.5), Inches(12.3), Inches(2.6), [
    "索引對比（查 1 個 token、10 億筆）：無索引 = 全表掃描 O(n)，數秒；有索引 = B-tree O(log n)，次毫秒",
    "scan 記錄非同步（BackgroundTasks），不算進這條延遲",
    "三道防線：cache 擋多數讀（第 7 題）+ token 索引讓 miss 也快（第 2 題）+ scan 非同步不擋路（第 8 題）",
    "正式版再加 read replica 分攤 ~5,800 QPS（第 16 題）",
], size=14, gap=8)

# ============ Slide 8: QR image flow ============
s = prs.slides.add_slide(BLANK)
header(s, "07 · QR IMAGE", "取 QR 圖片流程  ·  GET /api/v1/qr/{token}/image")
steps = [
    ("① 驗證 token", "找不到/已軟刪\n→ 404", APP),
    ("② 組短碼", 'short_url =\nBASE_URL+"/r/{token}"', GATEWAY),
    ("③④ 算矩陣", "編碼→糾錯(RS)\n→2D 模組矩陣", DB),
    ("⑤ 算像素", "每模組→方塊\nborder=quiet zone", CDN),
    ("⑥⑦ PNG", "BytesIO\n選填 dimension 縮放", CACHE),
    ("⑧ 串流", "StreamingResponse\nimage/png", ANALYTICS),
]
n = len(steps); bw = Inches(1.9); gap = Inches(0.14); x0 = Inches(0.5); y = Inches(2.4)
for i, (t, d, color) in enumerate(steps):
    x = x0 + i * (bw + gap)
    box(s, x, y, bw, Inches(1.5), t + "\n" + d, color, WHITE, 12)
    if i < n - 1:
        arrow(s, x + bw, y + Inches(0.75), x + bw + gap, y + Inches(0.75))
textbox(s, Inches(0.55), Inches(4.4), Inches(12.2), Inches(2.4), [
    ("⚠ 編進 QR 的是『不變的短碼 /r/{token}』，不是原始 URL —— dynamic QR 關鍵：改目標 URL 不需重生圖。", 14, INK, True, 8),
    ("QR 原理：字串 → byte 編碼 → Reed-Solomon 糾錯（破損仍可掃）→ 排入矩陣（finder/timing/alignment + 遮罩）。", 13, MUTED, False, 6),
    ("圖片是靜態內容 → 原型即時生成；正式版預生成存 object store + CDN（第 15 題）。", 13, MUTED, False, 6),
    ("樣式參數：dimension（像素邊長）· color（hex 黑塊色）· border（quiet zone 模組數）。", 13, MUTED, False)])

# ============ Slide 9: Token decision ============
s = prs.slides.add_slide(BLANK)
header(s, "08 · DEEP DIVE", "決策 1 — Token 生成與碰撞")
bullets(s, Inches(0.7), Inches(1.9), Inches(11.8), Inches(5), [
    "演算法：SHA-256(url + nonce) → Base62 → 取前 8 碼",
    "nonce = 時間戳_重試次數，打破 SHA-256 的確定性",
    ("作用：讓同一個 URL 每次也產生不同 token（第 4 題：不去重）", 1),
    ("⚠ nonce 只解決『確定性』，不解決『碰撞』", 1),
    "碰撞數學：截斷成 8 碼後空間 = 62^8 ≈ 2.18×10^14",
    ("10 億規模下碰撞『必然』發生（預期 ~2,290 次）", 1),
    ("7 碼 → 8 碼：空間 ×62、預期碰撞降到 1/62", 1),
    "安全網：DB token UNIQUE + 直接插入例外重試（≤10）",
    ("每筆插入碰撞率 ~0.0005%，重試幾乎都第 2 次成功", 1),
], size=15, gap=8)

# ============ Slide: Token internals (Hash -> Encode) ============
s = prs.slides.add_slide(BLANK)
header(s, "08b · TOKEN INTERNALS", "Token 實作 — Hash → Encode")
stages = [
    ("url + nonce", "字串", CLIENT),
    (".encode()", "→ bytes (UTF-8)", GATEWAY),
    ("sha256().digest()", "→ 32 bytes 指紋", APP),
    ("int.from_bytes", "→ 256-bit 大整數", DB),
    ("base62 ÷62 取餘", "→ ~43 字元", CDN),
    ("[:8]", "→ token", ANALYTICS),
]
n = len(stages); bw = Inches(1.9); gap = Inches(0.13); x0 = Inches(0.5); y = Inches(2.25)
for i, (t, d, color) in enumerate(stages):
    x = x0 + i * (bw + gap)
    box(s, x, y, bw, Inches(1.3), t + "\n" + d, color, WHITE, 12)
    if i < n - 1:
        arrow(s, x + bw, y + Inches(0.65), x + bw + gap, y + Inches(0.65))
bullets(s, Inches(0.6), Inches(3.95), Inches(12.2), Inches(3), [
    "Hash（攪亂）：sha256(...).digest() 壓成固定 32 bytes 指紋 — 確定性·雪崩·單向（.hexdigest() 則回 64 字元 hex）",
    "Encode（變短碼）：大整數不斷 ÷62 記餘數 → 餘數 0–61 對映 0-9a-zA-Z；reversed 因餘數從最低位算起",
    ("選 Base62 而非 Base64：不含 / + = → URL 安全", 1),
    "截斷：62^43 ≈ 2^256，取前 8 碼 → 空間砍到 62^8（碰撞源於此）",
    "⚠ 兩個 encode：(url+nonce).encode() 是文字→bytes；base62_encode() 是 bytes→文字",
    "範例：3842 → 餘60='Y'、餘61='Z' → reversed → \"ZY\"",
], size=13, gap=7)

# ============ Slide: Token strategy comparison ============
s = prs.slides.add_slide(BLANK)
header(s, "08c · TOKEN 策略", "Token 生成策略比較（演進路徑）")
trows = [
    ("策略", "簡單度", "唯一性", "可預測", "適用場景"),
    ("Auto-increment", "高", "高", "高（危險）", "內部系統"),
    ("隨機字串 / UUID 截短", "中", "中", "低", "低流量"),
    ("純 hash(URL)", "中", "低", "低", "✗ 同 URL 同 token"),
    ("SHA-256+nonce+Base62", "中", "高", "低", "通用方案 ← 採用"),
    ("Pre-generated Pool", "低", "高", "低", "高流量 · 零碰撞"),
    ("Snowflake-like", "低", "高", "中", "分散式系統"),
]
HILITE = RGBColor(0xDB, 0xEA, 0xFE)
tt = s.shapes.add_table(len(trows), 5, Inches(0.6), Inches(1.75), Inches(12.15), Inches(3.6)).table
tt.columns[0].width = Inches(3.6)
for ci in (1, 2, 3):
    tt.columns[ci].width = Inches(1.75)
tt.columns[4].width = Inches(3.3)
for r in range(len(trows)):
    for c in range(5):
        cell = tt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        if r == 0:
            cell.fill.fore_color.rgb = ACCENT
        elif r == 4:
            cell.fill.fore_color.rgb = HILITE
        else:
            cell.fill.fore_color.rgb = WHITE if r % 2 else LIGHT
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = trows[r][c]
        bold = (r == 0) or (c == 0) or (r == 4)
        _font(run, 12, WHITE if r == 0 else INK, bold)
bullets(s, Inches(0.6), Inches(5.6), Inches(12.3), Inches(1.6), [
    "演進：隨機（碰撞靠運氣）→ 純 hash（同 URL 同 token、無法獨立追蹤）→ SHA-256+nonce（換 nonce 確定性重試 + DB 兜底）",
    "進階：Pre-generated Pool（寫入零碰撞，適合超高寫入）· Snowflake-like（分散式本地生成）",
    "我們選 SHA-256+nonce 為通用方案；寫入流量再上去可換 Pre-generated Pool",
], size=13, gap=6)

# ============ Slide 9: Redirect-speed decisions ============
s = prs.slides.add_slide(BLANK)
header(s, "09 · DEEP DIVE", "決策 2 — 302、快取、非同步")
left = [
    "302 而非 301",
    ("301 被瀏覽器快取 → 跳過我方 server", 1),
    ("→ 無法改/刪/統計；故選 302 每次回源", 1),
    "Cache：Redis（非行程內 dict）",
    ("stateless 多台共享 + 全域 invalidation", 1),
    ("行程內 dict 會讓 PATCH/DELETE 失效漏清", 1),
    ("含 TTL 作最終一致性保險", 1),
]
right = [
    "Scan 非同步寫",
    ("redirect 是 <100ms + ~5,800 QPS 熱路徑", 1),
    ("分析可容忍延遲/少量遺失 → 不擋跳轉", 1),
    ("原型 BackgroundTasks；正式版佇列+worker", 1),
    "token index",
    ("避免全表掃描，redirect 直接命中", 1),
]
bullets(s, Inches(0.7), Inches(1.9), Inches(6.0), Inches(5), left, size=14, gap=8)
bullets(s, Inches(6.9), Inches(1.9), Inches(6.0), Inches(5), right, size=14, gap=8)

# ============ Slide 10: Reliability / data ============
s = prs.slides.add_slide(BLANK)
header(s, "10 · DEEP DIVE", "決策 3 — 可靠性與資料語意")
bullets(s, Inches(0.7), Inches(1.9), Inches(11.8), Inches(5), [
    "軟刪除（is_deleted）：支撐 410 語意、保留分析、避免 token 回收風險",
    "錯誤語意：404（從未存在） vs 410（曾存在已刪/過期）",
    ("掃描路徑刪除回 410；管理 API 已刪回 404（刻意差異）", 1),
    "惰性過期 + cron 兜底：掃描即時檢查過期；cron 清過期/通知未點擊/物理清理",
    "URL 保守正規化：只 lower-case host，保留 path/query 與原 scheme",
    ("修正 repo 的整串小寫（破壞 path）+ 強制 https（導向失效站）", 1),
    "惡意阻擋：黑名單 + SSRF/內網位址（localhost·127.*·私網），正式版接 Safe Browsing",
], size=15, gap=9)

# ============ Slide 11: Scaling ============
s = prs.slides.add_slide(BLANK)
header(s, "11 · SCALING", "擴展策略")
bullets(s, Inches(0.7), Inches(1.9), Inches(11.8), Inches(5), [
    "App server stateless → 增加 instance 水平擴展（cache 外置 Redis）",
    "DB：單 primary + 多 read replica（讀走副）+ primary 掛 failover 升主",
    ("複寫延遲：create 後暖 cache 緩解", 1),
    "分片：以 token hash 為鍵（均勻），列為未來選項（200GB 目前單機足夠）",
    "QR 圖片：object store + CDN（靜態內容，目標 URL 改不需重生圖）",
    "分析：佇列 → 獨立分析庫 + 預聚合每日計數表",
    "清理 cron：清過期 / 通知後刪長期未點擊 / 物理清理超期軟刪 + invalidate cache",
], size=15, gap=9)

# ============ Slide: Read-heavy -> Read Replica ============
s = prs.slides.add_slide(BLANK)
header(s, "11b · READ REPLICA", "Read-heavy → 讀寫分離 + Read Replica")
textbox(s, Inches(0.6), Inches(1.8), Inches(6.0), Inches(3.2), [
    ("容量：1B × 200B = 200GB（單機放得下）", 15, INK, False, 8),
    ("讀流量：1 億 users × 5/日 = 5 億 redirects/日", 15, INK, False, 4),
    ("            5 億 ÷ 86,400s ≈ 5,787 redirects/秒", 16, ACCENT, True, 8),
    ("讀寫比：≈ 100:1（讀 ≫ 寫）", 15, INK, False, 12),
    ("→ 瓶頸是「讀吞吐」、不是容量", 15, INK, True, 4),
    ("→ 讀寫分離 + 多個 read replica", 16, GREEN, True),
])
# topology
b_app = box(s, Inches(6.7), Inches(2.5), Inches(1.8), Inches(1.0), "App\n(stateless)", APP, WHITE, 13)
box(s, Inches(9.4), Inches(1.9), Inches(3.3), Inches(0.8), "Primary (Write)", DB, WHITE, 13)
box(s, Inches(9.4), Inches(2.95), Inches(3.3), Inches(0.65), "Read Replica 1", GATEWAY, WHITE, 12)
box(s, Inches(9.4), Inches(3.75), Inches(3.3), Inches(0.65), "Read Replica 2", GATEWAY, WHITE, 12)
box(s, Inches(9.4), Inches(4.55), Inches(3.3), Inches(0.65), "Read Replica N", GATEWAY, WHITE, 12)
arrow(s, Inches(8.5), Inches(2.8), Inches(9.4), Inches(2.3), color=RED, width=1.6)         # 寫
arrow(s, Inches(8.5), Inches(3.15), Inches(9.4), Inches(3.27), color=GREEN, width=1.4)     # 讀
arrow(s, Inches(8.5), Inches(3.3), Inches(9.4), Inches(4.07), color=GREEN, width=1.4)
arrow(s, Inches(8.5), Inches(3.4), Inches(9.4), Inches(4.87), color=GREEN, width=1.4)
textbox(s, Inches(8.35), Inches(2.25), Inches(1.0), Inches(0.3), [("寫", 12, RED, True)])
textbox(s, Inches(8.3), Inches(3.5), Inches(1.0), Inches(0.3), [("讀", 12, GREEN, True)])
arrow(s, Inches(11.05), Inches(2.7), Inches(11.05), Inches(2.95), color=MUTED, width=1.1)  # 複寫
textbox(s, Inches(11.2), Inches(2.62), Inches(1.6), Inches(0.3), [("非同步複寫", 10, MUTED, False)])
bullets(s, Inches(0.6), Inches(5.5), Inches(12.3), Inches(1.7), [
    "寫走 primary、讀走 replica（load-balanced）；讀不夠就加 replica 水平擴讀",
    "配合 Redis cache，真正打到 DB 的讀已先被擋掉一大半",
    "failover：primary 掛 → 升一台 read replica 為新 primary；複寫延遲靠 create 後暖 cache 緩解",
], size=13, gap=6)

# ============ Slide 12: Improvements over repo ============
s = prs.slides.add_slide(BLANK)
header(s, "12 · IMPROVEMENTS", "對參考 repo 的關鍵改良")
items = [
    ("①", "先查再插 → 直接插入 + UNIQUE 例外重試", "消除高並發 race"),
    ("②", "行程內 dict cache → Redis", "stateless 一致性（invalidation 全域生效）"),
    ("③", "同步寫 scan → 非同步", "不擋 redirect 關鍵路徑"),
    ("④", "整串小寫 → 保守正規化", "避免破壞 path 大小寫 / 導向失效站"),
    ("＋", "token 7 碼 → 8 碼", "碰撞預期 ×1/62"),
]
y = Inches(1.85)
for num, change, why in items:
    box(s, Inches(0.7), y, Inches(0.7), Inches(0.8), num, ACCENT, WHITE, 18)
    card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.55), y, Inches(11.1), Inches(0.8))
    card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = LIGHT; card.shadow.inherit = False
    textbox(s, Inches(1.8), y + Inches(0.08), Inches(10.7), Inches(0.7), [
        (change, 15, INK, True, 2), (why, 12, MUTED, False)])
    y += Inches(1.0)

# ============ Slide: NoSQL alternative ============
s = prs.slides.add_slide(BLANK)
header(s, "附錄 · NOSQL", "替代方案 — DynamoDB 風格 key 設計")
box(s, Inches(0.6), Inches(1.65), Inches(6.0), Inches(0.6),
    "直覺方案  PK=user_id · SK=created_at#qr_token", ANALYTICS, WHITE, 12)
ca = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(2.3), Inches(6.0), Inches(1.9))
ca.fill.solid(); ca.fill.fore_color.rgb = LIGHT; ca.line.color.rgb = LIGHT; ca.shadow.inherit = False
bullets(s, Inches(0.8), Inches(2.45), Inches(5.6), Inches(1.7), [
    "✅ 列出我的 QR：Query PK=user_id 天生最佳",
    "❌ redirect 只有 token、沒 user_id → 定位不到分區",
    ("→ 退化成 Scan O(n)，違反 <100ms（致命）", 1),
], size=13, gap=6)
box(s, Inches(6.85), Inches(1.65), Inches(5.9), Inches(0.6),
    "建議  base PK=qr_token + GSI(user_id, created_at)", GREEN, WHITE, 12)
cb = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.85), Inches(2.3), Inches(5.9), Inches(1.9))
cb.fill.solid(); cb.fill.fore_color.rgb = LIGHT; cb.line.color.rgb = LIGHT; cb.shadow.inherit = False
bullets(s, Inches(7.05), Inches(2.45), Inches(5.5), Inches(1.7), [
    "✅ redirect：GetItem(PK=qr_token) O(1)、強一致",
    "✅ 列我的 QR：走 GSI(PK=user_id)",
    "qr_token 高亂度 → 均勻分區，避免 hot partition",
    "唯一性：條件寫入 attribute_not_exists",
], size=13, gap=5)
nrows = [
    ("存取模式", "SQL（現況）", "NoSQL PK=user_id", "NoSQL PK=qr_token"),
    ("redirect 查 token", "B-tree O(log n) ✅", "Scan O(n) ❌", "GetItem O(1) ✅"),
    ("列出我的 QR", "WHERE user_id ✅", "天生最佳 ✅", "GSI 查 ✅"),
    ("token 唯一", "UNIQUE ✅", "需額外處理", "條件寫入 ✅"),
]
nt = s.shapes.add_table(len(nrows), 4, Inches(0.6), Inches(4.5), Inches(12.15), Inches(2.1)).table
nt.columns[0].width = Inches(3.0)
for ci in (1, 2, 3):
    nt.columns[ci].width = Inches(3.05)
for r in range(len(nrows)):
    for c in range(4):
        cell = nt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = nrows[r][c]
        _font(run, 11, WHITE if r == 0 else (INK if c == 0 else MUTED), r == 0 or c == 0)
textbox(s, Inches(0.6), Inches(6.75), Inches(12.2), Inches(0.6),
        [("NoSQL 先列存取模式、再決定 key；redirect（token→URL）是這個系統的第一公民。", 12, MUTED, False)])

# ============ Slide: CDN edge redirect variant ============
s = prs.slides.add_slide(BLANK)
header(s, "附錄 · CDN EDGE", "CDN 邊緣 redirect 變體（可選最佳化）")
textbox(s, Inches(0.6), Inches(1.5), Inches(12.2), Inches(0.6), [
    ("主設計選擇不快取 redirect（回源保即時改/刪 + 分析）。302 與 CDN 無因果——302 仍可設短 TTL 讓 CDN 快取；", 12, MUTED, False, 2),
    ("真正取捨是「快取 redirect vs 即時性/分析」。本變體：熱門 token 的 302 設短 TTL、邊緣直接跳，換更低延遲。", 12, MUTED, False)])
box(s, Inches(0.6), Inches(2.2), Inches(2.2), Inches(0.95), "掃描者\nGET /r/{token}", CLIENT, WHITE, 12)
box(s, Inches(3.3), Inches(2.1), Inches(3.6), Inches(1.15),
    "CDN Edge（近使用者）\nedge cache: token→URL\n短 TTL（例 60s）", CDN, WHITE, 12)
arrow(s, Inches(2.8), Inches(2.65), Inches(3.3), Inches(2.65))
box(s, Inches(3.3), Inches(3.65), Inches(3.6), Inches(0.85),
    "命中 → CDN 直接回 302\n不回源 · 最低延遲", GREEN, WHITE, 12)
arrow(s, Inches(5.1), Inches(3.25), Inches(5.1), Inches(3.65), color=GREEN, width=1.3)
box(s, Inches(7.3), Inches(2.25), Inches(5.4), Inches(0.95),
    "miss → 回源 Service\n→ Cache/DB → 回 302（CDN 記短 TTL）", DB, WHITE, 12)
arrow(s, Inches(6.9), Inches(2.65), Inches(7.3), Inches(2.65))
nrows = [
    ("面向", "主設計（回源 + Redis）", "CDN 邊緣變體"),
    ("延遲", "低（回源 + Redis）", "最低（邊緣直接跳）"),
    ("改 / 刪即時性", "立即生效", "TTL 內延遲"),
    ("掃描分析", "每次都記", "TTL 內漏記"),
]
nt = s.shapes.add_table(len(nrows), 3, Inches(0.6), Inches(4.75), Inches(12.15), Inches(2.0)).table
nt.columns[0].width = Inches(3.0); nt.columns[1].width = Inches(4.55); nt.columns[2].width = Inches(4.6)
for r in range(len(nrows)):
    for c in range(3):
        cell = nt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = nrows[r][c]
        _font(run, 12, WHITE if r == 0 else (INK if c == 0 else MUTED), r == 0 or c == 0)
textbox(s, Inches(0.6), Inches(6.85), Inches(12.2), Inches(0.5), [
    ("預設不採用；列為超高流量、目標穩定的熱門 QR 之可選最佳化（短 TTL 控制誤差）。", 12, MUTED, False)])

# ============ Slide: Prototype -> Production gap ============
s = prs.slides.add_slide(BLANK)
header(s, "附錄 · PROD GAP", "從 Prototype 到 Production 的落差")
textbox(s, Inches(0.6), Inches(1.5), Inches(12.2), Inches(0.5), [
    ("動態 QR 把 server 變成 SPOF → caching + CDN + monitoring 不是加分項，是必要代價。", 13, MUTED, False)])
grows = [
    ("面向", "原型現況", "Production 需要"),
    ("Error handling", "驗證回 400，未全面", "全面驗證 · 結構化錯誤 · 不 crash"),
    ("Rate limiting", "✅ 已實作", "WAF per-IP + API GW 節流"),
    ("Auth & 多租戶", "✅ 已實作", "Cognito + JWT + owner_id 隔離"),
    ("Monitoring / Alerting", "✅ 已實作", "CloudWatch alarms→SNS + Logs + canary"),
    ("Data cleanup", "✅ 已實作", "EventBridge + Lambda 定時清理"),
    ("Caching / CDN", "記憶體 + 即時生圖", "Redis + object store + CDN"),
]
gt = s.shapes.add_table(len(grows), 3, Inches(0.6), Inches(2.15), Inches(12.15), Inches(4.0)).table
gt.columns[0].width = Inches(3.2); gt.columns[1].width = Inches(4.2); gt.columns[2].width = Inches(4.75)
RED_BG = RGBColor(0xFE, 0xE2, 0xE2)
for r in range(len(grows)):
    for c in range(3):
        cell = gt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        if r == 0:
            cell.fill.fore_color.rgb = ACCENT
        elif c == 1 and grows[r][1] == "無":
            cell.fill.fore_color.rgb = RED_BG
        else:
            cell.fill.fore_color.rgb = WHITE if r % 2 else LIGHT
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = grows[r][c]
        _font(run, 12, WHITE if r == 0 else INK, r == 0 or c == 0)
textbox(s, Inches(0.6), Inches(6.35), Inches(12.2), Inches(0.5), [
    ("rate limiting · auth/多租戶 · monitoring 是目前完全沒談、production 前必補的三塊。", 12, MUTED, False)])

# ============ Slide: AWS Deployment Architecture ============
s = prs.slides.add_slide(BLANK)
header(s, "部署 · AWS", "AWS 部署架構（Terraform，已上線）")
box(s, Inches(0.5), Inches(2.7), Inches(1.7), Inches(0.85), "Client", CLIENT, WHITE, 13)
box(s, Inches(2.45), Inches(2.7), Inches(2.0), Inches(0.85), "CloudFront\n(CDN + WAF)", CDN, WHITE, 12)
box(s, Inches(2.45), Inches(1.55), Inches(2.0), Inches(0.7), "S3 (QR PNG)\n/qr-img/*", ANALYTICS, WHITE, 11)
box(s, Inches(4.7), Inches(2.7), Inches(2.0), Inches(0.85), "API Gateway\n(JWT auth · 節流)", GATEWAY, WHITE, 12)
box(s, Inches(4.7), Inches(1.55), Inches(2.0), Inches(0.7), "Cognito\n(user pool)", DB, WHITE, 11)
arrow(s, Inches(5.7), Inches(2.7), Inches(5.7), Inches(2.25), color=MUTED, width=1.2)  # APIGW→Cognito(JWT)
box(s, Inches(6.95), Inches(2.7), Inches(1.6), Inches(0.85), "內部 ALB\n/health", APP, WHITE, 12)
box(s, Inches(8.8), Inches(2.7), Inches(2.1), Inches(0.95), "EC2 ASG\nDocker/gunicorn", INK, WHITE, 12)
box(s, Inches(8.8), Inches(3.95), Inches(2.1), Inches(0.7), "RDS PostgreSQL", DB, WHITE, 11)
box(s, Inches(8.8), Inches(4.8), Inches(2.1), Inches(0.7), "ElastiCache Redis", CACHE, WHITE, 11)
arrow(s, Inches(2.2), Inches(3.12), Inches(2.45), Inches(3.12))
arrow(s, Inches(3.45), Inches(2.7), Inches(3.45), Inches(2.25), color=MUTED, width=1.2)  # CF→S3
arrow(s, Inches(4.45), Inches(3.12), Inches(4.7), Inches(3.12))
arrow(s, Inches(6.7), Inches(3.12), Inches(6.95), Inches(3.12))
arrow(s, Inches(8.55), Inches(3.12), Inches(8.8), Inches(3.12))
arrow(s, Inches(9.85), Inches(3.65), Inches(9.85), Inches(3.95), color=MUTED, width=1.2)
arrow(s, Inches(9.85), Inches(4.65), Inches(9.85), Inches(4.8), color=MUTED, width=1.2)
textbox(s, Inches(4.7), Inches(3.62), Inches(2.2), Inches(0.3), [("VPC Link", 10, MUTED, False)])
textbox(s, Inches(0.5), Inches(5.7), Inches(12.3), Inches(1.4), [
    ("WAF(per-IP rate limit)掛在 CloudFront 邊緣;/qr-img/* → S3 長快取;/, /r/*, /api/* → API Gateway,redirect 不快取。", 13, INK, False, 4),
    ("設定/密鑰:SSM Parameter Store(DATABASE_URL/REDIS_URL/S3_BUCKET/CDN_BASE/BASE_URL/IMAGE_URI)·Secrets Manager(DB 密碼)·ECR·NAT Gateway。", 12, MUTED, False)])

# ============ Slide: Rate Limiting ============
s = prs.slides.add_slide(BLANK)
header(s, "安全 · RATE LIMITING", "防灌爆：WAF per-IP + API Gateway 節流（縱深）")
box(s, Inches(0.5), Inches(2.0), Inches(1.8), Inches(0.9), "掃描者 / Script", CLIENT, WHITE, 12)
box(s, Inches(2.65), Inches(2.0), Inches(2.5), Inches(0.9), "WAF (per-IP)\n超量 → 403", RED, WHITE, 12)
box(s, Inches(5.55), Inches(2.0), Inches(1.9), Inches(0.9), "CloudFront", CDN, WHITE, 12)
box(s, Inches(7.85), Inches(2.0), Inches(2.7), Inches(0.9), "API GW 整體節流\n超量 → 429", GATEWAY, WHITE, 12)
box(s, Inches(10.95), Inches(2.0), Inches(1.85), Inches(0.9), "ALB → EC2", APP, WHITE, 11)
arrow(s, Inches(2.3), Inches(2.45), Inches(2.65), Inches(2.45))
arrow(s, Inches(5.15), Inches(2.45), Inches(5.55), Inches(2.45))
arrow(s, Inches(7.45), Inches(2.45), Inches(7.85), Inches(2.45))
arrow(s, Inches(10.55), Inches(2.45), Inches(10.95), Inches(2.45))
rlrows = [
    ("層", "機制", "上限", "超量"),
    ("CloudFront WAF", "per-IP · /api/*", "300 / 5 分 / IP", "403"),
    ("CloudFront WAF", "per-IP · 全域", "2000 / 5 分 / IP", "403"),
    ("API Gateway", "整體 throttle", "1000 req/s · burst 2000", "429"),
]
rt = s.shapes.add_table(len(rlrows), 4, Inches(0.6), Inches(3.3), Inches(12.15), Inches(2.0)).table
rt.columns[0].width = Inches(3.0); rt.columns[1].width = Inches(3.0); rt.columns[2].width = Inches(4.15); rt.columns[3].width = Inches(2.0)
for r in range(len(rlrows)):
    for c in range(4):
        cell = rt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = rlrows[r][c]
        _font(run, 12, WHITE if r == 0 else INK, r == 0 or c == 0)
bullets(s, Inches(0.6), Inches(5.55), Inches(12.3), Inches(1.6), [
    "為何不能只靠 API GW:HTTP API 內建節流是『整體』非 per-IP、也無 API key → 擋單一 IP 濫用必須靠 WAF",
    "限速值皆 Terraform 變數可調;WAF 阻擋數進 CloudWatch（BlockedRequests）",
    "純 infra 變更,不動 app;已 validate/plan 通過(未 apply)",
], size=13, gap=7)

# ============ Slide: Auth & Isolation ============
s = prs.slides.add_slide(BLANK)
header(s, "安全 · AUTH & 多租戶", "Cognito + API GW JWT authorizer + owner_id 隔離")
box(s, Inches(0.5), Inches(2.0), Inches(2.2), Inches(0.9), "前端\n(管理頁)", CLIENT, WHITE, 12)
box(s, Inches(3.0), Inches(2.0), Inches(2.6), Inches(0.9), "Cognito Hosted UI\n登入(PKCE)→ id token", DB, WHITE, 11)
box(s, Inches(5.95), Inches(2.0), Inches(2.8), Inches(0.9), "API GW JWT authorizer\n未帶/無效 → 401", GATEWAY, WHITE, 11)
box(s, Inches(9.1), Inches(2.0), Inches(3.4), Inches(0.9), "EC2:取 sub=owner_id\n依 owner 過濾/授權", APP, WHITE, 11)
arrow(s, Inches(2.7), Inches(2.45), Inches(3.0), Inches(2.45))
arrow(s, Inches(5.6), Inches(2.45), Inches(5.95), Inches(2.45))
arrow(s, Inches(8.75), Inches(2.45), Inches(9.1), Inches(2.45))
nrows = [
    ("", "需登入 (JWT)", "公開 (無 auth)"),
    ("端點", "create / list / get / patch / delete / analytics", "/r/{token} 掃描 · QR 圖 · /health"),
    ("隔離", "owner_id = Cognito sub;非擁有者一律 404", "redirect/圖不分 owner"),
]
nt = s.shapes.add_table(len(nrows), 3, Inches(0.6), Inches(3.4), Inches(12.15), Inches(1.7)).table
nt.columns[0].width = Inches(1.6); nt.columns[1].width = Inches(6.35); nt.columns[2].width = Inches(4.2)
for r in range(len(nrows)):
    for c in range(3):
        cell = nt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = nrows[r][c]
        _font(run, 12, WHITE if r == 0 else INK, r == 0 or c == 0)
bullets(s, Inches(0.6), Inches(5.4), Inches(12.3), Inches(1.6), [
    "前端用 id token(帶 aud+email);API GW authorizer 在邊緣擋未授權,app 再取 sub 當 owner_id",
    "env-gated:本機不設 AUTH_ENABLED → dev user『local-dev』、免登入,SQLite 照跑",
    "純 env 開關 + 新 infra(Cognito/authorizer),已 validate/plan 通過(未 apply)",
], size=13, gap=7)

# ============ Slide: Data Cleanup Cron ============
s = prs.slides.add_slide(BLANK)
header(s, "可靠性 · DATA CLEANUP", "定時清理:EventBridge + Lambda（防 DB bloat）")
box(s, Inches(0.6), Inches(2.1), Inches(3.0), Inches(0.95), "EventBridge Scheduler\n每日 03:00 UTC", ANALYTICS, WHITE, 12)
box(s, Inches(4.0), Inches(2.1), Inches(3.0), Inches(0.95), "Lambda (VPC)\npg8000 純 Python", APP, WHITE, 12)
box(s, Inches(7.4), Inches(2.1), Inches(2.6), Inches(0.95), "RDS PostgreSQL\nDELETE", DB, WHITE, 12)
arrow(s, Inches(3.6), Inches(2.57), Inches(4.0), Inches(2.57))
arrow(s, Inches(7.0), Inches(2.57), Inches(7.4), Inches(2.57))
crows = [
    ("清理目標", "條件", "預設保留"),
    ("過期 QR", "expires_at < now - grace", "30 天"),
    ("軟刪除超期", "is_deleted 且 updated_at < now - retention", "30 天"),
    ("連帶", "上述 token 的 scan_events 一併刪", "—"),
]
ct = s.shapes.add_table(len(crows), 3, Inches(0.6), Inches(3.5), Inches(12.15), Inches(1.9)).table
ct.columns[0].width = Inches(2.6); ct.columns[1].width = Inches(7.05); ct.columns[2].width = Inches(2.5)
for r in range(len(crows)):
    for c in range(3):
        cell = ct.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = crows[r][c]
        _font(run, 12, WHITE if r == 0 else INK, r == 0 or c == 0)
bullets(s, Inches(0.6), Inches(5.6), Inches(12.3), Inches(1.4), [
    "單一來源:cleanup.py(SQLAlchemy+text SQL)Lambda 與本機共用;Lambda 用純 Python pg8000 免二進位打包",
    "與 app 實例解耦(不用 app 內排程,免多實例重複跑/leader election);保留天數皆 Terraform 變數可調",
], size=13, gap=8)

# ============ Slide: Monitoring / Alerting ============
s = prs.slides.add_slide(BLANK)
header(s, "可靠性 · MONITORING", "CloudWatch alarms → SNS + Logs + Dashboard + Canary")
box(s, Inches(0.6), Inches(2.0), Inches(3.2), Inches(0.9), "CloudWatch Alarms\nALB/RDS/EC2/API GW/Lambda/CF", APP, WHITE, 11)
box(s, Inches(4.3), Inches(2.0), Inches(2.6), Inches(0.9), "SNS topic\n→ Email 通知", ANALYTICS, WHITE, 12)
box(s, Inches(7.4), Inches(2.0), Inches(2.6), Inches(0.9), "Synthetic Canary\n每 5 分 /health", DB, WHITE, 12)
arrow(s, Inches(3.8), Inches(2.45), Inches(4.3), Inches(2.45))
arrow(s, Inches(7.4), Inches(2.45), Inches(6.9), Inches(2.45), color=MUTED, width=1.2)  # canary→alarms
mrows = [
    ("面向", "內容"),
    ("告警 alarms", "ALB unhealthy/5xx/latency · RDS CPU/storage · ElastiCache · API GW 5xx · Lambda errors · CloudFront 5xx(us-east-1)"),
    ("通知", "CloudWatch alarm → SNS topic → email（alert_email,需點確認信）"),
    ("App logs", "EC2 容器 awslogs driver → CloudWatch Logs /qrcode/app（retention 14d）"),
    ("Dashboard", "qrcode-overview：ALB/RDS/EC2/API GW/Lambda 一頁"),
    ("Canary", "syn-nodejs-puppeteer 每 5 分探測 CloudFront /health → 失敗即 alarm"),
]
mt = s.shapes.add_table(len(mrows), 2, Inches(0.6), Inches(3.3), Inches(12.15), Inches(2.9)).table
mt.columns[0].width = Inches(2.2); mt.columns[1].width = Inches(9.95)
for r in range(len(mrows)):
    for c in range(2):
        cell = mt.cell(r, c)
        cell.margin_top = Pt(2); cell.margin_bottom = Pt(2); cell.margin_left = Pt(8)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT if r == 0 else (WHITE if r % 2 else LIGHT)
        p = cell.text_frame.paragraphs[0]
        run = p.add_run(); run.text = mrows[r][c]
        _font(run, 12, WHITE if r == 0 else INK, r == 0 or c == 0)
bullets(s, Inches(0.6), Inches(6.4), Inches(12.3), Inches(0.8), [
    "解決『服務掛了沒人知道』;canary 是端到端外部探測(連 CloudFront/整鏈路掛掉都測得到)。純 infra,已 validate/plan 通過(未 apply)。",
], size=12, gap=4)

# ============ Slide: CI/CD Flow ============
s = prs.slides.add_slide(BLANK)
header(s, "部署 · CI/CD", "CI/CD 流程（GitHub Actions + OIDC）")
steps = [
    ("git push", "QR Code Generator/**", CLIENT),
    ("GitHub Actions", "OIDC assume role\n(無長期金鑰)", GATEWAY),
    ("buildx", "--platform\nlinux/arm64", APP),
    ("ECR push", "tag = SHA\n+ latest", DB),
    ("SSM 更新", "/qrcode/\nIMAGE_URI", CDN),
    ("SSM 部署", "→ EC2\ndeploy-app.sh", ANALYTICS),
    ("上線", "ALB /health\n通過", GREEN),
]
n = len(steps); bw = Inches(1.62); gp = Inches(0.13); x0 = Inches(0.5); y = Inches(2.5)
for i, (t, d, color) in enumerate(steps):
    x = x0 + i * (bw + gp)
    box(s, x, y, bw, Inches(1.4), t + "\n" + d, color, WHITE, 11)
    if i < n - 1:
        arrow(s, x + bw, y + Inches(0.7), x + bw + gp, y + Inches(0.7))
bullets(s, Inches(0.6), Inches(4.4), Inches(12.3), Inches(2.4), [
    "EC2 deploy-app.sh:從 SSM 撈設定 → ECR login → docker pull → docker run(換新容器)",
    "全 env-gated:本機無這些環境變數 → 維持 SQLite + 記憶體 cache + 即時生圖,不受影響",
    "部署踩到的兩個坑:① EC2 role 少 s3:PutObject(500)② CI build amd64 但 EC2 arm64(502)→ buildx 跨平台修正",
], size=14, gap=9)

# ============ Slide 13: Status ============
s = prs.slides.add_slide(BLANK)
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
bg.fill.solid(); bg.fill.fore_color.rgb = INK; bg.line.fill.background(); bg.shadow.inherit = False
textbox(s, Inches(0.9), Inches(1.2), Inches(11.5), Inches(1.2), [
    ("原型狀態", 16, ACCENT, True, 4), ("可跑的 FastAPI 實作 + 管理頁前端", 30, WHITE, True)])
bullets_dark = [
    "7 個 API 端點 + 管理頁前端（建立/清單/編輯/刪除/分析）",
    "全部 curl 驗證通過：302 / 404 / 410 / 8 碼 token / 正規化 / SSRF / 過期",
    "production 替換點已在程式註解標明（Redis / 佇列 / CDN / 預聚合 / PostgreSQL）",
    "設計依據：DESIGN.md（16 題決策 + 優劣分析）",
]
tb = s.shapes.add_textbox(Inches(0.9), Inches(3.0), Inches(11.5), Inches(3))
tf = tb.text_frame; tf.word_wrap = True
for i, it in enumerate(bullets_dark):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_after = Pt(12)
    r = p.add_run(); r.text = "✓  " + it
    _font(r, 16, RGBColor(0xE2, 0xE8, 0xF0), False)

# ============ Slide: 下週展開（Prototype → Production gap）============
s = prs.slides.add_slide(BLANK)
header(s, "下週預告 · NEXT WEEK", "從 Prototype 到 Production 的巨大鴻溝")
textbox(s, Inches(0.6), Inches(1.5), Inches(12), Inches(0.4),
        [("快速掃過，建立全貌 — 下週 Deep Dive 才是主菜", 14, MUTED, False)])

gap_cards = [
    ("[Error Handling]", "遇到不合法輸入直接 Crash。", ANALYTICS),
    ("[Rate Limiting]", "毫無防護，極易被 Script 惡意灌爆 API。", ANALYTICS),
    ("[Auth & Isolation]", "缺乏多租戶概念，資料全公開。", ANALYTICS),
    ("[Monitoring]", "服務掛了完全沒有 Alerting，處於盲飛狀態。", GATEWAY),
    ("[Data Cleanup]", "過期資料永不刪除，一年後 DB 將面臨嚴重 Bloat。", GATEWAY),
    ("[Caching / CDN]", "每次轉址都直擊 DB，流量一來直接癱瘓。\n→ 下週 Deep Dive 展開", GATEWAY),
]
cw = Inches(3.95); ch = Inches(1.75); gx = Inches(0.27); gy = Inches(0.3)
x0 = Inches(0.6); y0 = Inches(2.15)
for i, (title, desc, accent) in enumerate(gap_cards):
    col = i % 3
    row = i // 3
    x = x0 + col * (cw + gx)
    y = y0 + row * (ch + gy)
    card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, ch)
    card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = LIGHT; card.shadow.inherit = False
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, Inches(0.09), ch)
    bar.fill.solid(); bar.fill.fore_color.rgb = accent; bar.line.fill.background(); bar.shadow.inherit = False
    textbox(s, x + Inches(0.3), y + Inches(0.2), cw - Inches(0.5), ch - Inches(0.3),
            [(title, 16, INK, True, 8), (desc, 13, MUTED, False)])

# ============ Slide: 下週預告 — 壓力測試與極限擴展 ============
s = prs.slides.add_slide(BLANK)
header(s, "下週預告 · DEEP DIVE", "壓力測試與極限擴展 (Deep Dive)")
# 左：QPS 暴增
textbox(s, Inches(0.6), Inches(2.2), Inches(6.0), Inches(3.2), [
    ("1,000 QPS  →  50,000 QPS", 26, ACCENT, True, 16),
    ("當大型客戶上線，", 19, INK, True, 4),
    ("流量瞬間暴增 50 倍，", 19, INK, True, 4),
    ("這個架構哪會先崩潰？", 19, INK, True),
])
# 右：主題 bullets
bullets(s, Inches(7.0), Inches(2.25), Inches(5.8), Inches(2.4), [
    "流量暴增 Scenario 推演（Server vs DB vs Network）",
    "Event-Driven Analytics Pipeline 架構",
    "System Design 面試實戰答題框架",
], size=16, gap=14)
# 右下：課前準備
prep = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(7.0), Inches(4.75), Inches(5.8), Inches(1.15))
prep.fill.solid(); prep.fill.fore_color.rgb = LIGHT; prep.line.color.rgb = LIGHT; prep.shadow.inherit = False
pbar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7.0), Inches(4.75), Inches(0.09), Inches(1.15))
pbar.fill.solid(); pbar.fill.fore_color.rgb = GATEWAY; pbar.line.fill.background(); pbar.shadow.inherit = False
textbox(s, Inches(7.3), Inches(4.95), Inches(5.3), Inches(0.9), [
    ("課前準備", 15, INK, True, 6), ("複習教材 Deep Dive 章節", 14, MUTED, False)])

prs.save("QR_Code_Generator.pptx")
print("saved QR_Code_Generator.pptx ·", len(prs.slides._sldIdLst), "slides")
