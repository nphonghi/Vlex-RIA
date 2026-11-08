import argparse
import cgi
import html
import io
import json
import logging
import os
import sys
import tempfile
import traceback
from argparse import Namespace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Generator, List, Tuple

from ria_components import (
    LegalDocumentExtractorPlayer,
    LegalPaperExtractorPlayer,
    RIAAreaChair,
    RIACouncilArena,
    RIAPaperReviewArena,
    initialize_ria_players,
)
from ria_role_descriptions import (
    get_ac_config,
    get_author_config,
    get_document_extractor_config,
    get_paper_extractor_config,
    get_reviewer_player_config,
)
from run_ria_review_cli import render_state_standard_html, export_html_to_pdf
from vlex_ria.agent import Player
from vlex_ria.environments import RIAReview, PaperReview
from vlex_ria.experiment_config import all_settings
from vlex_ria.ria_player import Reviewer
from vlex_ria.ria_settings import get_experiment_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Council composition: Reviewers (Legal/Treaties, Economy/CBA, Gender, Administrative Procedures), AC (Form 01 synthesis), Author (Drafting Agency).
num_players = 6

PHASE_NAMES_VI = {
    0: "Giai đoạn 0 (Phase 0): Parser bóc tách Chương/Điều/Khoản & Nghĩa vụ pháp lý",
    1: "Giai đoạn 1 (Phase 1): Thẩm định độc lập từ 04 Chuyên gia theo Mẫu số 01 (Pháp luật, Kinh tế-Xã hội, Giới, TTHC)",
    2: "Giai đoạn 2 (Phase 2): Ban Soạn Thảo giải trình & tiếp thu từng ý kiến của 04 Chuyên gia",
    3: "Giai đoạn 3 (Phase 3): Chủ tọa (AC) điều phối 04 Chuyên gia thảo luận chéo & chốt điểm",
    4: "Giai đoạn 4 (Phase 4): Chủ tọa (AC) lập Báo cáo Đánh giá Tác động của Chính sách (Mẫu số 01)",
}


def get_player(
    index: int,
    config: dict,
    global_settings: dict,
    args: Namespace,
    backend_config: dict,
):
    """
    Được xây dựng trực tiếp từ hàm `get_player(index, config)` trong `app.py` (dòng 141-214),
    hỗ trợ đầy đủ 4 Reviewer (index 0..3), AC (index 4), Author (index 5).
    """
    if index in [0, 1, 2, 3]:
        Commitment_config = config.get("Commitment", "Normal")
        Intention_config = config.get("Intention", "Normal")
        Knowledgeability_config = config.get("Knowledgeability", "Normal")

        is_benign = True if Intention_config == "Benign" else (False if Intention_config == "Malicious" else None)
        is_responsible = (
            True
            if Commitment_config == "Responsible"
            else (False if Commitment_config in ["Irresponsible", "Lazy"] else None)
        )
        is_knowledgeable = (
            True
            if Knowledgeability_config == "Knowledgeable"
            else (False if Knowledgeability_config == "Unknowledgeable" else None)
        )

        p_setting = {
            "is_benign": is_benign,
            "is_knowledgeable": is_knowledgeable,
            "is_responsible": is_responsible,
            "knows_authors": config.get("Knows Authors", "No") == "Yes",
        }
        player_config = get_reviewer_player_config(
            reviewer_index=index + 1,
            global_settings=global_settings,
            backend_config=backend_config,
            **p_setting,
        )
        player_config["name"] = f"Reviewer {index + 1}"
        return player_config, p_setting

    elif index == 4:
        ac_type = config.get("Conformity", "inclusive")
        p_setting = {"area_chair_type": ac_type}
        player_config = get_ac_config(
            env_type="paper_review",
            scoring_method="ranking",
            global_settings=global_settings,
            backend_config=backend_config,
            **p_setting,
        )
        return player_config, p_setting

    elif index == 5:
        player_config = get_author_config(backend_config=backend_config)
        return player_config, {}


def create_new_arena(
    document_path: str,
    player_configs: List[dict],
    model_name: str = "qwen2.5:7b",
    ollama_url: str = "http://localhost:11434",
    experiment_name: str = "BASELINE",
) -> RIAPaperReviewArena:
    """
    Được xây dựng trực tiếp từ hàm `create_new_arena(...)` trong `app.py` (dòng 505-591).
    Khởi tạo `PaperReview` environment và `RIAPaperReviewArena` từ cấu hình người dùng chọn trên giao diện.
    """
    doc_file = Path(document_path).resolve()
    paper_id = doc_file.stem
    paper_decision = "draft"

    args = Namespace(
        data_dir="data",
        conference="RIA",
        task="paper_review",
        ac_scoring_method="ranking",
        num_papers_per_area_chair=10,
        acceptance_rate=0.32,
        max_num_words=4500,
        openai_client_type="ollama",
        skip_logging=True,
        model=model_name,
        model_name=f"gpt-4-{model_name.replace(':', '_')}",
        experiment_name=experiment_name,
        output_dir="outputs/RIA",
    )

    base_setting = dict(all_settings.get(experiment_name, all_settings["BASELINE"]))
    experiment_setting = get_experiment_settings(
        paper_id=paper_id,
        paper_decision=paper_decision,
        setting=base_setting,
    )

    backend_config = {
        "backend_type": "ollama-chat",
        "model": model_name,
        "base_url": ollama_url,
        "temperature": 0.3,
        "max_tokens": 2048,
        "num_ctx": 8192,
        "fallback_when_offline": model_name.startswith("offline-heuristic"),
    }

    players = []
    reviewer_settings = []

    # Initialize council reviewers, AC, and Author from player configurations
    for i in range(num_players):
        p_cfg, p_setting = get_player(
            index=i,
            config=player_configs[i],
            global_settings=experiment_setting["global_settings"],
            args=args,
            backend_config=backend_config,
        )
        if i in [0, 1, 2, 3]:
            reviewer_settings.append(p_setting)
            player = Reviewer(data_dir=args.data_dir, conference=args.conference, args=args, **p_cfg)
        elif i == 4:
            experiment_setting["players"]["AC"] = [p_setting]
            player = RIAAreaChair(data_dir=args.data_dir, conference=args.conference, args=args, **p_cfg)
        elif i == 5:
            player = Player(data_dir=args.data_dir, conference=args.conference, args=args, **p_cfg)
        players.append(player)

    experiment_setting["players"]["Reviewer"] = reviewer_settings

    # Initialize LegalPaperExtractorPlayer to extract articles and clauses
    extractor_cfg = get_paper_extractor_config(global_settings=experiment_setting["global_settings"])
    paper_extractor = LegalPaperExtractorPlayer(
        paper_pdf_path=str(doc_file),
        data_dir=args.data_dir,
        paper_id=paper_id,
        paper_decision=paper_decision,
        args=args,
        conference=args.conference,
        **extractor_cfg,
    )
    players.append(paper_extractor)

    env = PaperReview(
        player_names=[p.name for p in players],
        paper_decision=paper_decision,
        paper_id=paper_id,
        args=args,
        experiment_setting=experiment_setting,
    )

    arena = RIAPaperReviewArena(players=players, environment=env, args=args)
    return arena


def run_full_ria_pipeline(
    document_path: str,
    model_name: str = "qwen2.5:7b",
    ollama_url: str = "http://localhost:11434",
    player_configs: List[dict] = None,
    report_meta: dict = None,
) -> dict:
    """
    Chạy trọn vẹn 5 Phase của `RIAPaperReviewArena` (hoàn thiện phần `# TODO: 连续运行` trong `app.py`)
    và trả về toàn bộ thông điệp của các tác nhân cùng Báo cáo RIA cuối cùng theo Mẫu số 01.
    """
    if player_configs is None:
        player_configs = [
            {"Intention": "Normal", "Knowledgeability": "Normal", "Commitment": "Normal", "Knows Authors": "No"},
            {"Intention": "Normal", "Knowledgeability": "Normal", "Commitment": "Normal", "Knows Authors": "No"},
            {"Intention": "Normal", "Knowledgeability": "Normal", "Commitment": "Normal", "Knows Authors": "No"},
            {"Intention": "Normal", "Knowledgeability": "Normal", "Commitment": "Normal", "Knows Authors": "No"},
            {"Conformity": "inclusive"},
            {},
        ]
    if report_meta is None:
        report_meta = {}

    import importlib
    import ria_role_descriptions as _rrd
    import ria_components as _rc
    import run_ria_review_cli as _rcli
    importlib.reload(_rrd)
    importlib.reload(_rc)
    importlib.reload(_rcli)
    globals()["LegalPaperExtractorPlayer"] = _rc.LegalPaperExtractorPlayer
    globals()["RIAAreaChair"] = _rc.RIAAreaChair
    globals()["RIAPaperReviewArena"] = _rc.RIAPaperReviewArena
    globals()["initialize_ria_players"] = _rc.initialize_ria_players
    globals()["get_ac_config"] = _rrd.get_ac_config
    globals()["get_author_config"] = _rrd.get_author_config
    globals()["get_paper_extractor_config"] = _rrd.get_paper_extractor_config
    globals()["get_reviewer_player_config"] = _rrd.get_reviewer_player_config
    globals()["render_state_standard_html"] = _rcli.render_state_standard_html
    globals()["export_html_to_pdf"] = _rcli.export_html_to_pdf

    t_start = datetime.now()

    arena = create_new_arena(
        document_path=document_path,
        player_configs=player_configs,
        model_name=model_name,
        ollama_url=ollama_url,
    )

    steps_log = []
    while True:
        current_phase = arena.environment.phase_index
        timestep = arena.step()
        all_msgs = timestep.observation
        latest_msg = all_msgs[-1] if all_msgs else None
        if latest_msg:
            steps_log.append(
                {
                    "phase_index": current_phase,
                    "phase_title": PHASE_NAMES_VI.get(current_phase, f"Phase {current_phase}"),
                    "agent_name": latest_msg.agent_name,
                    "visible_to": latest_msg.visible_to,
                    "content": latest_msg.content,
                }
            )
        if timestep.terminal:
            break

    # Persist evaluation records to outputs/RIA (.html and .pdf only)
    doc_file = Path(document_path).resolve()
    paper_id = doc_file.stem
    out_dir = Path("outputs/RIA")
    out_dir.mkdir(parents=True, exist_ok=True)

    final_report = ""
    for item in reversed(steps_log):
        if item["agent_name"] == "AC":
            final_report = item["content"]
            break

    html_path = out_dir / f"{paper_id}_RIA_Report.html"
    pdf_path = out_dir / f"{paper_id}_RIA_Report.pdf"

    report_html = render_state_standard_html(
        final_report,
        report_meta.get("draft_title") or doc_file.name,
        model_name,
        agency_name=report_meta.get("agency_name", ""),
        report_number=report_meta.get("report_number", ""),
        location_name=report_meta.get("location_name", "Hà Nội"),
        signer_title=report_meta.get("signer_title", "KT. BỘ TRƯỞNG\nTHỨ TRƯỞNG"),
    )

    html_path.write_text(report_html, encoding="utf-8")
    export_html_to_pdf(html_path, pdf_path)

    elapsed_seconds = round((datetime.now() - t_start).total_seconds(), 1)

    return {
        "document_name": doc_file.name,
        "paper_id": paper_id,
        "model_name": model_name,
        "elapsed_seconds": elapsed_seconds,
        "steps": steps_log,
        "final_report_html": report_html,
        "html_path": str(html_path.resolve()),
        "pdf_path": str(pdf_path.resolve()) if pdf_path.exists() else "",
    }


def launch_gradio_app(port: int = 7860, share: bool = False):
    import gradio as gr

    with gr.Blocks(title="Vlex-RIA") as demo:
        gr.Markdown(
            "## Vlex-RIA\n"
            "**Hệ thống tự động đánh giá tác động chính sách**"
        )

        with gr.Row():
            with gr.Column(scale=1):
                file_input = gr.File(
                    label="Tài liệu dự thảo",
                    file_types=[".txt", ".md", ".docx", ".pdf"],
                )
                use_sample_chk = gr.Checkbox(
                    label="Sử dụng văn bản mẫu",
                    value=True,
                )
                model_dropdown = gr.Dropdown(
                    choices=[
                        "qwen2.5:7b",
                        "qwen2.5:14b",
                        "deepseek-r1:14b",
                        "deepseek-r1:7b",
                        "offline-heuristic",
                    ],
                    value="qwen2.5:7b",
                    label="Mô hình",
                )
                ollama_url_box = gr.Textbox(
                    value="http://localhost:11434",
                    label="Cổng máy chủ",
                )

                with gr.Accordion("Hội đồng chuyên gia", open=False):
                    r1_intent = gr.Dropdown(["Normal", "Benign", "Malicious"], value="Normal", label="Chuyên gia pháp luật - Động cơ")
                    r1_know = gr.Dropdown(["Normal", "Knowledgeable", "Unknowledgeable"], value="Normal", label="Chuyên gia pháp luật - Chuyên môn")
                    r1_resp = gr.Dropdown(["Normal", "Responsible", "Irresponsible"], value="Normal", label="Chuyên gia pháp luật - Trách nhiệm")

                    r2_intent = gr.Dropdown(["Normal", "Benign", "Malicious"], value="Normal", label="Chuyên gia kinh tế xã hội - Động cơ")
                    r2_know = gr.Dropdown(["Normal", "Knowledgeable", "Unknowledgeable"], value="Normal", label="Chuyên gia kinh tế xã hội - Chuyên môn")
                    r2_resp = gr.Dropdown(["Normal", "Responsible", "Irresponsible"], value="Normal", label="Chuyên gia kinh tế xã hội - Trách nhiệm")

                    r3_intent = gr.Dropdown(["Normal", "Benign", "Malicious"], value="Normal", label="Chuyên gia bình đẳng giới - Động cơ")
                    r3_know = gr.Dropdown(["Normal", "Knowledgeable", "Unknowledgeable"], value="Normal", label="Chuyên gia bình đẳng giới - Chuyên môn")
                    r3_resp = gr.Dropdown(["Normal", "Responsible", "Irresponsible"], value="Normal", label="Chuyên gia bình đẳng giới - Trách nhiệm")

                    r4_intent = gr.Dropdown(["Normal", "Benign", "Malicious"], value="Normal", label="Chuyên gia thủ tục hành chính - Động cơ")
                    r4_know = gr.Dropdown(["Normal", "Knowledgeable", "Unknowledgeable"], value="Normal", label="Chuyên gia thủ tục hành chính - Chuyên môn")
                    r4_resp = gr.Dropdown(["Normal", "Responsible", "Irresponsible"], value="Normal", label="Chuyên gia thủ tục hành chính - Trách nhiệm")

                    ac_conf = gr.Dropdown(["inclusive", "conformist", "authoritarian"], value="inclusive", label="Chủ tọa hội đồng")

                run_btn = gr.Button("Bắt đầu thẩm định", variant="primary")

            with gr.Column(scale=2):
                with gr.Tabs():
                    with gr.TabItem("Báo cáo"):
                        final_report_md = gr.Markdown(label="Báo cáo hoàn chỉnh")
                        with gr.Row():
                            dl_html = gr.File(label="Tải HTML")
                            dl_pdf = gr.File(label="Tải PDF")

                    with gr.TabItem("Nhật ký thẩm định"):
                        parser_out = gr.Textbox(label="Trích xuất điều khoản", lines=8)
                        r1_out = gr.Markdown(label="Chuyên gia pháp luật")
                        r2_out = gr.Markdown(label="Chuyên gia kinh tế xã hội")
                        r3_out = gr.Markdown(label="Chuyên gia bình đẳng giới")
                        r4_out = gr.Markdown(label="Chuyên gia thủ tục hành chính")
                        author_out = gr.Markdown(label="Cơ quan soạn thảo")
                        discussion_out = gr.Markdown(label="Thảo luận hội đồng")

        def _execute_gradio(
            uploaded_file,
            use_sample,
            model_name,
            ollama_url,
            r1_i, r1_k, r1_r,
            r2_i, r2_k, r2_r,
            r3_i, r3_k, r3_r,
            r4_i, r4_k, r4_r,
            ac_c,
        ):
            if uploaded_file is not None:
                doc_path = uploaded_file.name if hasattr(uploaded_file, "name") else str(uploaded_file)
            elif use_sample:
                doc_path = "data/RIA/du_thao_nghi_dinh_ai.txt"
            else:
                raise gr.Error("Vui lòng tải lên tài liệu dự thảo hoặc chọn văn bản mẫu.")

            p_configs = [
                {"Intention": r1_i, "Knowledgeability": r1_k, "Commitment": r1_r, "Knows Authors": "No"},
                {"Intention": r2_i, "Knowledgeability": r2_k, "Commitment": r2_r, "Knows Authors": "No"},
                {"Intention": r3_i, "Knowledgeability": r3_k, "Commitment": r3_r, "Knows Authors": "No"},
                {"Intention": r4_i, "Knowledgeability": r4_k, "Commitment": r4_r, "Knows Authors": "No"},
                {"Conformity": ac_c},
                {},
            ]
            result = run_full_ria_pipeline(
                document_path=doc_path,
                model_name=model_name,
                ollama_url=ollama_url,
                player_configs=p_configs,
            )

            p0_text = ""
            r1_text, r2_text, r3_text, r4_text = "", "", "", ""
            auth_texts, p3_texts = [], []

            for step in result["steps"]:
                p_idx = step["phase_index"]
                ag = step["agent_name"]
                content = step["content"]
                if p_idx == 0 and "Extractor" in ag:
                    p0_text = content
                elif p_idx == 1:
                    if ag == "Reviewer 1":
                        r1_text = f"### Chuyên gia pháp luật\n\n{content}"
                    elif ag == "Reviewer 2":
                        r2_text = f"### Chuyên gia kinh tế xã hội\n\n{content}"
                    elif ag == "Reviewer 3":
                        r3_text = f"### Chuyên gia bình đẳng giới\n\n{content}"
                    elif ag == "Reviewer 4":
                        r4_text = f"### Chuyên gia thủ tục hành chính\n\n{content}"
                elif p_idx == 2 and ag == "Author":
                    auth_texts.append(f"**Cơ quan soạn thảo**:\n{content}")
                elif p_idx == 3:
                    p3_texts.append(f"**{ag}**:\n{content}")

            return (
                result["final_report_html"],
                result["html_path"],
                result["pdf_path"],
                p0_text,
                r1_text,
                r2_text,
                r3_text,
                r4_text,
                "\n\n---\n\n".join(auth_texts),
                "\n\n---\n\n".join(p3_texts),
            )

        run_btn.click(
            fn=_execute_gradio,
            inputs=[
                file_input, use_sample_chk, model_dropdown, ollama_url_box,
                r1_intent, r1_know, r1_resp,
                r2_intent, r2_know, r2_resp,
                r3_intent, r3_know, r3_resp,
                r4_intent, r4_know, r4_resp,
                ac_conf,
            ],
            outputs=[
                final_report_md, dl_html, dl_pdf,
                parser_out, r1_out, r2_out, r3_out, r4_out, author_out, discussion_out,
            ],
        )

    demo.queue()
    demo.launch(server_port=port, share=share)


INDEX_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Vlex-RIA</title>
<link rel="icon" href="/assets/favicon.png" type="image/png"/>
<link rel="shortcut icon" href="/favicon.ico" type="image/png"/>
<style>
  :root {
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e2e8f0;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #94a3b8;
    --btn-primary: #0f172a;
    --btn-hover: #1e293b;
    --ring: #0f172a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text-primary);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  header {
    background: #ffffff;
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 64px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-logo-box {
    width: 44px;
    height: 44px;
    border-radius: 10px;
    background: #000000;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.14);
    overflow: hidden;
  }
  .brand-logo-box img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    border-radius: 10px;
  }
  .brand-text {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 1px;
  }
  .brand-title {
    font-weight: 800;
    font-size: 19px;
    letter-spacing: -0.02em;
    color: #0f172a;
    line-height: 1.15;
  }
  .brand-subtitle {
    font-size: 12px;
    font-weight: 500;
    color: #64748b;
    line-height: 1.25;
  }
  .status-pill {
    font-size: 12px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #10b981;
  }
  .container {
    display: grid;
    grid-template-columns: 340px 1fr;
    gap: 20px;
    max-width: 1540px;
    margin: 20px auto;
    padding: 0 20px;
    min-height: calc(100vh - 94px);
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  }
  .section-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  label {
    display: block;
    font-size: 12.5px;
    font-weight: 500;
    margin: 12px 0 4px;
    color: var(--text-secondary);
  }
  input[type="file"], select, input[type="text"] {
    width: 100%;
    padding: 7px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    color: var(--text-primary);
    background: #ffffff;
    transition: all 0.15s ease;
  }
  input:focus, select:focus {
    outline: none;
    border-color: var(--ring);
    box-shadow: 0 0 0 1px var(--ring);
  }
  .upload-dropzone {
    border: 1.5px dashed #cbd5e1;
    border-radius: 8px;
    padding: 16px 12px;
    text-align: center;
    background: #fafafa;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .upload-dropzone:hover, .upload-dropzone.dragover {
    border-color: #0f172a;
    background: #f1f5f9;
  }
  .dropzone-title {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 3px;
  }
  .dropzone-hint {
    font-size: 11.5px;
    color: var(--text-muted);
  }
  .dropzone-selected {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 6px;
    text-align: left;
  }
  .file-info {
    overflow: hidden;
    padding-right: 8px;
  }
  .file-name {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-primary);
    display: block;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .file-size {
    font-size: 11px;
    color: var(--text-muted);
  }
  .file-change-btn {
    font-size: 11.5px;
    font-weight: 500;
    color: #2563eb;
    background: none;
    border: none;
    cursor: pointer;
    padding: 2px 4px;
    flex-shrink: 0;
  }
  .file-change-btn:hover {
    text-decoration: underline;
  }
  .model-select-wrapper {
    display: flex;
    align-items: stretch;
    gap: 8px;
    margin-top: 4px;
  }
  .model-icon-box {
    width: 38px;
    height: 38px;
    border-radius: 6px;
    background: #ffffff;
    border: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
    transition: all 0.2s ease;
  }
  .model-select-wrapper select {
    flex: 1;
    height: 38px;
    padding: 0 28px 0 12px;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 10px center;
    font-weight: 600;
    cursor: pointer;
  }
  .history-select-wrapper {
    position: relative;
    display: inline-flex;
    align-items: center;
  }
  .history-icon {
    position: absolute;
    left: 9px;
    width: 13px;
    height: 13px;
    color: var(--text-muted);
    pointer-events: none;
    z-index: 1;
  }
  .history-select-wrapper select {
    width: auto;
    min-width: 210px;
    max-width: 260px;
    padding: 5px 26px 5px 28px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    background-color: #ffffff;
    border: 1px solid var(--border);
    border-radius: 5px;
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 8px center;
    transition: all 0.15s ease;
  }
  .history-select-wrapper select:hover {
    border-color: #cbd5e1;
    color: var(--text-primary);
    background-color: #f8fafc;
  }
  .history-select-wrapper select:focus {
    border-color: var(--ring);
    box-shadow: 0 0 0 1px var(--ring);
    outline: none;
  }
  .time-badge {
    font-size: 11.5px;
    font-weight: 600;
    color: #047857;
    background: #ecfdf5;
    border: 1px solid #a7f3d0;
    padding: 3px 8px;
    border-radius: 5px;
    display: inline-flex;
    align-items: center;
  }
  details {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    margin-top: 12px;
    background: #fafafa;
  }
  details summary {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-secondary);
    cursor: pointer;
    user-select: none;
  }
  .details-content {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }
  .trait-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 4px;
    margin-bottom: 6px;
  }
  .trait-grid select {
    font-size: 11.5px;
    padding: 4px 6px;
  }
  .btn-submit {
    width: 100%;
    margin-top: 16px;
    padding: 10px;
    background: var(--btn-primary);
    color: #ffffff;
    font-weight: 600;
    font-size: 13px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .btn-submit:hover { background: var(--btn-hover); }
  .btn-submit:disabled { opacity: 0.6; cursor: wait; }
  .workspace {
    display: flex;
    flex-direction: column;
  }
  .workspace-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
    margin-bottom: 12px;
  }
  .tabs {
    display: flex;
    gap: 4px;
  }
  .tab-btn {
    padding: 6px 12px;
    border: none;
    background: transparent;
    border-radius: 5px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    color: var(--text-secondary);
    transition: all 0.15s;
  }
  .tab-btn.active {
    background: #0f172a;
    color: #ffffff;
  }
  .actions-bar {
    display: flex;
    gap: 6px;
  }
  .action-btn {
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 5px;
    text-decoration: none;
    transition: all 0.15s;
  }
  .action-btn:hover {
    color: var(--text-primary);
    border-color: #cbd5e1;
    background: #f8fafc;
  }
  .tab-panel { display: none; height: calc(100vh - 170px); }
  .tab-panel.active { display: block; }
  iframe#reportFrame {
    width: 100%;
    height: 100%;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: #ffffff;
  }
  .status-text {
    font-size: 12.5px;
    color: var(--text-secondary);
    margin-bottom: 10px;
    min-height: 18px;
  }
  .step-list {
    height: 100%;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-right: 4px;
  }
  .step-item {
    border: 1px solid var(--border);
    background: #fafafa;
    border-radius: 6px;
    padding: 10px 12px;
  }
  .step-meta {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
  }
  .step-content {
    font-size: 12.5px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    max-height: 280px;
    overflow-y: auto;
    line-height: 1.5;
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-logo-box">
      <img src="/assets/logo_rounded_square.png" alt="Vlex-RIA"/>
    </div>
    <div class="brand-text">
      <span class="brand-title">Vlex-RIA</span>
      <span class="brand-subtitle">Hệ thống tự động đánh giá tác động chính sách</span>
    </div>
  </div>
  <div class="status-pill" id="ollamaPill">
    <span class="status-dot"></span>
    <span id="ollamaPillText">Ollama sẵn sàng</span>
  </div>
</header>

<div class="container">
  <div class="card">
    <div class="section-title">Thiết lập hồ sơ</div>
    <form id="riaForm">
      <label>Tài liệu dự thảo</label>
      <div class="upload-dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
        <input type="file" id="fileInput" name="document" accept=".docx,.pdf,.txt,.md" style="display:none;"/>
        <div id="dropzoneEmpty">
          <div class="dropzone-title">Bấm để chọn hoặc kéo thả tệp văn bản</div>
          <div class="dropzone-hint">Định dạng hỗ trợ: .docx, .pdf, .txt, .md</div>
        </div>
        <div id="dropzoneSelected" style="display:none;" class="dropzone-selected">
          <div class="file-info">
            <span class="file-name" id="selectedFileName"></span>
            <span class="file-size" id="selectedFileSize"></span>
          </div>
          <button type="button" class="file-change-btn" onclick="event.stopPropagation(); document.getElementById('fileInput').click();">Thay đổi</button>
        </div>
      </div>

      <label for="modelName">Mô hình thẩm định</label>
      <div class="model-select-wrapper">
        <div class="model-icon-box" id="modelIconBox">
          <img src="/assets/qwen_logo.png" alt="Qwen" style="width:24px;height:24px;object-fit:contain;display:block;"/>
        </div>
        <select id="modelName" name="model_name" onchange="onModelChange(this.value)">
          <option value="qwen2.5:7b">Qwen 2.5 · 7B</option>
        </select>
      </div>

      <details>
        <summary>Thông tin thể thức</summary>
        <div class="details-content">
          <label>Cơ quan soạn thảo</label>
          <input type="text" name="agency_name" placeholder="Tự động theo cơ quan chủ trì trong văn bản"/>
          <label>Số văn bản</label>
          <input type="text" name="report_number" placeholder="Tự động (vd: /BC-BKHCN)"/>
          <label>Địa danh</label>
          <input type="text" name="location_name" value="Hà Nội"/>
          <label>Tên dự thảo</label>
          <input type="text" name="draft_title" placeholder="Tự động trích xuất từ văn bản"/>
          <label>Người ký</label>
          <input type="text" name="signer_title" value="BỘ TRƯỞNG"/>
        </div>
      </details>

      <details>
        <summary>Hội đồng chuyên gia</summary>
        <div class="details-content">
          <label>Chuyên gia pháp luật</label>
          <div class="trait-grid">
            <select name="r1_intent"><option value="Normal">Tiêu chuẩn</option><option value="Benign">Tích cực</option><option value="Malicious">Nghiêm ngặt</option></select>
            <select name="r1_know"><option value="Normal">Tiêu chuẩn</option><option value="Knowledgeable">Chuyên sâu</option><option value="Unknowledgeable">Cơ bản</option></select>
            <select name="r1_resp"><option value="Normal">Tiêu chuẩn</option><option value="Responsible">Kỹ lưỡng</option><option value="Irresponsible">Tóm lược</option></select>
          </div>

          <label>Chuyên gia kinh tế xã hội</label>
          <div class="trait-grid">
            <select name="r2_intent"><option value="Normal">Tiêu chuẩn</option><option value="Benign">Tích cực</option><option value="Malicious">Nghiêm ngặt</option></select>
            <select name="r2_know"><option value="Normal">Tiêu chuẩn</option><option value="Knowledgeable">Chuyên sâu</option><option value="Unknowledgeable">Cơ bản</option></select>
            <select name="r2_resp"><option value="Normal">Tiêu chuẩn</option><option value="Responsible">Kỹ lưỡng</option><option value="Irresponsible">Tóm lược</option></select>
          </div>

          <label>Chuyên gia bình đẳng giới</label>
          <div class="trait-grid">
            <select name="r3_intent"><option value="Normal">Tiêu chuẩn</option><option value="Benign">Tích cực</option><option value="Malicious">Nghiêm ngặt</option></select>
            <select name="r3_know"><option value="Normal">Tiêu chuẩn</option><option value="Knowledgeable">Chuyên sâu</option><option value="Unknowledgeable">Cơ bản</option></select>
            <select name="r3_resp"><option value="Normal">Tiêu chuẩn</option><option value="Responsible">Kỹ lưỡng</option><option value="Irresponsible">Tóm lược</option></select>
          </div>

          <label>Chuyên gia thủ tục hành chính</label>
          <div class="trait-grid">
            <select name="r4_intent"><option value="Normal">Tiêu chuẩn</option><option value="Benign">Tích cực</option><option value="Malicious">Nghiêm ngặt</option></select>
            <select name="r4_know"><option value="Normal">Tiêu chuẩn</option><option value="Knowledgeable">Chuyên sâu</option><option value="Unknowledgeable">Cơ bản</option></select>
            <select name="r4_resp"><option value="Normal">Tiêu chuẩn</option><option value="Responsible">Kỹ lưỡng</option><option value="Irresponsible">Tóm lược</option></select>
          </div>

          <label>Chủ tọa hội đồng</label>
          <select name="ac_conformity">
            <option value="inclusive">Tổng hợp toàn diện</option>
            <option value="conformist">Đồng thuận đa số</option>
            <option value="authoritarian">Trọng tâm quyết đoán</option>
          </select>

          <label>Cổng máy chủ</label>
          <input type="text" id="ollamaUrl" name="ollama_url" value="http://localhost:11434"/>
        </div>
      </details>

      <button type="submit" class="btn-submit" id="submitBtn">Bắt đầu thẩm định</button>
    </form>
  </div>

  <div class="card workspace">
    <div class="workspace-header">
      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('tab-report', this)">Báo cáo</button>
        <button class="tab-btn" onclick="switchTab('tab-steps', this)">Nhật ký thẩm định</button>
      </div>
      <div class="actions-bar" id="actionsBar">
        <span id="timeBadge" class="time-badge" style="display:none;"></span>
        <div class="history-select-wrapper">
          <svg class="history-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
          </svg>
          <select id="savedSelect" onchange="loadSavedReport(this.value)">
            <option value="">Lịch sử hồ sơ đã thẩm định...</option>
          </select>
        </div>
        <a id="dlHtmlBtn" class="action-btn" href="#" download style="display:none;">Tải HTML</a>
        <a id="dlPdfBtn" class="action-btn" href="#" download style="display:none;">Tải PDF</a>
      </div>
    </div>

    <div id="statusText" class="status-text">Sẵn sàng tiếp nhận tài liệu.</div>

    <div id="tab-report" class="tab-panel active">
      <iframe id="reportFrame" srcdoc="<p style='padding:24px;color:#94a3b8;font-size:13px;font-family:sans-serif;'>Báo cáo sẽ hiển thị tại đây sau khi hoàn tất thẩm định.</p>"></iframe>
    </div>

    <div id="tab-steps" class="tab-panel">
      <div id="stepsContainer" class="step-list">
        <p style="color:#94a3b8;font-size:13px;">Chưa có nhật ký phiên họp.</p>
      </div>
    </div>
  </div>
</div>

<script>
function switchTab(tabId, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  btn.classList.add('active');
}

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const dropzoneEmpty = document.getElementById('dropzoneEmpty');
const dropzoneSelected = document.getElementById('dropzoneSelected');
const selectedFileName = document.getElementById('selectedFileName');
const selectedFileSize = document.getElementById('selectedFileSize');

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('dragover');
});
dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('dragover');
});
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files && e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    updateFileDisplay();
  }
});

fileInput.addEventListener('change', updateFileDisplay);

function updateFileDisplay() {
  if (fileInput.files && fileInput.files.length) {
    const file = fileInput.files[0];
    selectedFileName.textContent = file.name;
    const sizeKb = (file.size / 1024).toFixed(1);
    const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
    selectedFileSize.textContent = file.size > 1024 * 1024 ? `${sizeMb} MB` : `${sizeKb} KB`;
    dropzoneEmpty.style.display = 'none';
    dropzoneSelected.style.display = 'flex';
  } else {
    dropzoneEmpty.style.display = 'block';
    dropzoneSelected.style.display = 'none';
  }
}

function clearFile() {
  fileInput.value = '';
  updateFileDisplay();
}

let timerInterval = null;

function formatDuration(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m > 0 ? `${m} phút ${s} giây` : `${sec} giây`;
}

document.getElementById('riaForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById('fileInput');
  const status = document.getElementById('statusText');
  const timeBadge = document.getElementById('timeBadge');

  if (!fileInput.files || !fileInput.files.length) {
    status.textContent = 'Vui lòng chọn tệp tài liệu dự thảo (.docx, .pdf, .txt, .md)';
    status.style.color = '#dc2626';
    return;
  }
  status.style.color = 'var(--text-secondary)';

  const targetName = fileInput.files[0].name;
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;

  if (timeBadge) timeBadge.style.display = 'none';

  const startTime = Date.now();
  const updateTimer = () => {
    const sec = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    const timeStr = m > 0 ? `${m}m ${s < 10 ? '0' : ''}${s}s` : `${s}s`;
    btn.textContent = `Đang thẩm định... (${timeStr})`;
    status.textContent = `Đang thẩm định 5 giai đoạn: ${targetName} (${timeStr})`;
  };
  updateTimer();
  timerInterval = setInterval(updateTimer, 1000);

  try {
    const formData = new FormData(document.getElementById('riaForm'));
    const resp = await fetch('/api/run_ria', { method: 'POST', body: formData });
    const data = await resp.json();
    if (!resp.ok || data.error) {
      throw new Error(data.error || 'Lỗi xử lý hệ thống');
    }

    clearInterval(timerInterval);
    const elapsedSec = data.elapsed_seconds || ((Date.now() - startTime) / 1000).toFixed(1);
    const timeText = formatDuration(elapsedSec);

    renderReportData(data, timeText);
    loadRecentList();
    status.textContent = `Hoàn tất thẩm định: ${data.document_name} · Thời gian xử lý: ${timeText}`;
  } catch (err) {
    clearInterval(timerInterval);
    status.textContent = 'Lỗi: ' + err.message;
    status.style.color = '#dc2626';
  } finally {
    clearInterval(timerInterval);
    btn.disabled = false;
    btn.textContent = 'Bắt đầu thẩm định';
  }
});

function renderReportData(data, timeText) {
  document.getElementById('reportFrame').srcdoc = data.final_report_html;
  const dlHtml = document.getElementById('dlHtmlBtn');
  const dlPdf = document.getElementById('dlPdfBtn');
  const timeBadge = document.getElementById('timeBadge');

  if (timeText && timeBadge) {
    timeBadge.textContent = `Thời gian: ${timeText}`;
    timeBadge.style.display = 'inline-flex';
  }

  const baseId = (data.paper_id || data.document_name || 'Bao_Cao').replace(/\.[^/.]+$/, '');
  const htmlBlob = new Blob([data.final_report_html], {type: 'text/html;charset=utf-8'});

  dlHtml.href = URL.createObjectURL(htmlBlob);
  dlHtml.download = baseId + '_RIA_Report.html';
  dlHtml.style.display = 'inline-block';

  dlPdf.href = `/api/download_pdf?id=${encodeURIComponent(baseId)}`;
  dlPdf.download = baseId + '_RIA_Report.pdf';
  dlPdf.style.display = 'inline-block';

  const stepsDiv = document.getElementById('stepsContainer');
  stepsDiv.innerHTML = '';
  if (data.steps && data.steps.length) {
    data.steps.forEach((st, idx) => {
      const card = document.createElement('div');
      card.className = 'step-item';
      const header = document.createElement('div');
      header.className = 'step-meta';
      header.textContent = 'Bước ' + (idx + 1) + ' · ' + st.agent_name + ' · Giai đoạn ' + st.phase_index;
      const body = document.createElement('div');
      body.className = 'step-content';
      body.textContent = st.content;
      card.appendChild(header);
      card.appendChild(body);
      stepsDiv.appendChild(card);
    });
  } else {
    stepsDiv.innerHTML = '<p style="color:#94a3b8;font-size:13px;">Chưa có dữ liệu nhật ký cho hồ sơ này.</p>';
  }
}

function getModelIconSvg(raw) {
  const name = (raw || '').toLowerCase();
  if (name.includes('deepseek')) {
    return `<img src="/assets/deepseek_logo.png" alt="DeepSeek" style="width:24px;height:24px;object-fit:contain;display:block;"/>`;
  }
  if (name.includes('qwen')) {
    return `<img src="/assets/qwen_logo.png" alt="Qwen" style="width:24px;height:24px;object-fit:contain;display:block;"/>`;
  }
  if (name.includes('gemma')) {
    return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 2C12 7.5 7.5 12 2 12C7.5 12 12 16.5 12 22C12 16.5 16.5 12 22 12C16.5 12 12 7.5 12 2Z" fill="#2563eb"/><circle cx="12" cy="12" r="2.5" fill="#60a5fa"/></svg>`;
  }
  if (name.includes('llama')) {
    return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M7.5 8C5.5 8 4 9.5 4 11.5S5.5 15 7.5 15c2.5 0 4-3.5 4.5-3.5s2 3.5 4.5 3.5c2 0 3.5-1.5 3.5-3.5S18.5 8 16.5 8c-2.5 0-4 3.5-4.5 3.5S10 8 7.5 8z" stroke="#0284c7" stroke-width="2"/></svg>`;
  }
  if (name.includes('mistral')) {
    return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><rect x="3" y="14" width="5" height="6" rx="1" fill="#ea580c"/><rect x="9.5" y="9" width="5" height="11" rx="1" fill="#ea580c"/><rect x="16" y="4" width="5" height="16" rx="1" fill="#ea580c"/></svg>`;
  }
  if (name.includes('phi')) {
    return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="7" height="7" rx="1" fill="#0284c7"/><rect x="13" y="4" width="7" height="7" rx="1" fill="#38bdf8"/><rect x="4" y="13" width="7" height="7" rx="1" fill="#38bdf8"/><rect x="13" y="13" width="7" height="7" rx="1" fill="#0284c7"/></svg>`;
  }
  return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M6 8l6-5 6 5M6 8l-3 6h6l-3-6M18 8l-3 6h6l-3-6"/></svg>`;
}

function onModelChange(val) {
  const box = document.getElementById('modelIconBox');
  if (box) box.innerHTML = getModelIconSvg(val);
}

function formatModelLabel(raw) {
  if (!raw || raw === 'offline-heuristic') return 'Quy tắc nội bộ (Offline)';
  const parts = raw.split(':');
  let family = parts[0].toLowerCase();
  let tag = parts[1] || '';

  let brand = family;
  if (family === 'deepseek-r1') brand = 'DeepSeek-R1';
  else if (family === 'deepseek-llm') brand = 'DeepSeek-LLM';
  else if (family.startsWith('deepseek')) brand = 'DeepSeek';
  else if (family === 'qwen2.5') brand = 'Qwen 2.5';
  else if (family === 'qwen2') brand = 'Qwen 2';
  else if (family === 'qwen2.5-coder') brand = 'Qwen 2.5 Coder';
  else if (family.startsWith('qwen')) brand = 'Qwen';
  else if (family === 'gemma2') brand = 'Gemma 2';
  else if (family === 'gemma') brand = 'Gemma';
  else if (family === 'llama3.3') brand = 'Llama 3.3';
  else if (family === 'llama3.2') brand = 'Llama 3.2';
  else if (family === 'llama3.1') brand = 'Llama 3.1';
  else if (family === 'llama3') brand = 'Llama 3';
  else if (family.startsWith('llama')) brand = 'Llama';
  else if (family.startsWith('mistral')) brand = 'Mistral';
  else if (family.startsWith('phi4')) brand = 'Phi-4';
  else if (family.startsWith('phi3')) brand = 'Phi-3';
  else brand = family.charAt(0).toUpperCase() + family.slice(1);

  let param = '';
  if (tag && tag.toLowerCase() !== 'latest') {
    const pMatch = tag.match(/^(\d+(?:\.\d+)?[bmk])/i);
    if (pMatch) {
      param = ' · ' + pMatch[1].toUpperCase();
    } else {
      param = ' · ' + tag;
    }
  }
  return brand + param;
}

function formatHistoryName(id) {
  if (!id) return '';
  const qhMatch = id.match(/^(\d+)_(\d{4})_QH(\d+)/i);
  if (qhMatch) {
    return `Luật ${qhMatch[1]}/${qhMatch[2]}/QH${qhMatch[3]}`;
  }
  const ndMatch = id.match(/^(\d+)_(\d{4})_ND_CP/i);
  if (ndMatch) {
    return `Nghị định ${ndMatch[1]}/${ndMatch[2]}/NĐ-CP`;
  }
  let clean = id.replace(/_/g, ' ');
  if (clean.length > 28) {
    clean = clean.substring(0, 25) + '...';
  }
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

async function loadRecentList() {
  const sel = document.getElementById('savedSelect');
  try {
    const res = await fetch('/api/recent_reports');
    const items = await res.json();
    sel.innerHTML = '<option value="">Lịch sử hồ sơ đã thẩm định...</option>';
    if (items.length === 0) {
      sel.innerHTML = '<option value="">Chưa có hồ sơ lưu</option>';
      return;
    }
    items.forEach(it => {
      const opt = document.createElement('option');
      opt.value = it.id;
      const prettyName = formatHistoryName(it.id);
      opt.textContent = `${prettyName} (${it.time})`;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

async function loadSavedReport(reportId) {
  if (!reportId) return;
  const status = document.getElementById('statusText');
  status.textContent = 'Đang tải lại hồ sơ: ' + reportId;
  try {
    const res = await fetch('/api/get_saved_report?id=' + encodeURIComponent(reportId));
    const data = await res.json();
    renderReportData(data, null);
    status.textContent = 'Đang hiển thị hồ sơ đã lưu: ' + reportId;
  } catch(e) {
    status.textContent = 'Lỗi tải hồ sơ: ' + e.message;
  }
}

async function checkOllamaStatus() {
  const pillText = document.getElementById('ollamaPillText');
  const dot = document.querySelector('.status-dot');
  const modelSelect = document.getElementById('modelName');
  try {
    const resp = await fetch('/api/ollama_status');
    const info = await resp.json();
    if (info.running && info.models && info.models.length) {
      dot.style.background = '#10b981';
      pillText.textContent = 'Ollama trực tuyến';

      const currentVal = modelSelect.value;
      modelSelect.innerHTML = '';
      info.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = formatModelLabel(m);
        modelSelect.appendChild(opt);
      });
      const ruleOpt = document.createElement('option');
      ruleOpt.value = 'offline-heuristic';
      ruleOpt.textContent = 'Quy tắc nội bộ (Offline)';
      modelSelect.appendChild(ruleOpt);

      if (info.models.includes(currentVal)) {
        modelSelect.value = currentVal;
      } else {
        modelSelect.value = info.models[0];
      }
    } else {
      dot.style.background = '#f59e0b';
      pillText.textContent = 'Quy tắc nội bộ';
      modelSelect.innerHTML = '<option value="offline-heuristic">Quy tắc nội bộ (Chưa có model LLM)</option>';
    }
  } catch (e) {
    dot.style.background = '#94a3b8';
    pillText.textContent = 'Ngoại tuyến';
    modelSelect.innerHTML = '<option value="offline-heuristic">Quy tắc nội bộ (Offline)</option>';
  }
  onModelChange(modelSelect.value);
}
checkOllamaStatus();
loadRecentList();
</script>
</body>
</html>
"""


class RIAWebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        req_line = args[0] if args else ""
        status_code = args[1] if len(args) > 1 else ""
        if "/api/" in req_line or str(status_code).startswith(("4", "5")):
            color = "\033[32m" if str(status_code).startswith("2") else "\033[31m"
            time_str = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(f"\033[2m{time_str}\033[0m {color}[HTTP {status_code}]\033[0m {req_line}\n")
            sys.stdout.flush()

    def do_GET(self):
        try:
            if self.path in ["/", "/index.html"]:
                data = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path in ["/favicon.ico", "/favicon.png"]:
                fav_p = Path("assets/favicon.png").resolve()
                if not fav_p.exists():
                    fav_p = Path("assets/logo_circle.png").resolve()
                if not fav_p.exists():
                    fav_p = Path("assets/logo.jpg").resolve()
                if fav_p.exists():
                    data = fav_p.read_bytes()
                    ct = "image/png" if fav_p.suffix.lower() == ".png" else "image/jpeg"
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404, "Favicon Not Found")
            elif self.path.startswith("/assets/"):
                asset_sub = self.path[len("/assets/"):].split("?")[0]
                asset_path = (Path("assets") / asset_sub).resolve()
                if asset_path.exists() and asset_path.is_file():
                    data = asset_path.read_bytes()
                    ct = "image/png" if asset_path.suffix.lower() == ".png" else "image/jpeg"
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404, "Asset Not Found")
            elif self.path == "/api/recent_reports":
                out_dir = Path("outputs/RIA")
                reports = []
                if out_dir.exists():
                    for p in sorted(out_dir.glob("*_RIA_Report.html"), key=lambda x: x.stat().st_mtime, reverse=True):
                        base_id = p.name.replace("_RIA_Report.html", "")
                        mtime_str = datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M %d/%m")
                        reports.append({
                            "id": base_id,
                            "time": mtime_str,
                        })
                payload = json.dumps(reports, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            elif self.path.startswith("/api/download_pdf"):
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query)
                report_id = query.get("id", [""])[0]
                out_dir = Path("outputs/RIA")
                pdf_p = out_dir / f"{report_id}_RIA_Report.pdf"
                html_p = out_dir / f"{report_id}_RIA_Report.html"

                if not pdf_p.exists() and html_p.exists():
                    export_html_to_pdf(html_p, pdf_p)

                if pdf_p.exists():
                    data = pdf_p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f'attachment; filename="{pdf_p.name}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404, "PDF report not found")

            elif self.path.startswith("/api/get_saved_report"):
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query)
                report_id = query.get("id", [""])[0]
                out_dir = Path("outputs/RIA")
                html_p = out_dir / f"{report_id}_RIA_Report.html"
                pdf_p = out_dir / f"{report_id}_RIA_Report.pdf"

                res = {
                    "document_name": report_id,
                    "paper_id": report_id,
                    "final_report_html": html_p.read_text(encoding="utf-8") if html_p.exists() else "",
                    "has_pdf": pdf_p.exists(),
                    "steps": [],
                }
                payload = json.dumps(res, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            elif self.path == "/api/ollama_status":
                import urllib.request
                status = {"running": False, "models": []}
                try:
                    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                        tags_data = json.loads(r.read().decode("utf-8"))
                        status["running"] = True
                        for m in tags_data.get("models", []):
                            name = m.get("name", "")
                            if name and name not in status["models"]:
                                status["models"].append(name)
                except Exception:
                    pass

                # Also inspect local manifest directory
                manifest_dir = Path(".local_ollama/models/manifests")
                if manifest_dir.exists():
                    for f in manifest_dir.glob("**/*"):
                        if f.is_file() and not f.name.startswith("."):
                            parts = f.relative_to(manifest_dir).parts
                            if len(parts) >= 3:
                                m_id = f"{parts[-2]}:{parts[-1]}"
                                if m_id not in status["models"]:
                                    status["models"].append(m_id)

                if status["models"]:
                    status["running"] = True

                payload = json.dumps(status, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_error(404, "Not Found")
        except Exception as e:
            traceback.print_exc()
            err_payload = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(err_payload)))
            self.end_headers()
            self.wfile.write(err_payload)

    def do_POST(self):
        if self.path == "/api/run_ria":
            try:
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    },
                )

                temp_path = None
                doc_path = None

                if "document" in form and getattr(form["document"], "filename", ""):
                    file_item = form["document"]
                    filename = os.path.basename(file_item.filename)
                    upload_dir = Path("outputs/RIA/uploads")
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    temp_path = upload_dir / filename
                    temp_path.write_bytes(file_item.file.read())
                    doc_path = str(temp_path)
                else:
                    raise ValueError(
                        "Vui lòng tải lên tệp tài liệu dự thảo (.docx, .pdf, .txt, .md)."
                    )

                model_name = form.getvalue("model_name", "qwen2.5:7b")
                ollama_url = form.getvalue("ollama_url", "http://localhost:11434")

                player_configs = [
                    {
                        "Intention": form.getvalue("r1_intent", "Normal"),
                        "Knowledgeability": form.getvalue("r1_know", "Normal"),
                        "Commitment": form.getvalue("r1_resp", "Normal"),
                        "Knows Authors": "No",
                    },
                    {
                        "Intention": form.getvalue("r2_intent", "Normal"),
                        "Knowledgeability": form.getvalue("r2_know", "Normal"),
                        "Commitment": form.getvalue("r2_resp", "Normal"),
                        "Knows Authors": "No",
                    },
                    {
                        "Intention": form.getvalue("r3_intent", "Normal"),
                        "Knowledgeability": form.getvalue("r3_know", "Normal"),
                        "Commitment": form.getvalue("r3_resp", "Normal"),
                        "Knows Authors": "No",
                    },
                    {
                        "Intention": form.getvalue("r4_intent", "Normal"),
                        "Knowledgeability": form.getvalue("r4_know", "Normal"),
                        "Commitment": form.getvalue("r4_resp", "Normal"),
                        "Knows Authors": "No",
                    },
                    {"Conformity": form.getvalue("ac_conformity", "inclusive")},
                    {},
                ]

                report_meta = {
                    "agency_name": form.getvalue("agency_name", "").strip(),
                    "report_number": form.getvalue("report_number", "").strip(),
                    "location_name": form.getvalue("location_name", "Hà Nội").strip() or "Hà Nội",
                    "draft_title": form.getvalue("draft_title", "").strip() or None,
                    "signer_title": form.getvalue("signer_title", "KT. BỘ TRƯỞNG\nTHỨ TRƯỞNG").strip() or "KT. BỘ TRƯỞNG\nTHỨ TRƯỞNG",
                }

                result = run_full_ria_pipeline(
                    document_path=doc_path,
                    model_name=model_name,
                    ollama_url=ollama_url,
                    player_configs=player_configs,
                    report_meta=report_meta,
                )

                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            except Exception as e:
                traceback.print_exc()
                err_payload = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_payload)))
                self.end_headers()
                self.wfile.write(err_payload)
        else:
            self.send_error(404, "Not Found")


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

    def server_bind(self):
        import socket
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def launch_builtin_web_app(host: str = "127.0.0.1", port: int = 7860):
    server = None
    target_port = port
    for p in range(port, port + 5):
        try:
            server = ReusableHTTPServer((host, p), RIAWebHandler)
            target_port = p
            break
        except OSError as e:
            if getattr(e, "errno", None) in (48, 98):  # Address already in use
                logger.warning(f"Cổng {p} đang được sử dụng hoặc đang chờ đóng, thử cổng {p + 1}...")
                continue
            raise e

    if server is None:
        server = ReusableHTTPServer((host, port), RIAWebHandler)
        target_port = port

    sys.stdout.write(f"\033[32m[READY]\033[0m \033[1;36mVlex-RIA\033[0m server running at \033[4;34mhttp://{host}:{target_port}\033[0m\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[33m[STOPPED]\033[0m Vlex-RIA server stopped.\n")
        sys.stdout.flush()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Khởi chạy ứng dụng Vlex-RIA")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Địa chỉ host")
    parser.add_argument("--port", type=int, default=7860, help="Cổng dịch vụ")
    parser.add_argument("--use_gradio", action="store_true", help="Sử dụng giao diện Gradio nếu có")
    parser.add_argument("--share", action="store_true", help="Chia sẻ liên kết công khai")
    args = parser.parse_args()

    if args.use_gradio:
        try:
            import gradio  # noqa: F401
            launch_gradio_app(port=args.port, share=args.share)
            return
        except ImportError:
            logger.warning("Môi trường chưa có gradio, sử dụng giao diện web mặc định.")

    launch_builtin_web_app(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

