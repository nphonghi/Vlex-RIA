import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Union

# Import ria_components to register OllamaBackend and custom players
from ria_components import (
    RIACouncilArena,
    RIAPaperReviewArena,
    initialize_ria_players,
    LegalDocumentExtractorPlayer,
)
from colorama import Fore
from vlex_ria.environments import RIAReview, PaperReview
from vlex_ria.experiment_config import all_settings
from vlex_ria.ria_settings import get_experiment_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def render_state_standard_html(
    report_markdown: str,
    document_name: str,
    model_name: str,
    agency_name: str = "BỘ TƯ PHÁP",
    report_number: str = "/BC-BTP",
    location_name: str = "Hà Nội",
    signer_title: str = "KT. BỘ TRƯỞNG\nTHỨ TRƯỞNG",
) -> str:
    """Render the Area Chair RIA report into Vietnamese state administrative HTML format."""
    import html
    import re
    from ria_components import LegalDocumentExtractorPlayer

    # Auto-extract draft title and lead agency from report closing sentence when defaults or raw filenames are passed
    closing_match = re.search(
        r"Trên đây là nội dung Báo cáo đánh giá tác động của chính sách (?:của|trong)\s+([^,\n]+),\s*([^\n]+?)\s+kính báo cáo\./\.",
        report_markdown or "",
    )
    extracted_draft_title = closing_match.group(1).strip() if closing_match else ""
    extracted_agency = closing_match.group(2).strip() if closing_match else ""

    clean_doc_title = (document_name or "").strip()
    clean_doc_title = re.sub(r"^\((.+)\)$", r"\1", clean_doc_title).strip()
    clean_doc_title = re.sub(r"\s*\(\d+\)\s*$", "", clean_doc_title).strip()
    if (
        not clean_doc_title
        or clean_doc_title.lower().endswith((".docx", ".pdf", ".txt", ".md"))
        or re.match(r"^\d+_\d{4}_", clean_doc_title)
    ):
        clean_doc_title = extracted_draft_title or clean_doc_title

    clean_lower = clean_doc_title.lower()
    if clean_lower.startswith(("dự thảo", "đề nghị xây dựng", "dự án")):
        report_sub_title = f"Đánh giá tác động của chính sách trong {clean_doc_title[0].lower() + clean_doc_title[1:]}"
    elif clean_doc_title:
        report_sub_title = f"Đánh giá tác động của chính sách trong dự thảo {clean_doc_title}"
    else:
        report_sub_title = "Đánh giá tác động của chính sách"

    clean_agency = re.sub(r"\s*\(\d+\)\s*", "", agency_name or "").strip()
    if clean_agency in ["", "BỘ / CƠ QUAN CHỦ TRÌ SOẠN THẢO", "BỘ TƯ PHÁP"] and extracted_agency:
        clean_agency = extracted_agency.upper()
    elif not clean_agency:
        clean_agency = "BỘ TƯ PHÁP"
    clean_agency = clean_agency.upper()

    clean_report_num = re.sub(r"\.\.\.\(\d+\)\.\.\.", "BTP", report_number or "").strip()
    clean_report_num = re.sub(r"^\.+", "", clean_report_num).strip()
    if clean_report_num in ["", "/BC-BTP"] and extracted_agency:
        abbr = LegalDocumentExtractorPlayer.MINISTRY_ABBR_MAP.get(extracted_agency, "BTP")
        clean_report_num = f"/BC-{abbr}"
    if not clean_report_num.startswith("/"):
        clean_report_num = f"/{clean_report_num.lstrip('/')}"

    clean_signer = re.sub(r"\s*\(\d+\)\s*", "", signer_title or "").strip()
    if not clean_signer or clean_signer == "QUYỀN HẠN, CHỨC VỤ CỦA NGƯỜI KÝ":
        clean_signer = "KT. BỘ TRƯỞNG<br/>THỨ TRƯỞNG"
    else:
        clean_signer = html.escape(clean_signer).replace("\n", "<br/>")

    escaped = html.escape(report_markdown or "")
    lines = escaped.splitlines()
    html_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# MẪU SỐ 01") or stripped.startswith("# BÁO CÁO") or stripped.startswith("```"):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            if re.match(r"^\|\s*[:-]+", stripped):
                continue
            if not in_table:
                html_lines.append('<table class="ria-table">')
                in_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            row_html = "".join(f"<td>{c}</td>" for c in cells)
            html_lines.append(f"<tr>{row_html}</tr>")
            continue
        else:
            if in_table:
                html_lines.append("</table>")
                in_table = False

        roman_m = re.match(r"^(?:#+\s*)?(?:\*\*)?([IVX]+\.\s+[^\n*]+?)(?:\*\*)?$", stripped)
        sub_m = re.match(r"^(?:#+\s*)?(?:\*\*)?(\d+\.\d+\.?\s+[^\n*]+?)(?:\*\*)?$", stripped)
        if roman_m:
            html_lines.append(f'<p class="sec-roman">{roman_m.group(1).strip()}</p>')
        elif sub_m:
            html_lines.append(f'<p class="sec-sub">{sub_m.group(1).strip()}</p>')
        elif stripped.startswith("# "):
            html_lines.append(f'<p class="sec-roman">{stripped[2:].strip().strip("*")}</p>')
        elif stripped.startswith("## "):
            html_lines.append(f'<p class="sec-num">{stripped[3:].strip().strip("*")}</p>')
        elif stripped.startswith("### "):
            html_lines.append(f'<p class="sec-sub">{stripped[4:].strip().strip("*")}</p>')
        elif stripped.startswith("#### "):
            html_lines.append(f'<p class="sec-sub">{stripped[5:].strip().strip("*")}</p>')
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:].strip()
            impact_m = re.match(r"^(Tác động[^:]+:)(.*)$", content)
            if impact_m:
                html_lines.append(f'<p class="body-para">- <strong>{impact_m.group(1)}</strong>{impact_m.group(2)}</p>')
            else:
                html_lines.append(f'<p class="body-para">- {content}</p>')
        elif stripped.startswith("+ "):
            html_lines.append(f'<p class="body-para">+ {stripped[2:].strip()}</p>')
        elif stripped == "---":
            continue
        elif stripped == "":
            continue
        else:
            html_lines.append(f'<p class="body-para">{stripped}</p>')

    if in_table:
        html_lines.append("</table>")

    body_html = "\n".join(html_lines)
    body_html = re.sub(r"&lt;br\s*/?&gt;", "<br/>", body_html, flags=re.IGNORECASE)
    body_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body_html)
    body_html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", body_html)
    body_html = re.sub(r"`(.+?)`", r"\1", body_html)

    now_dt = datetime.now()
    date_str = f"{html.escape(location_name)}, ngày {now_dt.day:02d} tháng {now_dt.month:02d} năm {now_dt.year}"
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8"/>
<title>Báo cáo đánh giá tác động của chính sách - {html.escape(clean_doc_title)}</title>
<style>
  body {{
    font-family: "Times New Roman", Times, serif;
    font-size: 14pt;
    line-height: 1.5;
    color: #000;
    max-width: 210mm;
    margin: 20px auto;
    padding: 20mm 15mm 20mm 25mm;
    background: #fff;
    box-shadow: 0 2px 12px rgba(0,0,0,0.12);
  }}
  .header-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 16px;
  }}
  .header-table td {{
    vertical-align: top;
    text-align: center;
    border: none;
    padding: 0 4px;
    line-height: 1.35;
  }}
  .agency-title {{
    font-size: 12pt;
    font-weight: bold;
    text-transform: uppercase;
    white-space: nowrap;
  }}
  .agency-underline {{
    width: 115px;
    border-bottom: 1px solid #000;
    margin: 3px auto 6px auto;
  }}
  .doc-number {{
    font-size: 13pt;
    margin-top: 4px;
  }}
  .draft-badge {{
    font-size: 13pt;
    font-weight: bold;
    text-decoration: underline;
    margin-top: 8px;
  }}
  .nation-title {{
    font-size: 12pt;
    font-weight: bold;
    text-transform: uppercase;
    white-space: nowrap;
  }}
  .motto-title {{
    font-size: 13.5pt;
    font-weight: bold;
  }}
  .motto-underline {{
    width: 175px;
    border-bottom: 1px solid #000;
    margin: 3px auto 8px auto;
  }}
  .date-line {{
    font-size: 13.5pt;
    font-style: italic;
  }}
  .report-title {{
    text-align: center;
    margin: 16px 0 18px 0;
    line-height: 1.35;
  }}
  .title-main {{
    font-size: 14pt;
    font-weight: bold;
    text-transform: uppercase;
  }}
  .title-sub {{
    font-size: 13.5pt;
    font-weight: bold;
    line-height: 1.4;
    margin-top: 4px;
  }}
  .title-doc {{
    font-size: 14pt;
    font-weight: bold;
    margin-top: 2px;
  }}
  .title-underline {{
    width: 155px;
    border-bottom: 1px solid #000;
    margin: 8px auto 0 auto;
  }}
  p.sec-roman {{
    font-size: 14pt;
    font-weight: bold;
    text-transform: uppercase;
    text-indent: 1.27cm;
    text-align: justify;
    margin: 8px 0 4px 0;
  }}
  p.sec-num {{
    font-size: 14pt;
    font-weight: bold;
    text-indent: 1.27cm;
    text-align: justify;
    margin: 6px 0 4px 0;
  }}
  p.sec-sub {{
    font-size: 14pt;
    font-weight: normal;
    text-indent: 1.27cm;
    text-align: justify;
    margin: 4px 0;
  }}
  p.body-para {{
    font-size: 14pt;
    text-indent: 1.27cm;
    text-align: justify;
    margin: 4px 0;
  }}
  .ria-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 14px 0;
    font-size: 12.5pt;
    line-height: 1.35;
  }}
  .ria-table td {{
    border: 1px solid #000;
    padding: 6px 8px;
    vertical-align: top;
    text-align: left;
    text-indent: 0;
  }}
  .ria-table tr:first-child td {{
    font-weight: bold;
    text-align: center;
  }}
  .footer-table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 22px;
  }}
  .footer-table td {{
    vertical-align: top;
    border: none;
    padding: 4px;
  }}
  @page {{
    size: A4;
    margin: 20mm 15mm 20mm 25mm;
  }}
  @media print {{
    body {{
      box-shadow: none;
      margin: 0;
      padding: 0;
      max-width: 100%;
    }}
  }}
</style>
</head>
<body>
  <table class="header-table">
    <tr>
      <td style="width: 42%;">
        <div class="agency-title">{html.escape(clean_agency)}</div>
        <div class="agency-underline"></div>
        <div class="doc-number">Số: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{html.escape(clean_report_num)}</div>
        <div class="draft-badge">Dự thảo</div>
      </td>
      <td style="width: 58%;">
        <div class="nation-title">CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</div>
        <div class="motto-title">Độc lập - Tự do - Hạnh phúc</div>
        <div class="motto-underline"></div>
        <div class="date-line">{date_str}</div>
      </td>
    </tr>
  </table>

  <div class="report-title">
    <div class="title-main">BÁO CÁO</div>
    <div class="title-sub">{html.escape(report_sub_title)}</div>
    <div class="title-underline"></div>
  </div>

  {body_html}

  <table class="footer-table">
    <tr>
      <td style="width: 52%; text-align: left; font-size: 11pt; line-height: 1.35;">
        <div style="font-size: 12pt; font-weight: bold; font-style: italic; margin-bottom: 2px;">Nơi nhận:</div>
        - Như trên;<br/>
        - Thủ tướng Chính phủ <em>(để báo cáo)</em>;<br/>
        - Các Phó Thủ tướng Chính phủ <em>(để báo cáo)</em>;<br/>
        - Văn phòng Chính phủ;<br/>
        - Bộ Tư pháp;<br/>
        - Bộ trưởng, các Thứ trưởng;<br/>
        - Vụ Pháp chế, Văn phòng Bộ;<br/>
        - Lưu: VT.
      </td>
      <td style="width: 48%; text-align: center; font-size: 13.5pt; line-height: 1.35;">
        <strong>{clean_signer}</strong><br/>
        <em style="font-size: 12pt;">(Chữ ký, dấu)</em><br/><br/><br/><br/><br/>
      </td>
    </tr>
  </table>
</body>
</html>"""


def export_html_to_pdf(html_path: Union[str, Path], pdf_path: Union[str, Path]) -> bool:
    """Convert an HTML RIA report to a PDF document using available tools."""
    html_p = Path(html_path).resolve()
    pdf_p = Path(pdf_path).resolve()
    if not html_p.exists():
        return False

    pdf_p.parent.mkdir(parents=True, exist_ok=True)
    if pdf_p.exists():
        try:
            pdf_p.unlink()
        except OSError:
            pass

    candidate_browsers = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
        shutil.which("wkhtmltopdf"),
    ]

    for browser in candidate_browsers:
        if browser and Path(browser).exists():
            try:
                if "wkhtmltopdf" in str(browser):
                    cmd = [str(browser), "--page-size", "A4", "--encoding", "utf-8", str(html_p), str(pdf_p)]
                else:
                    cmd = [
                        str(browser),
                        "--headless=new",
                        "--disable-gpu",
                        "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_p}",
                        str(html_p),
                    ]
                subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if pdf_p.exists() and pdf_p.stat().st_size > 0:
                    return True
            except Exception:
                continue

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Hệ thống Tự động Đánh giá Tác động Văn bản Quy phạm Pháp luật (Vlex-RIA)"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Đường dẫn tới file văn bản luật cần thẩm định (.docx, .pdf, .txt, .md)",
    )
    parser.add_argument(
        "--document_path",
        type=str,
        default="data/RIA/du_thao_nghi_dinh_ai.txt",
        help="Đường dẫn tới file dự thảo văn bản quy phạm pháp luật (.txt, .md, .docx, .pdf)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen2.5:7b",
        help="Tên model Ollama cục bộ (vd: qwen2.5:7b, qwen2.5:14b, deepseek-r1:14b, hoặc offline-heuristic)",
    )
    parser.add_argument(
        "--ollama_url",
        type=str,
        default="http://localhost:11434",
        help="Địa chỉ máy chủ Ollama cục bộ",
    )
    parser.add_argument(
        "--experiment_name",
        type=str,
        default="BASELINE",
        choices=list(all_settings.keys()),
        help="Cấu hình hội đồng thẩm định (mặc định: BASELINE)",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="outputs/RIA",
        help="Thư mục lưu Báo cáo RIA (.html, .pdf)",
    )
    parser.add_argument(
        "--max_num_words",
        type=int,
        default=4500,
        help="Số từ tối đa trích xuất từ văn bản dự thảo",
    )
    parser.add_argument(
        "--require_ollama",
        action="store_true",
        help="Bắt buộc kết nối tới Ollama (không tự động chuyển sang chế độ offline-heuristic khi Ollama tắt)",
    )
    parser.add_argument("--data_dir", type=str, default="data")
    parser.add_argument("--conference", type=str, default="RIA")
    parser.add_argument("--task", type=str, default="paper_review")
    parser.add_argument("--ac_scoring_method", type=str, default="ranking")
    parser.add_argument("--num_papers_per_area_chair", type=int, default=10)
    parser.add_argument("--acceptance_rate", type=float, default=0.32)

    args = parser.parse_args()

    target_doc = args.file if args.file else args.document_path
    doc_path = Path(target_doc).resolve()
    if not doc_path.exists():
        print(f"[ERROR] Document file not found: {doc_path}")
        sys.exit(1)

    paper_id = doc_path.stem
    paper_decision = "draft"

    # Load experiment settings
    setting = dict(all_settings[args.experiment_name])
    experiment_setting = get_experiment_settings(
        paper_id=paper_id,
        paper_decision=paper_decision,
        setting=setting,
    )

    backend_config = {
        "backend_type": "ollama-chat",
        "model": args.model,
        "base_url": args.ollama_url,
        "temperature": 0.3,
        "max_tokens": 2048,
        "num_ctx": 8192,
        "fallback_when_offline": args.model.startswith("offline-heuristic"),
    }

    print(f"\033[36m[START]\033[0m \033[1;36mVlex-RIA\033[0m | Document: \033[1m{doc_path.name}\033[0m | Model: \033[33m{args.model}\033[0m")

    # Initialize RIA council players
    players = initialize_ria_players(
        experiment_setting=experiment_setting,
        args=args,
        paper_pdf_path=str(doc_path),
        backend_config=backend_config,
    )

    # Initialize PaperReview environment
    env = PaperReview(
        player_names=[p.name for p in players],
        paper_decision=paper_decision,
        paper_id=paper_id,
        args=args,
        experiment_setting=experiment_setting,
    )

    # Initialize arena and execute evaluation phases
    arena = RIAPaperReviewArena(
        players=players,
        environment=env,
        args=args,
        global_prompt=None,
    )

    arena.launch_cli(interactive=False)

    # Extract final RIA Form 01 report synthesized by Area Chair
    messages = env.message_pool.get_all_messages()
    final_ria_report = ""
    for msg in reversed(messages):
        if msg.agent_name == "AC":
            final_ria_report = msg.content
            break

    os.makedirs(args.output_dir, exist_ok=True)
    report_html_path = os.path.join(args.output_dir, f"{paper_id}_RIA_Report.html")
    report_pdf_path = os.path.join(args.output_dir, f"{paper_id}_RIA_Report.pdf")

    with open(report_html_path, "w", encoding="utf-8") as f:
        f.write(render_state_standard_html(final_ria_report, doc_path.name, args.model))

    export_html_to_pdf(report_html_path, report_pdf_path)

    print(f"\n\033[32m[DONE]\033[0m Thẩm định hoàn tất:")
    print(f"  Báo cáo HTML:     \033[36m{report_html_path}\033[0m")
    if os.path.exists(report_pdf_path):
        print(f"  Báo cáo PDF:      \033[36m{report_pdf_path}\033[0m")


if __name__ == "__main__":
    main()
