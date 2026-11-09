"""A live doc as .docx (opens as a Google Doc when dropped into Drive). A snapshot: it does not
update. HTML banners cannot be turned into images on the server, so the file points to the live link."""

import io
import re
from html.parser import HTMLParser

from docx import Document
from docx.shared import Pt, RGBColor


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li", "h1", "h2", "h3", "tr"):
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def plain(html: str | None) -> str:
    if not html:
        return ""
    p = _Text()
    p.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.out)).strip()


def _field(doc, label: str, value: str | None):
    if not value:
        return
    par = doc.add_paragraph()
    par.paragraph_format.space_after = Pt(2)
    par.add_run(f"{label}: ").bold = True
    par.add_run(value)


def to_docx(d: dict, link: str) -> bytes:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10.5)
    eyebrow = doc.add_paragraph()
    r = eyebrow.add_run("OFFERS IN PLAN")
    r.bold, r.font.size, r.font.color.rgb = True, Pt(8), RGBColor(0x6B, 0x72, 0x80)
    doc.add_heading(d["name"], level=0)
    meta = " · ".join(filter(None, [", ".join(s["name"] for s in d["stores"]), f'{d["start_date"]} to {d["end_date"]}',
                                    " ".join(t["name"] for t in d["tags"])]))
    doc.add_paragraph(meta)
    doc.add_paragraph(f"Snapshot of the live doc: {link}")
    if d.get("custom_top"):
        doc.add_paragraph(plain(d["custom_top"]))
    pf = d.get("period_forecast")
    if pf:
        doc.add_heading("Forecast", level=1)
        if pf.get("missing"):
            doc.add_paragraph(pf["missing"])
        else:
            _field(doc, f"Revenue · {pf['label']}" if pf.get("label") else "Revenue",
                   " · ".join(filter(None, [pf["revenue"], pf.get("original")])))
            _field(doc, "Forecast AOV", " · ".join(filter(None, [pf.get("aov"), pf.get("current_aov") and f"current {pf['current_aov']}"])))
            _field(doc, "Orders (estimate)", f"about {pf['orders']:,} (revenue / forecast AOV)" if pf.get("orders") is not None else None)
            _field(doc, "Growth", pf.get("growth"))
    if d["events"]:
        doc.add_heading("Events", level=1)
        store = None
        for e in d["events"]:
            if e["store_code"] != store:
                store = e["store_code"]
                doc.add_paragraph(f'{e["store_code"]} · {e["store_name"]}'.upper()).runs[0].bold = True
            doc.add_heading(e["name"], level=2)
            doc.add_paragraph(" · ".join([e["when"], *[t["name"] for t in e["tags"]]]))
            if e["banner"]:
                doc.add_paragraph("Banners: see the live doc.")
            for i, m in enumerate(e["emails"], start=1):
                _field(doc, f"Email {i}" if len(e["emails"]) > 1 else "Email",
                       " · ".join(filter(None, [m["subject"], m["preview"] and f"preview: {m['preview']}"])))
            _field(doc, "Offer", e["offer"])
            _field(doc, "Displayed", e["display_text"])
            _field(doc, "Coupon code", " / ".join(e["codes"]))
            _field(doc, "Threshold", e["threshold"])
            _field(doc, "Activation", e["activation"])
            if e["bonus"]:
                _field(doc, "Free shipping", e["bonus"]["free_shipping"])
                _field(doc, "Give-away product", e["bonus"]["giveaway_product"])
                _field(doc, "Bonus", e["bonus"]["free_gift"])
            _field(doc, "Products", e["products"])
            _field(doc, "Advertising", ", ".join(c["name"] for c in e["channels"]))
            f = e["forecast"]
            if f:
                _field(doc, "Forecast", " · ".join(filter(None, [f"{f['orders']} orders" if f["orders"] else None,
                                                                 f"{f['uses']} code uses" if f["uses"] else None,
                                                                 f"AOV {f['aov']}" if f["aov"] else None])))
            _field(doc, "Extra details", e["extra_details"])
    if d["focus"]:
        doc.add_heading("Products to focus", level=1)
        t = doc.add_table(rows=1, cols=4)
        t.style = "Light List"
        for i, h in enumerate(["Product", "SKU", "Performance", "Extra tags"]):
            t.rows[0].cells[i].text = h
        for p in d["focus"]:
            c = t.add_row().cells
            c[0].text, c[1].text, c[2].text, c[3].text = p["name"], p["sku"] or "", p["perf"] or "", ", ".join(p["tags"])
    if d["product_offers"]:
        doc.add_heading("Product offers", level=1)
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light List"
        for i, h in enumerate(["Name", "SKU", "Dedicated offer"]):
            t.rows[0].cells[i].text = h
        for p in d["product_offers"]:
            c = t.add_row().cells
            c[0].text, c[1].text, c[2].text = p["name"], p["sku"] or "", p["offer"] or ""
    if d["extra_activities"]:
        doc.add_heading("Extra activities", level=1)
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light List"
        for i, h in enumerate(["Activity", "What the customer gets", "Price / value"]):
            t.rows[0].cells[i].text = h
        for a in d["extra_activities"]:
            c = t.add_row().cells
            c[0].text, c[1].text, c[2].text = a["name"], a["description"] or "", a["price"] or ""
    if d.get("custom_bottom"):
        doc.add_paragraph(plain(d["custom_bottom"]))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
