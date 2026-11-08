<p align="center">
  <img src="assets/logo.jpg" alt="Vlex-RIA Logo" width="160">
</p>

# Vlex-RIA: Automated Regulatory Impact Assessment System

**Author:** Nguyen Nhat Phong

**Research Area:** Artificial Intelligence / Computer Science

**Status:** Research Project

## Overview

**Vlex-RIA** (*Regulatory Impact Assessment System*) is an artificial intelligence research framework leveraging **Multi-Agent Deliberation** to automate the comprehensive analysis, review, and generation of **Regulatory Impact Assessment (RIA) Reports**. The system strictly adheres to the standard statutory regulatory evaluation specifications (Form No. 01 / Mẫu số 01) pursuant to Vietnamese legal frameworks for the promulgation of normative legal documents.

By modeling the legislative review process as a collaborative, multi-turn discourse among specialized autonomous agents (representing legal compliance, socioeconomic impact, gender equality, administrative procedures, the drafting authority, and council chairs), Vlex-RIA automatically dissects complex draft legal documents (`.docx`, `.pdf`, `.txt`, `.md`), identifies critical policy options, performs multidimensional cost-benefit analyses, and synthesizes standardized administrative reports in HTML and print-ready PDF formats.

## Key Features

- **Multi-Agent Deliberation Council:**
  - **Legal Reviewer:** Assesses legal system consistency, constitutionality, and compliance with international treaties and conventions.
  - **Socioeconomic Reviewer:** Evaluates economic efficiency, social welfare implications, and conducts quantitative/qualitative Cost-Benefit Analysis (CBA).
  - **Gender & Vulnerable Groups Reviewer:** Analyzes gender equality, social equity, and potential impacts on vulnerable populations.
  - **Administrative Procedures Reviewer:** Scrutinizes business conditions, licensing overhead, and administrative compliance burdens.
  - **Drafting Authority (Author Agent):** Provides explanations, policy rationale, and responds to reviewer inquiries and critiques.
  - **Area Chair (Council Moderator):** Coordinates debate rounds, manages turn-taking, and synthesizes consensus into the final RIA report.
- **Deep Legal Document Structural Parsing:**
  - Ingests multiple formats including `.docx`, `.pdf`, `.txt`, and `.md`.
  - Automatically identifies hierarchical legal document structures (Chapters, Sections, Articles, Clauses, Points) and groups corresponding policy proposals.
  - Extracts mandatory obligation matrices, prohibited behaviors, and new license requirements.
- **Form No. 01 Compliant Standardized Reports:**
  - Formats output according to formal state administrative standards:
    - **Part I: Problem Identification** (Domestic/international context, overall and specific objectives).
    - **Part II: Policy Impact Assessment** (Policy goals, option comparison, 4 mandatory impact dimensions, optimal solution selection).
    - **Part III: Appendix** (Cost-benefit calculation tables and international treaty compliance checklists).
  - Exports to publication-grade `.html` and `.pdf` with precise A4 layout, 14pt Times New Roman typography, official national emblems, headers, and signature authority blocks.
- **100% Offline & Local Privacy-Preserving Execution:**
  - Operates locally via Ollama (optimized for local LLMs such as Qwen 2.5, DeepSeek-R1, Llama 3) with full Apple Silicon Metal GPU acceleration.
  - Built-in heuristic offline fallback rules ensure complete availability without requiring cloud APIs or external internet connectivity.

## Repository Structure

```text
Vlex-RIA/
├── vlex_ria/                  # Core Vlex-RIA library package
│   ├── backends/              # LLM backend interfaces (Ollama, OpenAI, Dummy)
│   ├── environments/          # Multi-agent deliberation environments (RIAReview)
│   ├── utility/               # Legal text utilities, data processors, formatting
│   ├── agent.py               # Base agent definitions
│   ├── document_processor.py  # PDF and legal text processing
│   ├── ria_arena.py           # Council deliberation arena
│   └── ria_player.py          # RIA specialized agents (Reviewers, Area Chair)
├── app_ria.py                 # Web application server (Built-in Web & Gradio UI)
├── run_ria_review_cli.py      # Command-line interface for batch assessment
├── ria_components.py          # Runtime components, fallback loaders & council engine
├── ria_role_descriptions.py   # Specialized role descriptions and Form No. 01 prompts
├── setup_isolated_env.sh      # Environment bootstrap script (venv & model pull)
├── run_isolated_app.sh        # One-click startup script for local web app
├── assets/                    # Project logos, icons, and branding assets
├── outputs/                   # Generated RIA reports (.html, .pdf)
├── requirements.txt           # Project dependencies
├── setup.py                   # Package setup configuration
├── LICENSE                    # Proprietary license terms
├── COPYRIGHT                  # Detailed copyright declaration
└── README.md                  # Project documentation
```

## Installation

### Prerequisites
- Python 3.9+ (Python 3.10 or 3.11 recommended)
- macOS / Linux / Windows WSL

### 1. Automated Setup (Recommended)
Use the automated bootstrap script to set up a virtual environment and pull the default local model (`qwen2.5:7b`):
```bash
chmod +x setup_isolated_env.sh run_isolated_app.sh
./setup_isolated_env.sh qwen2.5:7b
```

### 2. Manual Installation
Alternatively, install dependencies manually:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Usage

### 1. Running the Web Application
Launch the standalone local web application:
```bash
./run_isolated_app.sh
```
Or run directly with Python:
```bash
python3 app_ria.py --host 127.0.0.1 --port 7860
```
Open your browser and navigate to `http://localhost:7860`.

To use the optional Gradio interface:
```bash
python3 app_ria.py --use_gradio --port 7860
```

### 2. Running via Command Line (CLI)
Perform a full automated RIA assessment on a draft legal document and generate a Form No. 01 report:
```bash
python3 run_ria_review_cli.py path/to/draft_law.docx --model qwen2.5:7b
```
The resulting `.html` and `.pdf` reports will be stored in `outputs/RIA/`.

## Citation

If you reference this project in academic or research work, please acknowledge the author:

```text
Nguyen Nhat Phong.
Vlex-RIA: Automated Regulatory Impact Assessment System.
GitHub Repository, 2026.
```

## Copyright and Intellectual Property

Copyright © 2026 Nguyen Nhat Phong.

All Rights Reserved.

The original source code and research materials in this repository are the intellectual property of Nguyen Nhat Phong.

This repository is publicly available for viewing and academic reference. No license is granted for using, copying, modifying, redistributing, or commercially exploiting the original source code without prior written permission from the copyright holder.

Third-party libraries, datasets, and dependencies are subject to their respective licenses and terms of use.

For licensing inquiries, please contact:

phong.nn262292m@sis.hust.edu.vn
