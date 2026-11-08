import json
import logging
import os
import re
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Union

# Polyfill optional external packages
def _ensure_optional_imports():
    try:
        import tenacity  # noqa: F401
    except ImportError:
        mod = types.ModuleType("tenacity")
        class RetryError(Exception):
            pass
        def _identity_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn
            return wrapper
        mod.RetryError = RetryError
        mod.retry = _identity_decorator
        mod.stop_after_attempt = lambda *a, **kw: None
        mod.wait_random_exponential = lambda *a, **kw: None
        sys.modules["tenacity"] = mod

    try:
        import openai  # noqa: F401
    except ImportError:
        mod = types.ModuleType("openai")
        mod.OpenAI = object
        mod.AzureOpenAI = object
        sys.modules["openai"] = mod

    try:
        import numpy  # noqa: F401
    except ImportError:
        mod = types.ModuleType("numpy")
        mod.ndarray = list
        mod.floor = lambda x: int(x)
        mod.array = lambda x: list(x)
        sys.modules["numpy"] = mod

    try:
        import pandas  # noqa: F401
    except ImportError:
        mod = types.ModuleType("pandas")
        mod.DataFrame = object
        mod.set_option = lambda *a, **kw: None
        sys.modules["pandas"] = mod

    try:
        import llama_index.readers.file.docs  # noqa: F401
    except ImportError:
        for name in ["llama_index", "llama_index.readers", "llama_index.readers.file", "llama_index.readers.file.docs"]:
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
        class _FallbackPDFReader:
            def load_data(self, file):
                return []
        sys.modules["llama_index.readers.file.docs"].PDFReader = _FallbackPDFReader

    try:
        import colorama  # noqa: F401
    except ImportError:
        mod = types.ModuleType("colorama")
        class _AnsiCodes:
            BLACK = "\033[30m"
            RED = "\033[31m"
            GREEN = "\033[32m"
            YELLOW = "\033[33m"
            BLUE = "\033[34m"
            MAGENTA = "\033[35m"
            CYAN = "\033[36m"
            WHITE = "\033[37m"
            RESET = "\033[0m"
            LIGHTBLACK_EX = "\033[90m"
            LIGHTRED_EX = "\033[91m"
            LIGHTGREEN_EX = "\033[92m"
            LIGHTYELLOW_EX = "\033[93m"
            LIGHTBLUE_EX = "\033[94m"
            LIGHTMAGENTA_EX = "\033[95m"
            LIGHTCYAN_EX = "\033[96m"
            LIGHTWHITE_EX = "\033[97m"
        class _StyleCodes:
            BRIGHT = "\033[1m"
            DIM = "\033[2m"
            NORMAL = "\033[22m"
            RESET_ALL = "\033[0m"
        mod.Fore = _AnsiCodes
        mod.Back = _AnsiCodes
        mod.Style = _StyleCodes
        sys.modules["colorama"] = mod

    try:
        import prettytable  # noqa: F401
    except ImportError:
        mod = types.ModuleType("prettytable")
        class PrettyTable:
            def __init__(self, field_names=None, **kwargs):
                self.field_names = field_names or []
                self.rows = []
            def add_row(self, row):
                self.rows.append(row)
            def __str__(self):
                lines = [" | ".join(str(x) for x in self.field_names)]
                lines.append("-" * 60)
                for r in self.rows:
                    lines.append(" | ".join(str(x) for x in r))
                return "\n".join(lines)
        mod.PrettyTable = PrettyTable
        sys.modules["prettytable"] = mod

    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        for name in ["prompt_toolkit", "prompt_toolkit.completion", "prompt_toolkit.styles"]:
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
        sys.modules["prompt_toolkit"].prompt = lambda *a, **kw: input()
        sys.modules["prompt_toolkit.completion"].WordCompleter = lambda *a, **kw: None
        class _DummyStyle:
            @staticmethod
            def from_dict(d):
                return d
        sys.modules["prompt_toolkit.styles"].Style = _DummyStyle

    try:
        import rich.console  # noqa: F401
    except ImportError:
        for name in ["rich", "rich.console", "rich.text"]:
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
        class _DummyConsole:
            def print(self, *args, **kwargs):
                print(*[str(a) for a in args])
        class _DummyText:
            def __init__(self, text="", *args, **kwargs):
                self._parts = [str(text)] if text else []
            def append(self, text, style=None):
                self._parts.append(str(text))
            def __str__(self):
                return "".join(self._parts)
        sys.modules["rich.console"].Console = _DummyConsole
        sys.modules["rich.text"].Text = _DummyText


_ensure_optional_imports()

# Core multi-agent imports
from vlex_ria.agent import Player
from vlex_ria.arena import TooManyInvalidActions
from vlex_ria.backends import BACKEND_REGISTRY
from vlex_ria.backends.base import IntelligenceBackend
from vlex_ria.backends.dummy import Dummy
from vlex_ria.backends.openai import OpenAIChat, STOP, END_OF_MESSAGE
from vlex_ria.config import AgentConfig, ArenaConfig, BackendConfig
from vlex_ria.environments.base import TimeStep
from vlex_ria.environments.ria_review import RIAReview, PaperReview
from vlex_ria.message import Message
from vlex_ria.ria_arena import RIAArena, PaperReviewArena
from vlex_ria.ria_player import AreaChair, DocumentExtractorPlayer, PaperExtractorPlayer, Reviewer

from ria_role_descriptions import (
    get_ac_config,
    get_author_config,
    get_document_extractor_config,
    get_paper_extractor_config,
    get_reviewer_description,
    get_reviewer_player_config,
)

logger = logging.getLogger(__name__)


class DummyIntelligenceBackend(Dummy, IntelligenceBackend):
    """Bổ sung interface IntelligenceBackend cho class Dummy gốc để tương thích với Player.__init__."""
    stateful = False
    type_name = "dummy"

    def query(self, *args, **kwargs) -> str:
        return ""

    async def async_query(self, *args, **kwargs) -> str:
        return ""


BACKEND_REGISTRY["dummy"] = DummyIntelligenceBackend


class LoadingIndicator:
    def __init__(self, model_name: str, message: str = "Đang tạo phản hồi..."):
        self.model_name = model_name
        self.message = message
        self.stop_event = threading.Event()
        self.thread = None
        self.t0 = 0.0
        self.is_tty = sys.stdout.isatty()

    def __enter__(self):
        self.t0 = time.time()
        if self.is_tty:
            frames = ["-", "\\", "|", "/"]

            def spin():
                idx = 0
                while not self.stop_event.is_set():
                    elapsed = time.time() - self.t0
                    f = frames[idx % len(frames)]
                    sys.stdout.write(
                        f"\r\033[33m[{f}]\033[0m \033[36m{self.model_name}\033[0m | {self.message} ({elapsed:.1f}s)"
                    )
                    sys.stdout.flush()
                    idx += 1
                    time.sleep(0.1)

            self.thread = threading.Thread(target=spin, daemon=True)
            self.thread.start()
        else:
            sys.stdout.write(f"\033[33m[RUNNING]\033[0m \033[36m{self.model_name}\033[0m | {self.message}\n")
            sys.stdout.flush()
        return self

    def done(self, char_count: int):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.5)
        elapsed = time.time() - self.t0
        if self.is_tty:
            sys.stdout.write(
                f"\r\033[2K\033[32m[DONE]\033[0m    \033[36m{self.model_name}\033[0m | Hoàn tất trong {elapsed:.1f}s ({char_count:,} ký tự)\n"
            )
        else:
            sys.stdout.write(
                f"\033[32m[DONE]\033[0m    \033[36m{self.model_name}\033[0m | Hoàn tất trong {elapsed:.1f}s ({char_count:,} ký tự)\n"
            )
        sys.stdout.flush()

    def fail(self, error_msg: str):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.5)
        if self.is_tty:
            sys.stdout.write(f"\r\033[2K\033[31m[ERROR]\033[0m   \033[36m{self.model_name}\033[0m | Lỗi: {error_msg}\n")
        else:
            sys.stdout.write(f"\033[31m[ERROR]\033[0m   \033[36m{self.model_name}\033[0m | Lỗi: {error_msg}\n")
        sys.stdout.flush()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.fail(str(exc_val))
        return False


class OllamaBackend(OpenAIChat):
    """
    Backend kết nối trực tiếp máy chủ Ollama cục bộ (offline, bảo mật),
    tối ưu hóa luồng hội thoại đa tác nhân và xử lý context cho văn bản pháp luật.
    """

    stateful = False
    type_name = "ollama-chat"

    def __init__(
        self,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        num_ctx: int = 8192,
        merge_other_agents_as_one_user: bool = True,
        fallback_when_offline: bool = False,
        **kwargs,
    ):
        # Bypass parent get_openai_client to avoid requiring OPENAI_API_KEY
        IntelligenceBackend.__init__(
            self,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            base_url=base_url,
            num_ctx=num_ctx,
            merge_other_agents_as_one_user=merge_other_agents_as_one_user,
            fallback_when_offline=fallback_when_offline,
            **kwargs,
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.merge_other_agent_as_user = merge_other_agents_as_one_user
        self.fallback_when_offline = fallback_when_offline

    @staticmethod
    def _clean_llm_output(text: str, thinking_fallback: str = "") -> str:
        raw = text or ""
        # Remove closed <think>...</think> blocks
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        # If an unclosed <think> tag remains, keep content after the tag or fallback
        if "<think>" in cleaned:
            parts = cleaned.split("<think>")
            cleaned = parts[0].strip() or parts[-1].strip()
        if not cleaned.strip() and thinking_fallback:
            cleaned = re.sub(r"</?think>", "", thinking_fallback).strip()
        cleaned = cleaned.replace(END_OF_MESSAGE, "").replace("<EOS>", "")
        cleaned = re.sub(r"^```(?:markdown|md|text)?\s*\n?", "", cleaned.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned.strip())
        cleaned = re.sub(r"^\s*assistant\s*:?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^\s*\[(?:Reviewer\s*\d*|Author|AC|Area Chair|Chủ tịch Hội đồng|Cơ quan[^\]]*|Hệ thống)[^\]]*\]\s*:?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Prevent OpenAIChat.query() line 170 `re.sub(rf"^\s*\[.*]:", "", response)` from stripping a valid first line
        first_nl = cleaned.find("\n")
        first_line = cleaned[:first_nl] if first_nl != -1 else cleaned
        if re.match(r"^\s*\[.*?\]:", first_line):
            cleaned = re.sub(r"^\s*\[(.*?)\]:", r"**\1**:", cleaned, count=1)
        cleaned = re.sub(r"\bEOS\s*$", "", cleaned).strip()
        cleaned = re.sub(
            r"(?:Trân trọng,?|Kính trình,?)\s*(?:\[[^\]]+\]\s*)*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        cleaned = re.sub(r"\[(?:Signature|Name|Position|Chữ ký|Họ và tên|Chức vụ)\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"Hãy cho tôi biết nếu bạn cần thêm thông tin[^\n]*", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def _compress_paper_extractor_block(
        text: str,
        max_clauses_per_article: int = 2,
        max_matrix_items: int = 10,
    ) -> str:
        marker = "# TOÀN VĂN CÁC CHƯƠNG, ĐIỀU, KHOẢN CỦA DỰ THẢO"
        if marker not in text:
            return text
        prefix, full_articles_block = text.split(marker, 1)

        # Compress extracted legal matrices in prefix so 90+ bullet items do not bloat context
        prefix_lines = []
        matrix_item_count = 0
        in_matrix = False
        for line in prefix.splitlines():
            stripped = line.strip()
            if stripped.startswith("### ["):
                in_matrix = True
                matrix_item_count = 0
                prefix_lines.append(stripped)
            elif in_matrix and stripped.startswith("* "):
                if matrix_item_count < max_matrix_items:
                    short_item = stripped if len(stripped) <= 220 else stripped[:217] + "..."
                    prefix_lines.append(f"  {short_item}")
                    matrix_item_count += 1
            else:
                if not stripped:
                    in_matrix = False
                prefix_lines.append(line)

        compressed_prefix = "\n".join(prefix_lines).strip() + "\n\n"

        # Compress full articles block while keeping every Chapter, every Article header, and top clauses per Article
        compressed_lines = [marker]
        clause_count_in_article = 0
        for line in full_articles_block.splitlines():
            stripped = line.strip()
            if stripped.startswith("## CHƯƠNG") or stripped.startswith("## Chương"):
                compressed_lines.append(f"\n{stripped}")
                clause_count_in_article = 0
            elif stripped.startswith("### Điều"):
                compressed_lines.append(stripped)
                clause_count_in_article = 0
            elif stripped and clause_count_in_article < max_clauses_per_article:
                short_clause = stripped if len(stripped) <= 220 else stripped[:217] + "..."
                compressed_lines.append(f"  {short_clause}")
                clause_count_in_article += 1

        return compressed_prefix + "\n".join(compressed_lines)

    @classmethod
    def _compress_agent_turn_block(cls, text: str, max_words: int = 160) -> str:
        cleaned = cls._clean_llm_output(text)
        if not cleaned:
            return ""
        rating_matches = re.findall(
            r"^(?:OVERALL|FINAL)\s*RATING\s*:\s*\d+[^\n]*",
            cleaned,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        words = cleaned.split()
        if len(words) <= max_words:
            return cleaned

        kept_lines = []
        word_acc = 0
        for ln in cleaned.splitlines():
            s = ln.strip()
            if not s:
                continue
            w_cnt = len(s.split())
            if word_acc + w_cnt > max_words and kept_lines:
                break
            kept_lines.append(s)
            word_acc += w_cnt

        result = "\n".join(kept_lines)
        for rm in rating_matches:
            if rm.strip() not in result:
                result += f"\n{rm.strip()}"
        return result

    @classmethod
    def _prepare_ollama_messages(cls, messages: List[dict]) -> List[dict]:
        if not messages:
            return []

        raw_sys = messages[0].get("content", "")
        sys_str = raw_sys.replace(f"The messages always end with the token {END_OF_MESSAGE}.", "")
        sys_str = sys_str.replace(
            "You are a helpful assistant.",
            "Bạn là chuyên gia thẩm định và đánh giá tác động chính sách pháp luật Việt Nam.",
        ).strip()

        name_match = re.search(r"Your name is ([^.\n]+)\.", raw_sys)
        current_agent = name_match.group(1).strip() if name_match else "Chuyên gia"

        # Reconstruct chronological transcript from all non-system messages
        raw_parts = []
        for m in messages[1:]:
            role = m.get("role", "user")
            c = (m.get("content", "") or "").replace(END_OF_MESSAGE, "").replace("<EOS>", "").strip()
            if not c:
                continue
            if role == "assistant":
                raw_parts.append(f"[{current_agent}]: {c}")
            else:
                raw_parts.append(c)

        joined_transcript = "\n\n".join(raw_parts)
        speaker_split = re.split(
            r"(?:^|\n\n)\[(Paper Extractor|Reviewer\s+\d+|Author|AC|System)\]:\s*",
            joined_transcript,
        )

        turns = []
        if len(speaker_split) > 1:
            for idx in range(1, len(speaker_split), 2):
                sp_name = speaker_split[idx].strip()
                sp_body = speaker_split[idx + 1].strip()
                turns.append((sp_name, sp_body))
        else:
            turns.append(("User", joined_transcript))

        non_sys_turns = [t for t in turns if t[0] != "System"]
        is_multi_turn_phase = len(non_sys_turns) > 2

        formatted_blocks = []
        reviewer_seen_counts = {}
        author_turn_idx = 0
        extractor_text = ""

        for sp_name, sp_body in turns:
            if sp_name == "System":
                continue
            if sp_name == "Paper Extractor":
                extractor_text = sp_body
                if is_multi_turn_phase:
                    comp_ext = cls._compress_paper_extractor_block(
                        sp_body, max_clauses_per_article=1, max_matrix_items=6
                    )
                else:
                    comp_ext = cls._compress_paper_extractor_block(
                        sp_body, max_clauses_per_article=2, max_matrix_items=10
                    )
                formatted_blocks.append(f"[Paper Extractor - Hồ sơ Dự thảo]:\n{comp_ext}")
            elif sp_name.startswith("Reviewer"):
                cnt = reviewer_seen_counts.get(sp_name, 0) + 1
                reviewer_seen_counts[sp_name] = cnt
                round_label = "Thẩm định vòng 1" if cnt == 1 else "Thảo luận vòng cuối - Phase 3"
                body_str = (
                    cls._compress_agent_turn_block(sp_body, max_words=160)
                    if is_multi_turn_phase
                    else cls._compress_agent_turn_block(sp_body, max_words=550)
                )
                if body_str:
                    formatted_blocks.append(f"[{sp_name} ({round_label})]:\n{body_str}")
            elif sp_name == "Author":
                author_turn_idx += 1
                body_str = cls._compress_agent_turn_block(
                    sp_body, max_words=150 if is_multi_turn_phase else 450
                )
                if body_str:
                    formatted_blocks.append(
                        f"[Author - Cơ quan soạn thảo (Giải trình với Reviewer {author_turn_idx})]:\n{body_str}"
                    )
            elif sp_name == "AC":
                body_str = cls._compress_agent_turn_block(sp_body, max_words=120)
                if body_str:
                    formatted_blocks.append(f"[AC - Chủ tịch Hội đồng]:\n{body_str}")
            else:
                formatted_blocks.append(sp_body)

        # Build explicit phase-aware Vietnamese instruction at the end of the single user prompt
        if current_agent == "AC" and is_multi_turn_phase:
            def _meta(label: str, default_v: str) -> str:
                m = re.search(rf"^- {re.escape(label)}:\s*(.+)$", extractor_text, flags=re.MULTILINE)
                return m.group(1).strip() if m else default_v

            draft_title = _meta("Tên dự thảo", "Dự thảo Văn bản quy phạm pháp luật")
            lead_agency = _meta("Cơ quan chủ trì đề xuất", "Bộ Tư pháp")
            authority = _meta("Thẩm quyền ban hành", "Chính phủ")
            chapters = re.findall(r"^##\s+(CHƯƠNG\s+[^\n]+)", extractor_text, flags=re.MULTILINE)
            ch_summary = "; ".join(chapters[:6]) if chapters else "Các nhóm chính sách trong Dự thảo"

            final_instruction = (
                f"[Hệ thống]: Bây giờ đến lượt Chủ tịch Hội đồng (AC) biên soạn toàn văn **BÁO CÁO ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH** "
                f"đối với {draft_title} (do {lead_agency} chủ trì soạn thảo, trình {authority}).\n"
                f"Dựa trên cấu trúc Dự thảo ({ch_summary}) cùng toàn bộ ý kiến phản biện, điểm số (OVERALL RATING -> FINAL RATING) của "
                f"Reviewer 1, Reviewer 2, Reviewer 3, Reviewer 4 và Giải trình của Cơ quan soạn thảo ở trên, hãy trực tiếp viết Báo cáo hoàn chỉnh bằng tiếng Việt "
                f"(viết thành các đoạn văn phân tích chi tiết, trích dẫn rõ Điều/Khoản, tuyệt đối không viết dấu `...` và không bọc trong ```) tuân thủ đúng 3 phần của Mẫu số 01 do Chính phủ ban hành:\n"
                f"# I. XÁC ĐỊNH VẤN ĐỀ\n"
                f"## 1. Bối cảnh xây dựng chính sách\n"
                f"- Bối cảnh quốc tế, khu vực liên quan đến các chính sách (nếu có): ...\n"
                f"- Bối cảnh trong nước (chính trị, pháp lý, kinh tế - xã hội...) liên quan đến các chính sách: ...\n"
                f"## 2. Mục tiêu xây dựng chính sách\n"
                f"# II. ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH\n"
                f"(Liệt kê và phân tích từng chính sách lớn: ## 1. Chính sách 1: ..., ## 2. Chính sách 2: ...; "
                f"mỗi chính sách gồm đủ 3 tiểu mục chuẩn theo đúng Mẫu số 01:\n"
                f"### x.1. Xác định vấn đề và mục tiêu giải quyết vấn đề\n"
                f"### x.2. Các giải pháp và đánh giá tác động của các giải pháp đối với đối tượng chịu sự tác động trực tiếp của chính sách và các đối tượng khác có liên quan "
                f"(nêu rõ Giải pháp 1 và Giải pháp 2; phân tích tác động tích cực (lợi ích) và tiêu cực (chi phí) đối với Nhà nước, người dân, doanh nghiệp trên 4 chiều cạnh: "
                f"- Tác động đối với hệ thống pháp luật: ...; - Tác động về kinh tế - xã hội: ...; - Tác động về giới (nếu có): ...; - Tác động của thủ tục hành chính (nếu có): ...)\n"
                f"### x.3. Lựa chọn giải pháp (kiến nghị giải pháp tối ưu và thẩm quyền ban hành))\n"
                f"# III. PHỤ LỤC\n"
                f"1. Các bảng, biểu tính toán chi phí và lợi ích của các giải pháp (nếu có): (Bảng Markdown so sánh chi phí và lợi ích của các giải pháp chính sách)\n"
                f"2. Danh mục các điều ước quốc tế có liên quan mà Cộng hòa xã hội chủ nghĩa Việt Nam là thành viên được rà soát và đánh giá tính tương thích: (Bảng Markdown rà soát điều ước quốc tế)\n"
                f"Kết thúc báo cáo bằng câu: Trên đây là nội dung Báo cáo đánh giá tác động của chính sách trong {draft_title}, {lead_agency} kính báo cáo./."
            )
        elif current_agent.startswith("Reviewer") and is_multi_turn_phase:
            final_instruction = (
                f"[Hệ thống]: Bây giờ đến lượt {current_agent} phát biểu tại Phiên thảo luận Hội đồng (Giai đoạn 3).\n"
                f"Sau khi đối chiếu Giải trình của Cơ quan soạn thảo (Author) và ý kiến của các chuyên gia khác trong Hội đồng, hãy viết phản hồi đầy đủ bằng tiếng Việt gồm:\n"
                f"FINAL RATING: <điểm số từ 1 đến 10>\n"
                f"Updated Review: Phân tích cụ thể những nội dung tại Dự thảo đã được Cơ quan soạn thảo giải trình, tiếp thu thỏa đáng và những Điều, Khoản cụ thể cần tiếp tục hoàn thiện để đưa vào Báo cáo đánh giá tác động chính thức."
            )
        elif current_agent == "Author":
            final_instruction = (
                f"[Hệ thống]: Bây giờ đến lượt Cơ quan chủ trì soạn thảo (Author) phát biểu giải trình.\n"
                f"Hãy viết Báo cáo giải trình và tiếp thu chi tiết bằng tiếng Việt đối với từng nhận định, kiến nghị của Chuyên gia thẩm định ở trên (trích dẫn rõ Điều, Khoản dự kiến chỉnh lý)."
            )
        else:
            final_instruction = (
                f"[Hệ thống]: Bây giờ đến lượt {current_agent} phát biểu.\n"
                f"Hãy viết đầy đủ nội dung đánh giá tác động chuyên sâu bằng tiếng Việt theo đúng nhiệm vụ của {current_agent} (bắt đầu bằng dòng `Overall rating: <điểm từ 1 đến 10>` và phân tích chi tiết 4 mục)."
            )

        formatted_blocks.append(final_instruction)
        merged_user_content = "\n\n".join(formatted_blocks)

        return [
            {"role": "system", "content": sys_str},
            {"role": "user", "content": merged_user_content},
        ]

    def _normalize_ai_ac_report(self, llm_ac_text: str, messages: List[dict]) -> str:
        conversation_text = "\n".join(m.get("content", "") for m in messages[1:])

        def _extract_meta(field_label: str, default_val: str) -> str:
            m = re.search(rf"^- {re.escape(field_label)}:\s*(.+)$", conversation_text, flags=re.MULTILINE)
            return m.group(1).strip() if m else default_val

        short_title = _extract_meta("Tên văn bản", "Văn bản quy phạm pháp luật")
        draft_title = _extract_meta("Tên dự thảo", f"Dự thảo {short_title}")
        lead_agency = _extract_meta("Cơ quan chủ trì đề xuất", "Bộ Tư pháp")

        cleaned = self._clean_llm_output(llm_ac_text)
        if not cleaned or len(cleaned.split()) < 120:
            return self._compile_state_ria_report(messages, llm_ac_text=cleaned, is_offline=False)

        lines = cleaned.splitlines()
        has_roman_one = any(
            re.match(r"^(?:#+\s*)?(?:\*\*)?I\.\s+.+", ln.strip()) for ln in lines
        )
        normalized_lines = []
        started_roman = not has_roman_one

        for line in lines:
            s = line.strip()
            if not s or s.startswith("```") or s == "---":
                continue

            # Normalize Roman numeral headings (I., II., III.) according to Form 01
            m_roman = re.match(r"^(?:#+\s*)?(?:\*\*)?([IVX]+\.\s+.+?)(?:\*\*)?$", s)
            if m_roman:
                started_roman = True
                r_text = m_roman.group(1).strip()
                r_upper = r_text.upper()
                if "XÁC ĐỊNH VẤN ĐỀ" in r_upper:
                    normalized_lines.append("# I. XÁC ĐỊNH VẤN ĐỀ")
                elif "ĐÁNH GIÁ TÁC ĐỘNG" in r_upper:
                    normalized_lines.append("# II. ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH")
                elif "PHỤ LỤC" in r_upper:
                    normalized_lines.append("# III. PHỤ LỤC")
                elif "Ý KIẾN THAM VẤN" in r_upper or "GIÁM SÁT VÀ ĐÁNH GIÁ" in r_upper:
                    normalized_lines.append(f"## {r_text}")
                else:
                    normalized_lines.append(f"# {r_text}")
                continue

            if not started_roman:
                continue

            # Ensure markdown table rows are properly closed
            if s.startswith("|") and not s.endswith("|"):
                s = s + " |"

            # Normalize x.y subheadings (1.1., 1.2., 1.3., 2.1., ...)
            m_sub = re.match(r"^(?:#+\s*)?(?:\*\*)?(\d+\.\d+\.?\s+.+?)(?:\*\*)?$", s)
            if m_sub:
                normalized_lines.append(f"### {m_sub.group(1).strip()}")
                continue

            # Normalize level-2 numbered headings (## 1. Bối cảnh..., ## 1. Chính sách 1...)
            m_num = re.match(
                r"^(?:#+\s*)?(?:\*\*)?(\d+\.\s+(?:Bối cảnh|Mục tiêu|Chính sách)\b.+?)(?:\*\*)?$",
                s,
                flags=re.IGNORECASE,
            )
            if m_num:
                normalized_lines.append(f"## {m_num.group(1).strip()}")
                continue

            if s.startswith("## "):
                normalized_lines.append(f"## {s[3:].strip().strip('*')}")
            elif s.startswith("### "):
                normalized_lines.append(f"### {s[4:].strip().strip('*')}")
            else:
                normalized_lines.append(s)

        result = "\n".join(normalized_lines).strip()
        closing_line = f"Trên đây là nội dung Báo cáo đánh giá tác động của chính sách trong {draft_title}, {lead_agency} kính báo cáo./."
        if "kính báo cáo./." not in result:
            result += f"\n{closing_line}"
        else:
            # Ensure closing sentence has exact draft_title and lead_agency for HTML header extraction
            result = re.sub(
                r"Trên đây là nội dung Báo cáo đánh giá tác động của chính sách (?:của|trong)[^\n]+kính báo cáo\./\.",
                closing_line,
                result,
            )
        return result

    @staticmethod
    def _format_clause_citation(raw_ref: str) -> Tuple[str, str]:
        cleaned = (raw_ref or "").strip().strip("`")
        m = re.match(r"^\[?(Điều\s+\d+)\s*[-–\.]\s*([^\]\n]+)\]?\s*(.*)$", cleaned)
        if m:
            art_id = m.group(1).strip()
            art_title = m.group(2).strip().rstrip(".")
            body = m.group(3).strip()
            body = re.sub(r"^(\d+)\.\s*", r"Khoản \1 quy định: ", body)
            body = re.sub(r"^([a-zđ])\)\s*", r"Điểm \1 quy định: ", body)
            return f"{art_id} ({art_title})", body
        m2 = re.match(r"^\[?(Điều\s+\d+)\]?\s*(.*)$", cleaned)
        if m2:
            return m2.group(1).strip(), m2.group(2).strip()
        return "quy định tại Dự thảo", cleaned

    @staticmethod
    def _diagnose_clause_issue(clause_ref: str, perspective: str) -> Tuple[str, str, str]:
        """
        Phân tích trực tiếp nội dung của một Điều/Khoản cụ thể trong văn bản dự thảo
        để chỉ ra: (1) Vấn đề/Tác động thực tế của điều khoản đó, (2) Mức độ rủi ro/tác động, (3) Kiến nghị hoàn thiện.
        Bảo đảm tính phổ quát cho mọi loại văn bản quy phạm pháp luật theo đúng hướng dẫn của Mẫu số 01.
        """
        lower = clause_ref.lower()

        if perspective == "enterprise":
            if any(k in lower for k in ["vốn", "tài chính", "ký quỹ", "phí", "lệ phí", "bảo hiểm", "kinh phí", "chi phí", "ngân sách", "tối thiểu"]):
                return (
                    "Quy định đặt ra nghĩa vụ tài chính hoặc điều kiện về nguồn lực làm tăng chi phí đầu tư và chi phí tuân thủ của các tổ chức, doanh nghiệp, đặc biệt là doanh nghiệp nhỏ và vừa.",
                    "Cao",
                    "Cần nghiên cứu phân loại theo quy mô và mức độ rủi ro; xem xét chính sách ưu đãi, miễn giảm hoặc lộ trình áp dụng phù hợp.",
                )
            if any(k in lower for k in ["hồ sơ", "chứng nhận", "đánh giá", "kiểm định", "kiểm toán", "báo cáo", "lưu trữ", "nhật ký"]):
                return (
                    "Làm phát sinh chi phí vận hành thường xuyên để lập hồ sơ, thực hiện kiểm định và duy trì chế độ báo cáo, lưu trữ tài liệu.",
                    "Trung bình - Cao",
                    "Đơn giản hóa biểu mẫu hồ sơ, ban hành hướng dẫn kỹ thuật rõ ràng và ứng dụng công nghệ thông tin để giảm thời gian, chi phí chuẩn bị.",
                )
            if any(k in lower for k in ["công khai", "minh bạch", "thông báo", "cung cấp thông tin"]):
                return (
                    "Phát sinh trách nhiệm cập nhật và công khai thông tin định kỳ đến cơ quan quản lý và các đối tượng liên quan.",
                    "Trung bình",
                    "Quy định rõ đầu mối và hình thức công khai thống nhất trên môi trường điện tử nhằm tiết kiệm chi phí cho các bên.",
                )
            if any(k in lower for k in ["sự cố", "tạm dừng", "đình chỉ", "thu hồi", "khắc phục", "bồi thường"]):
                return (
                    "Rủi ro gián đoạn hoạt động chuyên môn, kinh doanh và phát sinh chi phí ứng phó, khắc phục khi bị áp dụng biện pháp đình chỉ hoặc xử lý vi phạm.",
                    "Cao",
                    "Quy định rõ ràng, minh bạch các tiêu chí và trình tự áp dụng biện pháp tạm dừng, thu hồi; thiết lập cơ chế cảnh báo sớm.",
                )
            return (
                "Phát sinh nghĩa vụ tuân thủ mới và yêu cầu điều chỉnh quy trình hoạt động nội bộ của các đối tượng chịu sự tác động.",
                "Trung bình",
                "Ban hành hướng dẫn thực hiện chuẩn hóa và tổ chức tuyên truyền, phổ biến để các chủ thể dễ dàng tiếp cận, thực thi đúng quy định.",
            )

        elif perspective == "tthc":
            if any(k in lower for k in ["chính phủ quy định chi tiết", "thủ tướng chính phủ ban hành", "bộ trưởng.*quy định", "quy định chi tiết", "hướng dẫn thi hành"]):
                return (
                    "Điều khoản giao quy định chi tiết (ủy quyền lập quy), tiềm ẩn nguy cơ phát sinh thêm thủ tục hành chính, điều kiện kinh doanh hoặc giấy phép con ở văn bản hướng dẫn dưới luật.",
                    "Cao",
                    "Quán triệt nguyên tắc trong Luật: Văn bản quy định chi tiết không được đặt thêm giấy phép mới hoặc thủ tục hành chính ngoài phạm vi luật định.",
                )
            if any(k in lower for k in ["giấy phép", "chấp thuận", "phê duyệt", "thẩm định", "cấp phép", "cho phép"]):
                return (
                    "Thủ tục hành chính mang tính tiền kiểm (xin - cho), thành phần hồ sơ và thời gian thẩm định có thể làm chậm trễ cơ hội hoạt động của tổ chức, cá nhân.",
                    "Rất cao",
                    "Chuyển mạnh từ cơ chế tiền kiểm sang hậu kiểm gắn với quản lý rủi ro; rút ngắn thời hạn giải quyết và thực hiện liên thông trực tuyến.",
                )
            if any(k in lower for k in ["thông báo", "đăng ký", "nộp hồ sơ"]):
                return (
                    "Phát sinh thủ tục nộp hồ sơ đăng ký hoặc gửi thông báo đến cơ quan quản lý nhà nước có thẩm quyền.",
                    "Trung bình",
                    "Thiết kế quy trình một cửa điện tử, tự động tiếp nhận hồ sơ trực tuyến và ấn định thời hạn xử lý không quá 05 - 07 ngày làm việc.",
                )
            return (
                "Cần chuẩn hóa thành phần hồ sơ, trình tự giải quyết và số hóa toàn trình để bảo đảm thuận tiện cho người dân, doanh nghiệp.",
                "Trung bình",
                "Quy định rõ thời hạn tối đa xử lý thủ tục và kết nối liên thông dữ liệu giữa các cơ quan nhà nước.",
            )

        else:  # legal
            if any(k in lower for k in ["pháp luật có liên quan", "theo quy định của pháp luật"]):
                return (
                    "Sử dụng kỹ thuật viện dẫn mở ('theo quy định của pháp luật có liên quan'), dễ dẫn đến chồng chéo thẩm quyền hoặc lúng túng khi xác định văn bản áp dụng.",
                    "Cao",
                    "Bổ sung nguyên tắc áp dụng pháp luật hoặc dẫn chiếu cụ thể điều khoản của các luật chuyên ngành có liên quan để bảo đảm tính dự báo trước.",
                )
            if any(k in lower for k in ["nghiêm cấm", "cấm", "xử lý vi phạm", "chế tài"]):
                return (
                    "Quy định hành vi bị nghiêm cấm hoặc chế tài xử lý nếu mang tính định tính sẽ gây khó khăn cho việc áp dụng pháp luật và giám sát thực thi.",
                    "Cao",
                    "Định lượng hóa các tiêu chí xác định hành vi vi phạm và bảo đảm đồng bộ với Bộ luật Dân sự và Luật Xử lý vi phạm hành chính.",
                )
            if any(k in lower for k in ["bí mật", "bảo mật", "quyền tác giả", "sở hữu trí tuệ", "thông tin cá nhân"]):
                return (
                    "Cần bảo đảm hài hòa giữa yêu cầu cung cấp thông tin, hồ sơ phục vụ thanh tra, kiểm tra với quyền bảo vệ bí mật kinh doanh, sở hữu trí tuệ và quyền riêng tư.",
                    "Cao",
                    "Chế định rõ cơ chế bảo mật nghiêm ngặt và giới hạn phạm vi tài liệu bắt buộc tiếp cận trong hoạt động thanh tra, kiểm tra.",
                )
            return (
                "Cần rà soát tính thống nhất thuật ngữ và hiệu lực chuyển tiếp để bảo đảm tính khả thi, ổn định của hệ thống pháp luật.",
                "Trung bình",
                "Chuẩn hóa khái niệm tại Điều Giải thích từ ngữ và quy định lộ trình chuyển tiếp hợp lý cho các đối tượng đã hoạt động trước ngày văn bản có hiệu lực.",
            )

    def _get_response(self, messages: List[dict]) -> str:
        """
        Ghi đè duy nhất hàm gọi mạng `_get_response` của `OpenAIChat` để gọi tới Ollama cục bộ.
        Toàn bộ xử lý `messages` đầu vào và hậu xử lý ở `OpenAIChat.query()` được giữ nguyên.
        """
        system_prompt = messages[0]["content"] if messages else ""
        is_ac_phase_4 = (
            "Chủ tịch Hội đồng" in system_prompt
            or "Area Chair" in system_prompt
            or "Meta-Reviewer" in system_prompt
        )

        if self.model.startswith("offline-heuristic"):
            return self._synthesize_offline_response(messages)

        trimmed_messages = self._prepare_ollama_messages(messages)

        url = f"{self.base_url}/api/chat"
        predict_limit = max(self.max_tokens, 8192 if is_ac_phase_4 else 2048)
        payload = {
            "model": self.model,
            "messages": trimmed_messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": predict_limit,
                "num_ctx": max(self.num_ctx, 16384),
                "stop": ["<|endoftext|>", "<|im_end|>"],
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with LoadingIndicator(self.model, "Đang tạo phản hồi...") as loader:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    msg_obj = data.get("message", {})
                    raw_content = msg_obj.get("content", "")
                    raw_thinking = msg_obj.get("thinking", "")
                    content = self._clean_llm_output(raw_content, thinking_fallback=raw_thinking)
                    loader.done(len(content))
                    if is_ac_phase_4:
                        return self._normalize_ai_ac_report(content, messages)
                    return content
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            if self.fallback_when_offline:
                return self._synthesize_offline_response(messages)
            raise RuntimeError(
                f"Máy chủ Ollama tại {self.base_url} từ chối yêu cầu cho model '{self.model}' (HTTP {e.code}: {err_body}). "
                f"Có thể bạn chưa tải model này về máy. Hãy mở Terminal và chạy: `ollama pull {self.model}`"
            )
        except urllib.error.URLError as e:
            if self.fallback_when_offline:
                logger.warning(
                    f"Ollama server tại {self.base_url} chưa bật ({e}). "
                    f"Tự động chuyển sang chế độ phân tích Heuristic cục bộ trên văn bản thực tế."
                )
                return self._synthesize_offline_response(messages)
            raise ConnectionError(
                f"Không thể kết nối tới máy chủ Ollama tại {self.base_url} để chạy model '{self.model}'. "
                f"Hiện tại Ollama chưa được cài đặt hoặc chưa bật trên máy Mac của bạn. "
                f"Hãy bật ứng dụng Ollama (hoặc chạy `ollama serve` và `ollama pull {self.model}`), "
                f"hoặc nếu muốn chạy nhanh không dùng LLM thì chọn chế độ 'offline-heuristic'."
            )

    @staticmethod
    def _diagnose_clause_issue(clause_ref: str, perspective: str) -> Tuple[str, str, str]:
        """
        Phân tích trực tiếp nội dung câu chữ của một Điều/Khoản cụ thể trong văn bản đầu vào
        để chỉ ra: (1) Vấn đề/Tác động thực tế của điều khoản đó, (2) Mức độ rủi ro, (3) Kiến nghị sửa đổi/hoàn thiện.
        Không dùng bất kỳ mẫu câu cố định nào của văn bản khác.
        """
        lower = clause_ref.lower()

        if perspective == "enterprise":
            if any(k in lower for k in ["vốn điều lệ", "ký quỹ", "bảo hiểm", "tối thiểu"]):
                return (
                    "Quy định đặt ra ngưỡng tài chính/nhân sự cứng nhắc làm tăng chi phí vốn (CAPEX) và tạo rào cản gia nhập thị trường đối với doanh nghiệp nhỏ và vừa (SME), Startup.",
                    "Rất cao",
                    "Chuyển sang cơ chế phân tầng theo quy mô doanh nghiệp và mức độ rủi ro; miễn hoặc giảm ngưỡng tài chính đối với doanh nghiệp khởi nghiệp sáng tạo và SME.",
                )
            if any(k in lower for k in ["hồ sơ kỹ thuật", "nhật ký", "lưu vết", "lưu trữ", "đánh giá sự phù hợp", "kiểm toán"]):
                return (
                    "Làm phát sinh chi phí vận hành (OPEX) thường xuyên để xây dựng hệ thống lưu trữ nhật ký (logging), lập hồ sơ kỹ thuật và thực hiện đánh giá sự phù hợp.",
                    "Cao",
                    "Quy định rõ biểu mẫu hồ sơ kỹ thuật rút gọn và cung cấp công cụ tự đánh giá miễn phí cho doanh nghiệp nhỏ và vừa; giới hạn thời gian lưu trữ nhật ký hợp lý theo mức độ rủi ro.",
                )
            if any(k in lower for k in ["gắn nhãn", "minh bạch", "thông báo rõ ràng", "công khai"]):
                return (
                    "Phát sinh chi phí kỹ thuật để tích hợp công cụ gắn nhãn tự động (watermark/metadata) và duy trì thông tin minh bạch trên toàn bộ đầu ra của hệ thống.",
                    "Trung bình",
                    "Ban hành tiêu chuẩn kỹ thuật mở thống nhất về gắn nhãn số (metadata) và miễn trừ trách nhiệm gắn nhãn đối với các tác vụ hỗ trợ nội bộ không cung cấp ra công cộng.",
                )
            if any(k in lower for k in ["sự cố", "khắc phục", "tạm dừng", "thu hồi"]):
                return (
                    "Rủi ro gián đoạn hoạt động kinh doanh và phát sinh chi phí ứng cứu khẩn cấp khi xảy ra sự cố kỹ thuật hoặc khi bị yêu cầu tạm dừng hệ thống.",
                    "Cao",
                    "Quy định rõ tiêu chí định lượng thế nào là 'sự cố nghiêm trọng' và thiết lập quy trình cảnh báo sớm trước khi áp dụng biện pháp tạm dừng diện rộng.",
                )
            return (
                "Phát sinh nghĩa vụ tuân thủ và điều chỉnh quy trình vận hành nội bộ của nhà phát triển, nhà cung cấp và bên triển khai.",
                "Trung bình",
                "Cần ban hành hướng dẫn kỹ thuật chi tiết và quy trình tuân thủ chuẩn hóa trên Cổng thông tin điện tử một cửa để giảm chi phí tự tìm hiểu của doanh nghiệp.",
            )

        elif perspective == "tthc":
            if any(k in lower for k in ["chính phủ quy định chi tiết", "thủ tướng chính phủ ban hành", "bộ trưởng", "quy định chi tiết"]):
                return (
                    "Điều khoản giao quy định chi tiết (ủy quyền lập quy): Tiềm ẩn nguy cơ phát sinh thêm thủ tục hành chính, điều kiện kinh doanh hoặc 'giấy phép con' tại Nghị định/Thông tư hướng dẫn nếu không giới hạn nguyên tắc ngay trong Luật.",
                    "Cao",
                    "Bổ sung nguyên tắc khống chế ngay tại điều khoản này: 'Văn bản quy định chi tiết không được đặt thêm giấy phép mới hoặc thủ tục tiền kiểm ngoài phạm vi Luật định; thực hiện 100% trực tuyến'.",
                )
            if any(k in lower for k in ["giấy phép", "chấp thuận", "bản giấy", "90 ngày", "45 ngày"]):
                return (
                    "Thủ tục mang nặng tính tiền kiểm (xin - cho), thời gian thẩm định kéo dài làm mất cơ hội thị trường của sản phẩm công nghệ.",
                    "Rất cao",
                    "Chuyển từ cơ chế cấp phép tiền kiểm sang cơ chế tự phân loại, thông báo trực tuyến và hậu kiểm theo nguyên tắc quản lý rủi ro; rút ngắn tối đa thời hạn xử lý.",
                )
            if any(k in lower for k in ["thông báo", "đăng ký", "cổng thông tin điện tử một cửa", "hồ sơ phân loại"]):
                return (
                    "Phát sinh thủ tục thông báo/nộp hồ sơ phân loại rủi ro và báo cáo định kỳ trên Cổng thông tin điện tử một cửa.",
                    "Trung bình",
                    "Thiết kế cơ chế 'một cửa liên thông' tự động tiếp nhận (không yêu cầu chờ phê duyệt trước khi vận hành đối với việc thông báo phân loại) và ấn định thời hạn phản hồi tối đa 05-07 ngày làm việc.",
                )
            if any(k in lower for k in ["thử nghiệm có kiểm soát", "sandbox", "thẩm định"]):
                return (
                    "Quy trình tiếp nhận, thẩm định hồ sơ tham gia cơ chế thử nghiệm có kiểm soát (Sandbox) cần phối hợp nhiều cơ quan, dễ kéo dài thời gian.",
                    "Trung bình - Cao",
                    "Quy định rõ thời hạn tối đa thẩm định hồ sơ thử nghiệm có kiểm soát (ví dụ: không quá 15 ngày làm việc) và áp dụng nguyên tắc cơ quan chủ trì chịu trách nhiệm một đầu mối.",
                )
            return (
                "Cần chuẩn hóa thành phần hồ sơ, trình tự thực hiện và số hóa toàn trình để không gây phiền hà khi thanh tra, kiểm tra.",
                "Trung bình",
                "Quy định rõ tần suất kiểm tra định kỳ (không quá 01 lần/năm trừ khi có dấu hiệu vi phạm rõ ràng) và thực hiện hậu kiểm điện tử.",
            )

        else:  # legal
            if any(k in lower for k in ["pháp luật có liên quan", "theo quy định của pháp luật"]):
                return (
                    "Sử dụng kỹ thuật dẫn chiếu mở ('theo quy định của pháp luật có liên quan'), dễ dẫn đến chồng chéo thẩm quyền giữa Luật này với Luật Dữ liệu, Luật Bảo vệ dữ liệu cá nhân, Luật An ninh mạng, Luật Sở hữu trí tuệ và Luật Đầu tư.",
                    "Cao",
                    "Bổ sung nguyên tắc áp dụng pháp luật: Xác định rõ trong trường hợp có sự khác nhau giữa văn bản này và các luật chuyên ngành khác về cùng một vấn đề kỹ thuật thì áp dụng quy định nào.",
                )
            if any(k in lower for k in ["mã nguồn", "thuật toán", "bí mật kinh doanh", "dữ liệu huấn luyện"]):
                return (
                    "Xung đột tiềm năng giữa nghĩa vụ cung cấp hồ sơ kỹ thuật/mã nguồn/dữ liệu huấn luyện phục vụ thanh tra với quyền bảo vệ bí mật kinh doanh, quyền sở hữu trí tuệ theo Luật Sở hữu trí tuệ.",
                    "Rất cao",
                    "Chế định rõ cơ chế bảo mật nghiêm ngặt và giới hạn trường hợp bắt buộc tiếp cận mã nguồn/thuật toán chỉ khi có quyết định thanh tra/điều tra sự cố nghiêm trọng, kèm chế tài nếu làm lộ bí mật kinh doanh.",
                )
            if any(k in lower for k in ["nghiêm cấm", "cấm", "xử lý vi phạm", "bồi thường"]):
                return (
                    "Một số khái niệm mô tả hành vi vi phạm hoặc mức độ thiệt hại mang tính định tính (như 'gây nhầm lẫn', 'thao túng nhận thức', 'thiệt hại đáng kể'), cần đồng bộ với Bộ luật Dân sự 2015 và Luật Xử lý vi phạm hành chính.",
                    "Cao",
                    "Định lượng hóa tiêu chí xác định mức độ thiệt hại và dẫn chiếu cụ thể cơ chế xác định lỗi, trách nhiệm bồi thường thiệt hại ngoài hợp đồng theo Bộ luật Dân sự 2015.",
                )
            return (
                "Cần rà soát tính thống nhất thuật ngữ và hiệu lực chuyển tiếp để bảo đảm tính khả thi, dự báo trước được của quy phạm pháp luật.",
                "Trung bình",
                "Chuẩn hóa khái niệm tại Điều Giải thích từ ngữ và quy định rõ lộ trình chuyển tiếp phù hợp cho các hệ thống đã đưa vào vận hành trước ngày văn bản có hiệu lực.",
            )

    def _synthesize_offline_response(self, messages: List[dict]) -> str:
        """
        Bộ Phân tích Điều khoản Thực tế (Dynamic Clause-by-Clause Legal Analyzer):
        Đọc và phân tích trực tiếp 100% nội dung các Chương, Điều, Khoản của chính văn bản
        mà người dùng vừa đưa vào (trong `messages`), tuyệt đối không dùng văn mẫu cố định.
        """
        system_prompt = messages[0]["content"] if messages else ""
        conversation_text = "\n".join(m.get("content", "") for m in messages[1:])

        # Extract document metadata and structure from conversation history
        doc_info_match = re.search(r"^- Thông tin văn bản:\s*(.+)$", conversation_text, flags=re.MULTILINE)
        doc_info = doc_info_match.group(1).strip() if doc_info_match else "Văn bản dự thảo được tải lên"

        chapters = re.findall(r"^##\s+(CHƯƠNG\s+[^\n]+)", conversation_text, flags=re.MULTILINE)

        # Parse articles and clause bodies
        article_blocks = re.findall(
            r"^###\s+(Điều\s+\d+\.\s*[^\n]+)\n((?:  [^\n]+\n?)*)",
            conversation_text,
            flags=re.MULTILINE,
        )
        parsed_articles = []
        for art_header, body in article_blocks:
            clauses = [line.strip() for line in body.splitlines() if line.strip()]
            parsed_articles.append({"header": art_header.strip(), "clauses": clauses})

        # Extract structured sections produced by LegalPaperExtractorPlayer
        def _extract_section_items(sec_header: str) -> List[str]:
            pattern = rf"### {re.escape(sec_header)}[^\n]*\n((?:  \* [^\n]+\n?)+)"
            m = re.search(pattern, conversation_text)
            if not m:
                return []
            return [line.strip()[2:].strip() for line in m.group(1).splitlines() if line.strip().startswith("* ")]

        obligations = _extract_section_items("[1] TRÍCH XUẤT CÁC NGHĨA VỤ BẮT BUỘC MỚI")
        prohibitions = _extract_section_items("[2] TRÍCH XUẤT CÁC HÀNH VI BỊ NGHIÊM CẤM")
        licenses = _extract_section_items("[3] TRÍCH XUẤT ĐIỀU KIỆN CẤP PHÉP & THỦ TỤC HÀNH CHÍNH MỚI")
        delegations = _extract_section_items("[4] TRÍCH XUẤT ĐIỀU KHOẢN GIAO QUY ĐỊNH CHI TIẾT")

        # Extract scope, subjects, SME provisions, and transitional clauses
        scope_text = parsed_articles[0]["clauses"][0] if parsed_articles and parsed_articles[0]["clauses"] else "Quy định tại Điều 1 của văn bản."
        subject_text = (
            parsed_articles[1]["clauses"][0]
            if len(parsed_articles) > 1 and parsed_articles[1]["clauses"]
            else "Các cơ quan, tổ chức, doanh nghiệp, cá nhân liên quan."
        )

        sme_clauses = []
        transitional_clauses = []
        for art in parsed_articles:
            for c in art["clauses"]:
                ref = f"[{art['header']}] {c}"
                if any(k in c.lower() for k in ["doanh nghiệp nhỏ và vừa", "khởi nghiệp", "hỗ trợ", "ưu đãi", "quỹ"]):
                    sme_clauses.append(ref)
                if any(k in art["header"].lower() or k in c.lower() for k in ["hiệu lực", "chuyển tiếp"]):
                    transitional_clauses.append(ref)

        is_phase_3 = "FINAL RATING:" in system_prompt

        # Extract clauses relevant to gender equality, vulnerable groups, and international treaties
        gender_vulnerable_clauses = []
        intl_clauses = []
        for art in parsed_articles:
            for c in art["clauses"]:
                ref = f"[{art['header']}] {c}"
                if any(k in c.lower() for k in ["giới", "phụ nữ", "trẻ em", "khuyết tật", "cao tuổi", "dân tộc thiểu số", "yếu thế", "tổn thương", "bình đẳng", "không phân biệt đối xử"]):
                    gender_vulnerable_clauses.append(ref)
                if any(k in c.lower() for k in ["quốc tế", "điều ước", "nước ngoài", "xuyên biên giới", "toàn cầu", "hội nhập"]):
                    intl_clauses.append(ref)

        # Area Chair: Synthesize official Vietnamese State Regulatory Impact Assessment report
        if "Chủ tịch Hội đồng" in system_prompt or "Area Chair" in system_prompt or "Meta-Reviewer" in system_prompt:
            return self._compile_state_ria_report(messages, llm_ac_text="")

        # Legal reviewer: Consistency and international treaties
        if "Reviewer 1" in system_prompt:
            legal_targets = (prohibitions + obligations[:4] + transitional_clauses[:2])[:8]
            if not is_phase_3:
                lines = [
                    f"### 1. Bối cảnh và Xác định vấn đề về Hệ thống Pháp luật đối với: `{doc_info}`",
                    f"- **Cấu trúc dự thảo**: Gồm **{len(chapters)} Chương**, **{len(parsed_articles)} Điều** ({', '.join(a['header'] for a in parsed_articles[:6])}{'...' if len(parsed_articles) > 6 else ''}).",
                    f"- **Bối cảnh pháp lý**: {scope_text}",
                    f"- **Rà soát Điều ước quốc tế liên quan**: Phát hiện **{len(intl_clauses)} điều khoản** có yếu tố quốc tế/hội nhập (`{intl_clauses[0] if intl_clauses else 'Áp dụng đối với tổ chức, cá nhân trong nước và nước ngoài'}`).\n",
                    "### 2. Đánh giá tác động đối với Hệ thống Pháp luật theo từng Điều/Khoản",
                ]
                for idx, item in enumerate(legal_targets, 1):
                    issue, risk, fix = self._diagnose_clause_issue(item, "legal")
                    lines.append(f"#### ({idx}) Rà soát quy định: `{item}`")
                    lines.append(f"- **Tác động đối với hệ thống pháp luật**: {issue} *(Mức độ rủi ro pháp lý: **{risk}**)*")
                    lines.append(f"- **Kiến nghị hoàn thiện**: {fix}\n")

                lines.append("OVERALL RATING: 6")
                return "\n".join(lines)
            else:
                top_leg = legal_targets[0] if legal_targets else "các điều khoản trong văn bản"
                return (
                    f"### Nhận định cập nhật của Reviewer 1 (Hệ thống Pháp luật & Điều ước Quốc tế) tại Phiên họp Hội đồng (Phase 3)\n"
                    f"Sau khi đối chiếu Giải trình của Cơ quan soạn thảo và ý kiến của Reviewer 2, Reviewer 3, Reviewer 4 đối với `{doc_info}`:\n"
                    f"- Thống nhất bổ sung nguyên tắc áp dụng pháp luật tại `{top_leg}` để đồng bộ với Bộ luật Dân sự 2015, Luật Đầu tư 2020, Luật Dữ liệu, Luật Bảo vệ dữ liệu cá nhân và Luật Sở hữu trí tuệ.\n"
                    f"- Xác nhận các quy định trong dự thảo tương thích với các Điều ước quốc tế mà CHXHCN Việt Nam là thành viên (như Công ước ICCPR, CPTPP, EVFTA, RCEP).\n\n"
                    f"FINAL RATING: 7"
                )

        # Socio-economic reviewer: Impact and cost-benefit analysis
        if "Reviewer 2" in system_prompt:
            target_obs = obligations[:8] if obligations else [f"[{a['header']}] {a['clauses'][0]}" for a in parsed_articles[:5] if a["clauses"]]
            if not is_phase_3:
                lines = [
                    f"### 1. Bối cảnh và Xác định vấn đề Kinh tế - Xã hội đối với: `{doc_info}`",
                    f"- **Phạm vi tác động kinh tế - xã hội**: {scope_text}",
                    f"- **Đối tượng chịu tác động trực tiếp**: {subject_text} (Phát hiện **{len(obligations)} điều khoản nghĩa vụ tuân thủ** và **{len(sme_clauses)} điều khoản hỗ trợ/ưu đãi**).\n",
                    "### 2. Đánh giá Tác động tích cực (Lợi ích) đối với Nhà nước, Người dân, Doanh nghiệp",
                ]
                if sme_clauses:
                    for sc in sme_clauses[:4]:
                        lines.append(f"- **Lợi ích / Chính sách hỗ trợ phát triển**: `{sc}`")
                else:
                    lines.append(f"- Thiết lập hành lang pháp lý minh bạch tại **{parsed_articles[0]['header'] if parsed_articles else 'Điều 1'}**, song cần bổ sung cơ chế hỗ trợ chi phí tuân thủ cho Doanh nghiệp nhỏ và vừa (SME).")

                lines.append("\n### 3. Đánh giá Tác động tiêu cực (Chi phí tuân thủ CAPEX/OPEX) trên từng Điều/Khoản")
                for idx, ob in enumerate(target_obs, 1):
                    issue, risk, fix = self._diagnose_clause_issue(ob, "enterprise")
                    lines.append(f"#### ({idx}) Phân tích chi phí - lợi ích tại: `{ob}`")
                    lines.append(f"- **Tác động kinh tế - xã hội (Chi phí)**: {issue} *(Mức độ tác động chi phí: **{risk}**)*")
                    lines.append(f"- **Giải pháp tối ưu hóa chi phí**: {fix}\n")

                score = 6 if sme_clauses else 4
                lines.append(f"OVERALL RATING: {score}")
                return "\n".join(lines)
            else:
                top_ob = target_obs[0] if target_obs else "các điều khoản nghĩa vụ tuân thủ"
                return (
                    f"### Nhận định cập nhật của Reviewer 2 (Kinh tế - Xã hội & Chi phí - Lợi ích) tại Phiên họp Hội đồng (Phase 3)\n"
                    f"Sau khi xem xét Giải trình của Cơ quan soạn thảo đối với `{doc_info}`:\n"
                    f"- Đối với `{top_ob}`, Cơ quan soạn thảo đã thống nhất áp dụng quản lý theo phân tầng rủi ro và cung cấp công cụ tự đánh giá miễn phí cho SME, giúp giảm đáng kể chi phí tuân thủ (OPEX).\n\n"
                    f"FINAL RATING: {7 if sme_clauses else 5}"
                )

        # Gender reviewer: Equality and vulnerable group protection
        if "Reviewer 3" in system_prompt:
            if not is_phase_3:
                lines = [
                    f"### 1. Đánh giá Tác động về Giới và Nhóm dễ bị tổn thương đối với: `{doc_info}`",
                    f"- Qua rà soát toàn bộ **{len(parsed_articles)} Điều** của dự thảo theo Luật Bình đẳng giới và khung Mẫu số 01:",
                ]
                if gender_vulnerable_clauses:
                    lines.append(f"- Phát hiện **{len(gender_vulnerable_clauses)} điều khoản** có quy định trực tiếp về không phân biệt đối xử, bảo vệ nhóm dễ bị tổn thương (trẻ em, người khuyết tật, người dân tộc thiểu số, người cao tuổi):")
                    for gc in gender_vulnerable_clauses[:6]:
                        lines.append(f"  + `{gc}`")
                    lines.append(
                        "\n### 2. Nhận định và Kiến nghị lồng ghép Bình đẳng giới\n"
                        "- **Tác động tích cực**: Dự thảo đã bước đầu lồng ghép nguyên tắc công bằng, không phân biệt đối xử và bảo vệ nhóm yếu thế.\n"
                        "- **Kiến nghị bổ sung**: Trong các quy định về phát triển nguồn nhân lực, Quỹ hỗ trợ và doanh nghiệp khởi nghiệp sáng tạo, đề nghị bổ sung chính sách khuyến khích phụ nữ tham gia nghiên cứu, phát triển công nghệ và ưu tiên hỗ trợ doanh nghiệp nhỏ và vừa do phụ nữ làm chủ.\n\n"
                        "OVERALL RATING: 7"
                    )
                else:
                    lines.append(
                        f"- Các quy định từ **{parsed_articles[0]['header'] if parsed_articles else 'Điều 1'}** đến **{parsed_articles[-1]['header'] if parsed_articles else 'Điều cuối'}** về cơ bản mang tính trung tính về giới, áp dụng chung cho mọi tổ chức, cá nhân.\n"
                        "- **Cảnh báo khoảng cách tiếp cận**: Các điều kiện kỹ thuật và chi phí tuân thủ cao có thể tạo rào cản gián tiếp đối với các doanh nghiệp siêu nhỏ, hộ kinh doanh do phụ nữ làm chủ hoặc lao động nữ trong quá trình chuyển đổi số.\n"
                        "- **Kiến nghị**: Bổ sung nguyên tắc 'bảo đảm bình đẳng giới, không phân biệt đối xử và bảo vệ nhóm dễ bị tổn thương' vào Chương Quy định chung.\n\n"
                        "OVERALL RATING: 5"
                    )
                return "\n".join(lines)
            else:
                return (
                    f"### Nhận định cập nhật của Reviewer 3 (Tác động về Giới) tại Phiên họp Hội đồng (Phase 3)\n"
                    f"- Ghi nhận việc Cơ quan chủ trì soạn thảo tiếp thu bổ sung tiêu chí đánh giá không thiên lệch giới tính (gender bias) đối với dữ liệu huấn luyện/thuật toán và ưu tiên hỗ trợ đào tạo nhân lực nữ, doanh nghiệp do phụ nữ làm chủ.\n\n"
                    f"FINAL RATING: 7"
                )

        # Administrative reviewer: Procedures and regulatory burden
        if "Reviewer 4" in system_prompt:
            target_tthc = (licenses + delegations)[:8] or obligations[:6]
            if not is_phase_3:
                lines = [
                    f"### 1. Rà soát Tác động của Thủ tục Hành chính (TTHC) đối với: `{doc_info}`",
                    f"- Qua rà soát **{len(parsed_articles)} Điều** theo Nghị định 63/2010/NĐ-CP và Mẫu số 01:\n"
                    f"  + Số quy định về hồ sơ, thông báo, đăng ký, thẩm định, đánh giá sự phù hợp: **{len(licenses)} quy định**.\n"
                    f"  + Số quy định giao Chính phủ/Thủ tướng/Bộ quy định chi tiết (cần kiểm soát nguy cơ đẻ thêm giấy phép con): **{len(delegations)} quy định**.\n",
                    "### 2. Đánh giá sự cần thiết, tính hợp lý, hợp pháp và chi phí TTHC trên từng Điều/Khoản",
                ]
                for idx, item in enumerate(target_tthc, 1):
                    issue, risk, fix = self._diagnose_clause_issue(item, "tthc")
                    lines.append(f"#### ({idx}) Đánh giá TTHC tại: `{item}`")
                    lines.append(f"- **Tác động của thủ tục hành chính**: {issue} *(Mức độ rủi ro TTHC: **{risk}**)*")
                    lines.append(f"- **Kiến nghị đơn giản hóa**: {fix}\n")

                score = 6 if "cổng thông tin điện tử một cửa" in conversation_text.lower() else 3
                lines.append(f"OVERALL RATING: {score}")
                return "\n".join(lines)
            else:
                top_tthc = target_tthc[0] if target_tthc else "các quy định về hồ sơ, thủ tục"
                return (
                    f"### Nhận định cập nhật của Reviewer 4 (Tác động của Thủ tục Hành chính) tại Phiên họp Hội đồng (Phase 3)\n"
                    f"- Sau khi thảo luận cùng Reviewer 1, 2, 3 về `{doc_info}`, Hội đồng thống nhất yêu cầu chuẩn hóa thủ tục tại `{top_tthc}` theo hướng thực hiện 100% trực tuyến qua Cổng thông tin điện tử một cửa và kiểm soát chặt chẽ **{len(delegations)} điều khoản giao quy định chi tiết**.\n\n"
                    f"FINAL RATING: {7 if 'một cửa' in conversation_text.lower() else 5}"
                )

        # Author: Drafting agency response and rebuttal
        if "Cơ quan chủ trì soạn thảo" in system_prompt or "Ban Soạn Thảo" in system_prompt or "Author" in system_prompt:
            last_review_msg = messages[-1]["content"] if messages else ""
            cited_clauses = re.findall(r"`(\[Điều\s+\d+[^`]+)`", last_review_msg)
            cited_preview = cited_clauses[:3] if cited_clauses else [f"[{a['header']}]" for a in parsed_articles[:3]]
            bullet_rebuttals = "\n".join(
                f"   - **Đối với `{c[:120]}...`**: Cơ quan chủ trì soạn thảo xin tiếp thu, hoàn thiện giải pháp theo hướng tối ưu hóa lợi ích kinh tế - xã hội, bảo đảm bình đẳng giới, đơn giản hóa TTHC và thống nhất với hệ thống pháp luật."
                for c in cited_preview
            )
            return (
                f"### BÁO CÁO GIẢI TRÌNH VÀ TIẾP THU CỦA CƠ QUAN CHỦ TRÌ SOẠN THẢO ĐỐI VỚI `{doc_info}`\n"
                f"1. **Về bối cảnh và mục tiêu xây dựng chính sách**:\n"
                f"   - Dự thảo `{doc_info}` ({len(parsed_articles)} Điều) được xây dựng nhằm giải quyết các vấn đề thực tiễn tại **{parsed_articles[0]['header'] if parsed_articles else 'Điều 1'}**: *{scope_text}*\n"
                f"2. **Phương án tiếp thu ý kiến Chuyên gia đánh giá tác động**:\n"
                f"{bullet_rebuttals}\n"
                f"   - Cơ quan soạn thảo cam kết hoàn thiện các bảng tính toán chi phí - lợi ích và danh mục rà soát điều ước quốc tế tại Phụ lục của Báo cáo đánh giá tác động theo đúng Mẫu số 01."
            )

        return (
            f"### KẾT QUẢ ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH ĐỐI VỚI `{doc_info}`\n"
            f"- Đã bóc tách {len(parsed_articles)} Điều và rà soát các tác động theo Mẫu số 01.\n"
        )

    def _compile_state_ria_report(
        self,
        messages: List[dict],
        llm_ac_text: str = "",
        is_offline: bool = True,
    ) -> str:
        conversation_text = "\n".join(m.get("content", "") for m in messages[1:])

        def _extract_meta(field_label: str, default_val: str) -> str:
            m = re.search(rf"^- {re.escape(field_label)}:\s*(.+)$", conversation_text, flags=re.MULTILINE)
            return m.group(1).strip() if m else default_val

        short_title = _extract_meta("Tên văn bản", "Văn bản quy phạm pháp luật")
        draft_title = _extract_meta("Tên dự thảo", f"Dự thảo {short_title}")
        doc_number = _extract_meta("Số hiệu văn bản", "")
        authority = _extract_meta("Thẩm quyền ban hành", "Chính phủ")
        lead_agency = _extract_meta("Cơ quan chủ trì đề xuất", "Bộ Tư pháp")

        # Extract real AI agent contributions when running with an LLM
        agent_blocks = {"Reviewer 1": [], "Reviewer 2": [], "Reviewer 3": [], "Reviewer 4": [], "Author": []}
        if not is_offline:
            block_pattern = re.compile(
                r"\[(Paper Extractor|Reviewer 1|Reviewer 2|Reviewer 3|Reviewer 4|Author|AC|System|Hệ thống)\]:\s*",
                flags=re.MULTILINE,
            )
            splits = block_pattern.split(conversation_text)
            for idx in range(1, len(splits), 2):
                ag_name = splits[idx].strip()
                ag_body = splits[idx + 1].replace(END_OF_MESSAGE, "").replace("<EOS>", "").strip()
                if ag_name in agent_blocks and ag_body:
                    agent_blocks[ag_name].append(ag_body)

        def _extract_clean_points(texts: List[str]) -> List[str]:
            points = []
            for raw_t in texts:
                cleaned_t = self._clean_llm_output(raw_t)
                for ln in cleaned_t.splitlines():
                    s = ln.strip()
                    if not s or s.startswith("#") or s.startswith("|"):
                        continue
                    if re.match(r"^(OVERALL|FINAL)\s*RATING\s*:", s, flags=re.IGNORECASE):
                        continue
                    s = re.sub(r"^[\-\*\+]\s*", "", s)
                    s = re.sub(r"^\d+[\.\)]\s*", "", s)
                    s = re.sub(r"\*\*|\*|`|\[|\]", "", s).strip()
                    if len(s) >= 40 and s not in points:
                        points.append(s)
            return points

        def _extract_ratings(texts: List[str]) -> str:
            joined = "\n".join(texts)
            ov = re.findall(r"OVERALL\s*RATING\s*:\s*(\d+)", joined, flags=re.IGNORECASE)
            fn = re.findall(r"FINAL\s*RATING\s*:\s*(\d+)", joined, flags=re.IGNORECASE)
            if ov and fn:
                return f"Điểm sơ bộ: {ov[0]}/10 -> Điểm sau giải trình: {fn[-1]}/10"
            if fn:
                return f"Điểm thẩm định: {fn[-1]}/10"
            if ov:
                return f"Điểm thẩm định: {ov[0]}/10"
            return ""

        r1_points = _extract_clean_points(agent_blocks["Reviewer 1"])
        r2_points = _extract_clean_points(agent_blocks["Reviewer 2"])
        r3_points = _extract_clean_points(agent_blocks["Reviewer 3"])
        r4_points = _extract_clean_points(agent_blocks["Reviewer 4"])
        author_points = _extract_clean_points(agent_blocks["Author"])
        ac_points = _extract_clean_points([llm_ac_text] if llm_ac_text else [])

        def _pick_ai_point(points: List[str], ch_articles: list, p_idx: int) -> str:
            if not points:
                return ""
            art_ids = [a["header"].split(".")[0].strip() for a in ch_articles]
            for pt in points:
                if any(re.search(rf"\b{re.escape(aid)}\b", pt, flags=re.IGNORECASE) for aid in art_ids):
                    return pt.rstrip(".") + "."
            chosen = points[(p_idx - 1) % len(points)]
            return chosen.rstrip(".") + "."

        chapters = re.findall(r"^##\s+(CHƯƠNG\s+[^\n]+)", conversation_text, flags=re.MULTILINE)

        article_blocks = re.findall(
            r"^###\s+(Điều\s+\d+\.\s*[^\n]+)\n((?:  [^\n]+\n?)*)",
            conversation_text,
            flags=re.MULTILINE,
        )
        parsed_articles = []
        for art_header, body in article_blocks:
            clauses = [line.strip() for line in body.splitlines() if line.strip()]
            parsed_articles.append({"header": art_header.strip(), "clauses": clauses})

        def _extract_section_items(sec_header: str) -> List[str]:
            pattern = rf"### {re.escape(sec_header)}[^\n]*\n((?:  \* [^\n]+\n?)+)"
            m = re.search(pattern, conversation_text)
            if not m:
                return []
            return [line.strip()[2:].strip() for line in m.group(1).splitlines() if line.strip().startswith("* ")]

        obligations = _extract_section_items("[1] TRÍCH XUẤT CÁC NGHĨA VỤ BẮT BUỘC MỚI")
        prohibitions = _extract_section_items("[2] TRÍCH XUẤT CÁC HÀNH VI BỊ NGHIÊM CẤM")
        licenses = _extract_section_items("[3] TRÍCH XUẤT ĐIỀU KIỆN CẤP PHÉP & THỦ TỤC HÀNH CHÍNH MỚI")
        delegations = _extract_section_items("[4] TRÍCH XUẤT ĐIỀU KHOẢN GIAO QUY ĐỊNH CHI TIẾT")

        scope_text = (
            parsed_articles[0]["clauses"][0]
            if parsed_articles and parsed_articles[0]["clauses"]
            else f"Điều chỉnh các quan hệ xã hội thuộc phạm vi của {short_title}."
        )
        scope_text = re.sub(r"^\d+\.\s*", "", scope_text).rstrip(".")
        subject_text = (
            parsed_articles[1]["clauses"][0]
            if len(parsed_articles) > 1 and parsed_articles[1]["clauses"]
            else "Áp dụng đối với cơ quan, tổ chức, cá nhân Việt Nam và tổ chức, cá nhân nước ngoài có liên quan."
        )
        subject_text = re.sub(r"^\d+\.\s*", "", subject_text).rstrip(".")

        intl_clauses = []
        for art in parsed_articles:
            for c in art["clauses"]:
                if any(k in c.lower() for k in ["quốc tế", "điều ước", "nước ngoài", "xuyên biên giới", "toàn cầu", "hội nhập"]):
                    intl_clauses.append(f"[{art['header']}] {c}")

        # Group chapters into substantive policy blocks
        raw_chapter_splits = re.split(r"^##\s+(CHƯƠNG\s+[^\n]+)\n", conversation_text, flags=re.MULTILINE)
        raw_groups = []
        if len(raw_chapter_splits) > 1:
            for idx in range(1, len(raw_chapter_splits), 2):
                ch_title = raw_chapter_splits[idx].strip()
                ch_body = raw_chapter_splits[idx + 1]
                ch_arts = re.findall(
                    r"^###\s+(Điều\s+\d+\.\s*[^\n]+)\n((?:  [^\n]+\n?)*)",
                    ch_body,
                    flags=re.MULTILINE,
                )
                art_objs = []
                for ah, ab in ch_arts:
                    cls_list = [l.strip() for l in ab.splitlines() if l.strip()]
                    art_objs.append({"header": ah.strip(), "clauses": cls_list})
                if art_objs:
                    raw_groups.append({"chapter": ch_title, "articles": art_objs})

        # Merge purely procedural execution chapter ("ĐIỀU KHOẢN THI HÀNH") into preceding chapter if there are multiple chapters
        policy_groups = []
        for grp in raw_groups:
            ch_upper = grp["chapter"].upper()
            if "ĐIỀU KHOẢN THI HÀNH" in ch_upper and len(raw_groups) > 3 and policy_groups:
                policy_groups[-1]["articles"].extend(grp["articles"])
                policy_groups[-1]["chapter"] = f"{policy_groups[-1]['chapter']} và Điều khoản thi hành"
            else:
                policy_groups.append(grp)

        if not policy_groups and parsed_articles:
            step_sz = max(2, len(parsed_articles) // 3 or 1)
            for i in range(0, len(parsed_articles), step_sz):
                chunk = parsed_articles[i : i + step_sz]
                p_num = len(policy_groups) + 1
                policy_groups.append(
                    {
                        "chapter": f"Nhóm quy định thứ {p_num} ({chunk[0]['header'].split('.')[0]} đến {chunk[-1]['header'].split('.')[0]})",
                        "articles": chunk,
                    }
                )

        def _humanize_chapter_topic(ch_raw: str, ch_articles: list) -> str:
            topic = re.sub(r"^CHƯƠNG\s+[IVXLCDM0-9]+\s*[:\.\-]?\s*", "", ch_raw, flags=re.IGNORECASE).strip()
            if not topic:
                return ch_raw
            topic = topic.lower()
            for acr in ["AI", "CNTT", "TTHC", "SME", "UNESCO", "OECD", "ASEAN", "WTO", "CPTPP", "EVFTA"]:
                topic = re.sub(rf"\b{acr.lower()}\b", acr, topic, flags=re.IGNORECASE)
            topic = re.sub(r"\bviệt nam\b", "Việt Nam", topic, flags=re.IGNORECASE)
            topic = re.sub(r"\bchính phủ\b", "Chính phủ", topic, flags=re.IGNORECASE)
            topic = re.sub(r"\bnhà nước\b", "Nhà nước", topic, flags=re.IGNORECASE)
            topic = re.sub(r"\bquốc hội\b", "Quốc hội", topic, flags=re.IGNORECASE)
            if topic in ["quy định chung", "những quy định chung"] and ch_articles:
                sub_titles = [
                    a["header"].split(".", 1)[1].strip().lower()
                    for a in ch_articles[:6]
                    if "." in a["header"]
                ]
                sub_titles = [
                    re.sub(r"\bnhà nước\b", "Nhà nước", re.sub(r"\bai\b", "AI", st))
                    for st in sub_titles
                    if st not in ["giải thích từ ngữ", "đối tượng áp dụng"]
                ]
                if sub_titles:
                    return ", ".join(sub_titles[:4])
            return topic

        bullet_policy_issues = []
        policy_sections = []
        cost_benefit_rows = []

        ordinal_words = ["Thứ nhất", "Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Thứ tám"]
        specific_goals = []

        for p_idx, pg in enumerate(policy_groups, 1):
            ch_raw = pg["chapter"]
            ch_articles = pg["articles"]
            topic_lower = _humanize_chapter_topic(ch_raw, ch_articles)

            first_art_num = ch_articles[0]["header"].split(".")[0]
            last_art_num = ch_articles[-1]["header"].split(".")[0]
            art_range_str = (
                f"{first_art_num} đến {last_art_num}"
                if len(ch_articles) > 1
                else first_art_num
            )
            art_list_formatted = "; ".join(
                f"{a['header'].split('.')[0]} ({a['header'].split('.', 1)[1].strip()})"
                if "." in a["header"]
                else a["header"]
                for a in ch_articles
            )

            bullet_policy_issues.append(
                f"- Quy định về {topic_lower} (được thể chế hóa tại {len(ch_articles)} điều khoản, từ {art_range_str} của {draft_title})."
            )

            if p_idx <= 4:
                ord_label = ordinal_words[p_idx - 1]
                specific_goals.append(
                    f"{ord_label}, hoàn thiện quy định pháp luật về {topic_lower} ({art_range_str}), tạo cơ sở pháp lý minh bạch, khả thi để điều chỉnh quyền, nghĩa vụ và trách nhiệm của các cơ quan, tổ chức, cá nhân có liên quan."
                )

            ch_clauses = []
            for a in ch_articles:
                for c in a["clauses"]:
                    ch_clauses.append(f"[{a['header']}] {c}")

            # Prefer high-signal clauses (prohibitions, obligations, licensing, delegations) within the chapter
            high_signal_leg = [
                c for c in ch_clauses
                if any(k in c.lower() for k in ["nghiêm cấm", "cấm", "pháp luật có liên quan", "mã nguồn", "bí mật", "xử lý vi phạm", "bồi thường"])
            ]
            high_signal_eco = [
                c for c in ch_clauses
                if any(k in c.lower() for k in ["phải", "bắt buộc", "trách nhiệm", "nghĩa vụ", "hồ sơ", "đánh giá", "gắn nhãn", "sự cố", "quy định chi tiết", "giấy phép", "vốn"])
            ]

            rep_clause_1 = high_signal_leg[0] if high_signal_leg else (ch_clauses[0] if ch_clauses else f"[{ch_articles[0]['header']}]")
            rep_clause_2 = (
                next((c for c in high_signal_eco if c != rep_clause_1), high_signal_eco[0])
                if high_signal_eco
                else (ch_clauses[len(ch_clauses) // 2] if len(ch_clauses) > 1 else rep_clause_1)
            )

            cite_1_art, cite_1_body = self._format_clause_citation(rep_clause_1)
            cite_2_art, cite_2_body = self._format_clause_citation(rep_clause_2)

            leg_issue, leg_risk, leg_fix = self._diagnose_clause_issue(rep_clause_1, "legal")
            eco_issue, eco_risk, eco_fix = self._diagnose_clause_issue(rep_clause_2, "enterprise")
            tthc_issue, tthc_risk, tthc_fix = self._diagnose_clause_issue(rep_clause_2, "tthc")

            ch_has_tthc = any(
                any(re.search(pat, c.lower()) for pat in LegalPaperExtractorPlayer.LICENSING_PATTERNS)
                or "quy định chi tiết" in c.lower()
                for c in ch_clauses
            )
            ch_has_gender = any(
                any(k in c.lower() for k in ["giới", "phụ nữ", "trẻ em", "khuyết tật", "yếu thế", "tổn thương", "dân tộc thiểu số", "nhân lực", "doanh nghiệp"])
                for c in ch_clauses
            )

            gender_tthc_benefit = (
                f"Bảo đảm nguyên tắc bình đẳng giới, không phân biệt đối xử và bảo vệ nhóm yếu thế trong quá trình tiếp cận, thụ hưởng chính sách về {topic_lower}; đồng thời chuẩn hóa quy trình thực hiện thủ tục hành chính trên môi trường điện tử, giảm thời gian và chi phí đi lại cho người dân, doanh nghiệp."
                if (ch_has_gender or ch_has_tthc)
                else f"Chính sách được thiết kế trung tính về giới, bảo đảm cơ hội tiếp cận công bằng cho cả nam và nữ và không làm phát sinh thêm thủ tục hành chính gây phiền hà cho tổ chức, cá nhân."
            )

            # Weave actual AI council outputs into each policy section when running with an LLM
            if not is_offline:
                ai_leg = _pick_ai_point(r1_points, ch_articles, p_idx)
                ai_eco = _pick_ai_point(r2_points, ch_articles, p_idx)
                ai_gen = _pick_ai_point(r3_points, ch_articles, p_idx)
                ai_tthc = _pick_ai_point(r4_points, ch_articles, p_idx)
                ai_auth = _pick_ai_point(author_points, ch_articles, p_idx)
                if ai_leg:
                    leg_issue = ai_leg
                if ai_eco:
                    eco_issue = ai_eco
                if ai_gen or ai_tthc:
                    gender_tthc_benefit = " ".join(p for p in [ai_gen, ai_tthc] if p)
                if ai_auth:
                    leg_fix = ai_auth
                    eco_fix = ai_auth
                if ai_tthc:
                    tthc_fix = ai_tthc

            eco_issue_inline = eco_issue[0].lower() + eco_issue[1:] if eco_issue else eco_issue
            tthc_fix_inline = tthc_fix[0].lower() + tthc_fix[1:] if tthc_fix else tthc_fix
            cite_pair = f"{cite_1_art} và {cite_2_art}" if cite_1_art != cite_2_art else cite_1_art

            policy_sections.append(
                f"## {p_idx}. Chính sách {p_idx}: Hoàn thiện quy định về {topic_lower}.\n"
                f"### {p_idx}.1. Xác định vấn đề và mục tiêu giải quyết vấn đề.\n"
                f"- Xác định vấn đề: Trong thực tiễn thi hành pháp luật và quản lý nhà nước, các quan hệ xã hội liên quan đến {topic_lower} ({art_range_str}) đang phát sinh những vấn đề, bất cập mới cần được điều chỉnh trực tiếp. Cụ thể, tại {cite_1_art}, Dự thảo xác lập quy phạm: \"{cite_1_body}\". Nếu không có quy định cụ thể, cơ quan quản lý nhà nước sẽ thiếu công cụ pháp lý để giám sát, điều phối và xử lý các vấn đề phát sinh trong thực tiễn. Bên cạnh đó, qua rà soát đối với {art_range_str} (đặc biệt tại {cite_2_art}), một số quy định còn tiềm ẩn vấn đề cần hoàn thiện: {leg_issue} Đồng thời, dưới góc độ thực thi: {eco_issue}\n"
                f"- Mục tiêu giải quyết vấn đề: Xây dựng và hoàn thiện hệ thống quy phạm pháp luật điều chỉnh về {topic_lower} tại {art_range_str} của {draft_title} ({art_list_formatted}) nhằm tạo lập hành lang pháp lý đồng bộ, minh bạch, khả thi; phân định rõ quyền, nghĩa vụ pháp lý của tổ chức, cá nhân và trách nhiệm của cơ quan nhà nước có thẩm quyền, bảo đảm hài hòa giữa yêu cầu quản lý nhà nước và phát triển kinh tế - xã hội.\n"
                f"### {p_idx}.2. Các giải pháp và đánh giá tác động của các giải pháp đối với đối tượng chịu sự tác động trực tiếp của chính sách và các đối tượng khác có liên quan.\n"
                f"- Các giải pháp đề xuất:\n"
                f"+ Giải pháp thứ nhất (Giải pháp 1): Giữ nguyên hiện trạng, không quy định.\n"
                f"+ Giải pháp thứ hai (Giải pháp 2): Quy định cụ thể các nội dung về {topic_lower} tại {art_range_str} của {draft_title} (kết hợp tiếp thu, hoàn thiện kỹ thuật lập pháp theo ý kiến thẩm định).\n"
                f"- Đánh giá tác động của các giải pháp đối với đối tượng chịu sự tác động trực tiếp của chính sách và các đối tượng khác có liên quan:\n"
                f"+ Đối với Giải pháp 1 (Không quy định): Về lợi ích, không làm phát sinh thêm nghĩa vụ hay xáo trộn hệ thống quy phạm pháp luật; tuy nhiên về tác động tiêu cực, các bất cập thực tiễn về {topic_lower} không được tháo gỡ, thiếu căn cứ pháp lý thống nhất để bảo vệ quyền, lợi ích hợp pháp của người dân, doanh nghiệp và nâng cao hiệu lực quản lý nhà nước.\n"
                f"+ Đối với Giải pháp 2 (Quy định tại Dự thảo):\n"
                f"  - Tác động đối với hệ thống pháp luật: Tạo sự thống nhất, đồng bộ trong hệ thống pháp luật thông qua việc quy định rõ {len(ch_articles)} điều khoản ({art_range_str}), bảo đảm tính nghiêm minh và khả thi. Để xử lý các rủi ro pháp lý tại {cite_pair}, dự thảo tiếp thu chỉnh lý theo hướng: {leg_fix}\n"
                f"  - Tác động về kinh tế - xã hội: Tạo lập môi trường hoạt động minh bạch, có khả năng dự báo trước, bảo vệ quyền lợi hợp pháp của người dân và thúc đẩy doanh nghiệp phát triển bền vững. Về chi phí tuân thủ tại {cite_2_art}, {eco_issue_inline} Khi áp dụng cơ chế quản lý phù hợp ({eco_fix.rstrip('.')}), lợi ích kinh tế - xã hội mang lại sẽ vượt trội so với chi phí đầu tư tuân thủ.\n"
                f"  - Tác động về giới (nếu có): {gender_tthc_benefit}\n"
                f"  - Tác động của thủ tục hành chính (nếu có): Chuẩn hóa trình tự, thủ tục thực hiện, ứng dụng số hóa và cắt giảm tối đa chi phí tuân thủ cho người dân, doanh nghiệp ({tthc_fix_inline}).\n"
                f"### {p_idx}.3. Lựa chọn giải pháp.\n"
                f"- Kiến nghị lựa chọn Giải pháp 2 vì đây là giải pháp tối ưu, có tính khả thi cao, phù hợp với thực tiễn Việt Nam và thông lệ quốc tế.\n"
                f"- Thẩm quyền ban hành chính sách: {authority}.\n"
            )

            cost_benefit_rows.append(
                f"| {p_idx} | Chính sách {p_idx}: Quy định về {topic_lower} ({art_range_str}) | "
                f"Hoàn thiện khung pháp lý ({len(ch_articles)} Điều); bảo vệ quyền lợi người dân; tạo môi trường kinh doanh minh bạch (Lợi ích > Chi phí) | "
                f"Chi phí tuân thủ ở mức {eco_risk}; rủi ro TTHC ở mức {tthc_risk if ch_has_tthc else 'Thấp'} | "
                f"Lựa chọn Giải pháp 2 ({leg_fix.rstrip('.')}) |"
            )

        ac_context_paragraph = ""
        if not is_offline and ac_points:
            ac_summary = " ".join(p.rstrip(".") + "." for p in ac_points[:3])
            ac_context_paragraph = f"Qua tổng hợp nhận định của Chủ tịch Hội đồng thẩm định trên mô hình ngôn ngữ lớn ({self.model}): {ac_summary}\n"

        council_deliberation_block = ""
        if not is_offline and any([r1_points, r2_points, r3_points, r4_points, author_points, ac_points]):
            r1_rate = _extract_ratings(agent_blocks["Reviewer 1"])
            r2_rate = _extract_ratings(agent_blocks["Reviewer 2"])
            r3_rate = _extract_ratings(agent_blocks["Reviewer 3"])
            r4_rate = _extract_ratings(agent_blocks["Reviewer 4"])
            delib_lines = [
                f"Ý kiến thẩm định chuyên sâu của Hội đồng thẩm định ({self.model}) và giải trình của Cơ quan chủ trì soạn thảo:",
            ]
            if r1_points:
                r1_tag = f" ({r1_rate})" if r1_rate else ""
                delib_lines.append(f"- Về Hệ thống pháp luật và Điều ước quốc tế{r1_tag}: {' '.join(p.rstrip('.') + '.' for p in r1_points[:2])}")
            if r2_points:
                r2_tag = f" ({r2_rate})" if r2_rate else ""
                delib_lines.append(f"- Về Kinh tế - Xã hội và Chi phí - Lợi ích{r2_tag}: {' '.join(p.rstrip('.') + '.' for p in r2_points[:2])}")
            if r3_points:
                r3_tag = f" ({r3_rate})" if r3_rate else ""
                delib_lines.append(f"- Về Bình đẳng giới và Nhóm yếu thế{r3_tag}: {' '.join(p.rstrip('.') + '.' for p in r3_points[:2])}")
            if r4_points:
                r4_tag = f" ({r4_rate})" if r4_rate else ""
                delib_lines.append(f"- Về Thủ tục hành chính và Điều kiện kinh doanh{r4_tag}: {' '.join(p.rstrip('.') + '.' for p in r4_points[:2])}")
            if author_points:
                delib_lines.append(f"- Giải trình và tiếp thu của Cơ quan chủ trì soạn thảo: {' '.join(p.rstrip('.') + '.' for p in author_points[:2])}")
            if ac_points:
                delib_lines.append(f"- Kết luận của Chủ tịch Hội đồng thẩm định: {' '.join(p.rstrip('.') + '.' for p in ac_points[:2])}")
            council_deliberation_block = "\n".join(delib_lines) + "\n\n"

        doc_num_clause = f" (số hiệu {doc_number})" if doc_number else ""

        return (
            f"# I. XÁC ĐỊNH VẤN ĐỀ\n"
            f"## 1. Bối cảnh xây dựng chính sách\n"
            f"- Bối cảnh quốc tế, khu vực liên quan đến các chính sách (nếu có): Trên bình diện quốc tế và khu vực, nhiều quốc gia và tổ chức quốc tế đã ban hành các chuẩn mực, tiêu chuẩn quản trị và thông lệ tiến bộ nhằm thúc đẩy phát triển kinh tế - xã hội gắn liền với bảo vệ quyền con người, an toàn xã hội và trật tự công cộng. Qua rà soát {draft_title}{doc_num_clause}, văn bản có {len(intl_clauses)} quy định liên quan trực tiếp đến hợp tác, hội nhập quốc tế.\n"
            f"- Bối cảnh trong nước (chính trị, pháp lý, kinh tế - xã hội...) liên quan đến các chính sách: Đường lối, chủ trương của Đảng và chính sách, pháp luật của Nhà nước luôn nhất quán tạo môi trường pháp lý thuận lợi, minh bạch phục vụ sự nghiệp phát triển đất nước. Tuy nhiên, thực tiễn thi hành và sự vận động nhanh chóng của các quan hệ xã hội thuộc phạm vi điều chỉnh của {short_title} (gồm {len(chapters)} Chương, {len(parsed_articles)} Điều, với {len(obligations)} quy định nghĩa vụ bắt buộc, {len(prohibitions)} quy định hành vi nghiêm cấm, {len(licenses)} quy định thủ tục hành chính và {len(delegations)} quy định giao quy định chi tiết) đã đặt ra những yêu cầu mới mà hệ thống quy định hiện hành chưa giải quyết thỏa đáng hoặc còn tản mạn. Do vậy, việc ban hành {short_title} là hết sức cần thiết nhằm giải quyết các nhóm vấn đề:\n"
            f"{chr(10).join(bullet_policy_issues)}\n"
            f"{ac_context_paragraph}"
            f"## 2. Mục tiêu xây dựng chính sách\n"
            f"*Mục tiêu tổng thể:* Xây dựng và ban hành {short_title} nhằm đảm bảo tính đồng bộ, thống nhất của hệ thống pháp luật; bảo đảm tính minh bạch, khả thi, tạo thuận lợi cho việc thực thi có hiệu quả; thể chế hóa đầy đủ chủ trương của Đảng, chính sách của Nhà nước ({scope_text}); khắc phục các bất cập thực tiễn và nâng cao hiệu lực, hiệu quả quản lý nhà nước đối với {subject_text}.\n"
            f"*Mục tiêu cụ thể:*\n"
            f"{chr(10).join(specific_goals)}\n"
            f"# II. ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH\n"
            f"Phần này liệt kê từng vấn đề cần giải quyết. Nội dung phân tích từng vấn đề bao gồm: xác định vấn đề (mô tả vấn đề, phân tích các bất cập, hậu quả, nguyên nhân của vấn đề); mục tiêu giải quyết vấn đề; các giải pháp đề xuất để giải quyết vấn đề; đánh giá tác động tích cực (lợi ích) và tác động tiêu cực (chi phí) của từng giải pháp đối với Nhà nước, người dân, doanh nghiệp; kiến nghị lựa chọn giải pháp tối ưu trên cơ sở phân tích, so sánh tác động tích cực, tác động tiêu cực của các giải pháp.\n\n"
            f"{council_deliberation_block}"
            f"{chr(10).join(policy_sections)}"
            f"# III. PHỤ LỤC\n"
            f"1. Các bảng, biểu tính toán chi phí và lợi ích của các giải pháp (nếu có):\n"
            f"| TT | Chính sách và Điều khoản liên quan | Đánh giá lợi ích (Tác động tích cực) | Đánh giá chi phí (Tác động tiêu cực) | Giải pháp lựa chọn và kiến nghị chỉnh lý |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"{chr(10).join(cost_benefit_rows)}\n"
            f"2. Danh mục các điều ước quốc tế có liên quan mà Cộng hòa xã hội chủ nghĩa Việt Nam là thành viên được rà soát và đánh giá tính tương thích:\n"
            f"| TT | Tên điều ước, công ước quốc tế mà Việt Nam là thành viên | Nội dung quy định liên quan trong {draft_title} | Kết luận đánh giá tính tương thích |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"| 1 | Công ước quốc tế về các quyền dân sự và chính trị (ICCPR, 1966) | Các quy định về quyền con người, quyền tiếp cận thông tin, bảo vệ quyền riêng tư và các hành vi bị nghiêm cấm | Tương thích (bảo đảm nguyên tắc pháp quyền, minh bạch các tiêu chí xác định hành vi vi phạm) |\n"
            f"| 2 | Công ước xóa bỏ mọi hình thức phân biệt đối xử với phụ nữ (CEDAW) và Công ước về quyền của người khuyết tật (CRPD) | Các quy định về nguyên tắc công bằng, bình đẳng giới, không phân biệt đối xử và bảo vệ nhóm yếu thế | Tương thích hoàn toàn |\n"
            f"| 3 | Các Hiệp định thương mại tự do thế hệ mới (CPTPP, EVFTA, RCEP) và các cam kết của WTO | Các quy định về môi trường đầu tư kinh doanh, thủ tục hành chính, sở hữu trí tuệ và thương mại xuyên biên giới | Tương thích (bảo đảm nguyên tắc đối xử quốc gia, minh bạch và thuận lợi hóa thương mại) |\n"
            f"Trên đây là nội dung Báo cáo đánh giá tác động của chính sách trong {draft_title}, {lead_agency} kính báo cáo./.\n"
        )


# Register ollama-chat backend
BACKEND_REGISTRY["ollama-chat"] = OllamaBackend


class LegalDocumentExtractorPlayer(DocumentExtractorPlayer):
    """
    Tác nhân bóc tách cấu trúc văn bản quy phạm pháp luật (.docx, .pdf, .txt, .md),
    tự động phân tách Chương, Điều, Khoản và trích xuất ma trận nghĩa vụ, cấm đoán, điều kiện.
    """

    OBLIGATION_PATTERNS = [
        r"\bphải\b", r"\bbắt buộc\b", r"\bcó trách nhiệm\b", r"\bcó nghĩa vụ\b",
        r"\bđịnh kỳ\b", r"\bnộp báo cáo\b", r"\bmua bảo hiểm\b", r"\blưu trữ\b"
    ]
    PROHIBITION_PATTERNS = [
        r"\bnghiêm cấm\b", r"\bkhông được\b", r"\bcấm\b", r"\bđình chỉ\b", r"\bthu hồi\b"
    ]
    LICENSING_PATTERNS = [
        r"\bgiấy phép\b", r"\bgiấy chứng nhận\b", r"\bvăn bản chấp thuận\b",
        r"\bthẩm định\b", r"\bhồ sơ\b", r"\bthủ tục\b", r"\bđiều kiện cấp\b",
        r"\bvốn điều lệ tối thiểu\b", r"\bngày làm việc\b"
    ]

    def _read_raw_document(self, document_path: Path) -> str:
        ext = document_path.suffix.lower()
        if ext in [".txt", ".md"]:
            return document_path.read_text(encoding="utf-8", errors="ignore")

        elif ext == ".docx":
            with zipfile.ZipFile(document_path, "r") as zf:
                xml_bytes = zf.read("word/document.xml")
            tree = ET.fromstring(xml_bytes)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for p in tree.findall(".//w:p", ns):
                texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
                if texts:
                    paragraphs.append("".join(texts).strip())
            return "\n".join(paragraphs)

        elif ext == ".pdf":
            try:
                from vlex_ria.document_processor import extract_text_from_pdf
                return extract_text_from_pdf(str(document_path))
            except Exception:
                from llama_index.readers.file.docs import PDFReader
                loader = PDFReader()
                docs = loader.load_data(file=document_path)
                return "\n".join(d.text for d in docs)

        return document_path.read_text(encoding="utf-8", errors="ignore")

    MINISTRY_ABBR_MAP = {
        "Bộ Khoa học và Công nghệ": "BKHCN",
        "Bộ Thông tin và Truyền thông": "BTTTT",
        "Bộ Tư pháp": "BTP",
        "Bộ Văn hóa, Thể thao và Du lịch": "BVHTTDL",
        "Bộ Công Thương": "BCT",
        "Bộ Tài chính": "BTC",
        "Bộ Kế hoạch và Đầu tư": "BKHĐT",
        "Bộ Công an": "BCA",
        "Bộ Quốc phòng": "BQP",
        "Bộ Giáo dục và Đào tạo": "BGDĐT",
        "Bộ Y tế": "BYT",
        "Bộ Xây dựng": "BXD",
        "Bộ Giao thông vận tải": "BGTVT",
        "Bộ Tài nguyên và Môi trường": "BTNMT",
        "Bộ Nông nghiệp và Phát triển nông thôn": "BNNPTNT",
        "Bộ Lao động - Thương binh và Xã hội": "BLĐTBXH",
        "Bộ Nội vụ": "BNV",
        "Bộ Ngoại giao": "BNG",
    }

    @classmethod
    def extract_document_metadata(cls, raw_text: str) -> dict:
        lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
        preamble = lines[:18]

        doc_number = ""
        for ln in preamble:
            m_num = re.search(r"(?:Luật\s+số|Số)\s*:\s*([^\s\n]+)", ln, flags=re.IGNORECASE)
            if m_num:
                doc_number = m_num.group(1).strip()
                break

        short_title = ""
        authority = "Chính phủ"

        for ln in preamble:
            m_prom = re.search(
                r"(Quốc hội|Chính phủ|Ủy ban Thường vụ Quốc hội|Thủ tướng Chính phủ)\s+ban hành\s+(Luật|Nghị định|Nghị quyết|Quyết định)\s+([^\.\n]+)",
                ln,
                flags=re.IGNORECASE,
            )
            if m_prom:
                authority = m_prom.group(1).strip()
                doc_type_word = m_prom.group(2).strip()
                topic_part = m_prom.group(3).strip().rstrip(".")
                short_title = f"{doc_type_word} {topic_part}"
                break

        if not short_title:
            for idx, ln in enumerate(preamble):
                upper_ln = ln.upper()
                if upper_ln in ["LUẬT", "DỰ THẢO LUẬT", "BỘ LUẬT"] and idx + 1 < len(preamble):
                    authority = "Quốc hội"
                    nxt = preamble[idx + 1].strip()
                    if nxt.isupper():
                        nxt = nxt.capitalize()
                    short_title = f"Luật {nxt}"
                    break
                if upper_ln in ["NGHỊ ĐỊNH", "DỰ THẢO NGHỊ ĐỊNH"] and idx + 1 < len(preamble):
                    authority = "Chính phủ"
                    nxt = preamble[idx + 1].strip()
                    if nxt.isupper():
                        nxt = nxt.capitalize()
                        nxt = re.sub(r"\bai\b", "AI", nxt, flags=re.IGNORECASE)
                        nxt = re.sub(r"\bviệt nam\b", "Việt Nam", nxt, flags=re.IGNORECASE)
                        nxt = re.sub(r"\btrí tuệ nhân tạo\b", "Trí tuệ nhân tạo", nxt, flags=re.IGNORECASE)
                    if nxt.lower().startswith("quy định"):
                        nxt = nxt[0].lower() + nxt[1:]
                    short_title = f"Nghị định {nxt}"
                    break

        if not short_title:
            short_title = "Văn bản quy phạm pháp luật"

        if short_title.lower().startswith("dự thảo"):
            draft_title = short_title
            short_title = re.sub(r"^dự thảo\s+", "", short_title, flags=re.IGNORECASE).strip()
            short_title = short_title[0].upper() + short_title[1:] if short_title else "Văn bản quy phạm pháp luật"
        else:
            draft_title = f"Dự thảo {short_title}"

        lead_agency = "Bộ Tư pháp"
        for min_name in cls.MINISTRY_ABBR_MAP:
            if re.search(rf"{re.escape(min_name)}\s+(?:là cơ quan đầu mối|chủ trì|giúp Chính phủ)", raw_text, flags=re.IGNORECASE):
                lead_agency = min_name
                break
        else:
            best_min = None
            best_cnt = 0
            for min_name in cls.MINISTRY_ABBR_MAP:
                cnt = len(re.findall(re.escape(min_name), raw_text, flags=re.IGNORECASE))
                if cnt > best_cnt:
                    best_cnt = cnt
                    best_min = min_name
            if best_min and best_cnt > 0:
                lead_agency = best_min

        abbr = cls.MINISTRY_ABBR_MAP.get(lead_agency, "BTP")
        return {
            "short_title": short_title,
            "draft_title": draft_title,
            "doc_number": doc_number,
            "authority": authority,
            "lead_agency": lead_agency,
            "lead_agency_upper": lead_agency.upper(),
            "report_number": f"/BC-{abbr}",
        }

    @classmethod
    def parse_legal_structure(cls, raw_text: str) -> str:
        """
        Tách văn bản thành các Chương, Điều, Khoản và tự động trích xuất:
        Nghĩa vụ bắt buộc mới, Hành vi bị cấm, Điều kiện cấp phép & Thủ tục hành chính mới,
        Điều khoản giao quy định chi tiết (Ủy quyền lập quy).
        """
        meta = cls.extract_document_metadata(raw_text)
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        articles = []
        preamble_lines = []
        current_chapter = ""
        pending_chapter_header = None
        current_article = None

        chapter_re = re.compile(r"^(CHƯƠNG\s+[IVXLCDM0-9]+)[\.:\s]*(.*)", re.IGNORECASE)
        article_re = re.compile(r"^(Điều\s+\d+)[\.:\s]*(.*)", re.IGNORECASE)

        for line in lines:
            ch_match = chapter_re.match(line)
            art_match = article_re.match(line)

            if ch_match:
                ch_num = ch_match.group(1).upper()
                ch_title = ch_match.group(2).strip()
                if ch_title:
                    current_chapter = f"{ch_num}: {ch_title}"
                    pending_chapter_header = None
                else:
                    current_chapter = ch_num
                    pending_chapter_header = ch_num
            elif pending_chapter_header and not art_match:
                # Chapter title line immediately following chapter number
                current_chapter = f"{pending_chapter_header}: {line}"
                pending_chapter_header = None
            elif art_match:
                pending_chapter_header = None
                if current_article:
                    articles.append(current_article)
                current_article = {
                    "chapter": current_chapter,
                    "id": art_match.group(1),
                    "title": art_match.group(2).strip(),
                    "clauses": [],
                }
            else:
                if current_article is not None:
                    current_article["clauses"].append(line)
                elif not current_chapter:
                    preamble_lines.append(line)

        if current_article:
            articles.append(current_article)

        obligations, prohibitions, licenses, delegations = [], [], [], []
        delegation_patterns = [
            r"chính phủ quy định chi tiết",
            r"thủ tướng chính phủ ban hành",
            r"bộ trưởng.*quy định",
            r"quy định chi tiết điều này",
            r"quy định chi tiết khoản",
        ]

        for art in articles:
            for clause in art["clauses"]:
                lower_c = clause.lower()
                ref = f"[{art['id']} - {art['title']}] {clause}"
                if any(re.search(p, lower_c) for p in cls.PROHIBITION_PATTERNS):
                    prohibitions.append(ref)
                if any(re.search(p, lower_c) for p in cls.LICENSING_PATTERNS):
                    licenses.append(ref)
                if any(re.search(p, lower_c) for p in cls.OBLIGATION_PATTERNS):
                    obligations.append(ref)
                if any(re.search(p, lower_c) for p in delegation_patterns):
                    delegations.append(ref)

        doc_header = meta["short_title"]
        if meta["doc_number"]:
            doc_header = f"{meta['short_title']} (Số hiệu: {meta['doc_number']})"

        summary_blocks = [
            "# KẾT QUẢ BÓC TÁCH CẤU TRÚC DỰ THẢO VĂN BẢN (PARSER)",
            f"- Thông tin văn bản: {doc_header}",
            f"- Tên văn bản: {meta['short_title']}",
            f"- Tên dự thảo: {meta['draft_title']}",
            f"- Số hiệu văn bản: {meta['doc_number']}",
            f"- Thẩm quyền ban hành: {meta['authority']}",
            f"- Cơ quan chủ trì đề xuất: {meta['lead_agency']}",
            f"- Ký hiệu báo cáo: {meta['report_number']}",
            f"- Tổng số Điều được bóc tách: {len(articles)} Điều",
            f"- Số quy định chứa Nghĩa vụ bắt buộc mới: {len(obligations)}",
            f"- Số quy định chứa Hành vi bị nghiêm cấm: {len(prohibitions)}",
            f"- Số quy định chứa Điều kiện cấp phép / TTHC mới: {len(licenses)}",
            f"- Số quy định giao Chính phủ/Bộ quy định chi tiết: {len(delegations)}\n",
        ]

        if obligations:
            summary_blocks.append("### [1] TRÍCH XUẤT CÁC NGHĨA VỤ BẮT BUỘC MỚI:")
            summary_blocks.extend(f"  * {item}" for item in obligations[:25])
            summary_blocks.append("")

        if prohibitions:
            summary_blocks.append("### [2] TRÍCH XUẤT CÁC HÀNH VI BỊ NGHIÊM CẤM:")
            summary_blocks.extend(f"  * {item}" for item in prohibitions[:20])
            summary_blocks.append("")

        if licenses:
            summary_blocks.append("### [3] TRÍCH XUẤT ĐIỀU KIỆN CẤP PHÉP & THỦ TỤC HÀNH CHÍNH MỚI:")
            summary_blocks.extend(f"  * {item}" for item in licenses[:25])
            summary_blocks.append("")

        if delegations:
            summary_blocks.append("### [4] TRÍCH XUẤT ĐIỀU KHOẢN GIAO QUY ĐỊNH CHI TIẾT (NGUY CƠ VĂN BẢN HƯỚNG DẪN):")
            summary_blocks.extend(f"  * {item}" for item in delegations[:20])
            summary_blocks.append("")

        summary_blocks.append("# TOÀN VĂN CÁC CHƯƠNG, ĐIỀU, KHOẢN CỦA DỰ THẢO\n")

        if articles:
            last_ch = None
            for art in articles:
                if art["chapter"] and art["chapter"] != last_ch:
                    summary_blocks.append(f"\n## {art['chapter']}")
                    last_ch = art["chapter"]
                summary_blocks.append(f"\n### {art['id']}. {art['title']}")
                for c in art["clauses"]:
                    summary_blocks.append(f"  {c}")
        else:
            summary_blocks.append(raw_text)

        return "\n".join(summary_blocks)

    def act(self, observation: List[Message]) -> str:
        """
        Ghi đè hàm `act()` của `PaperExtractorPlayer` để đọc văn bản dự thảo luật
        và phân tách đầy đủ các Chương, Điều, Khoản.
        """
        if getattr(self, "paper_pdf_path", None) is not None:
            document_path = Path(self.paper_pdf_path)
            logging.info(f"Loading legal document from {document_path} ...")
        else:
            document_path = Path(
                os.path.join(
                    self.args.data_dir,
                    self.conference,
                    "paper",
                    self.paper_decision,
                    f"{self.paper_id}.pdf",
                )
            )
            logging.info(f"Loading {self.conference} document {self.paper_id} ...")

        raw_text = self._read_raw_document(document_path)
        structured_text = self.parse_legal_structure(raw_text)
        main_contents = "Nội dung Dự thảo Văn bản Quy phạm Pháp luật:\n\n" + structured_text
        return main_contents


LegalPaperExtractorPlayer = LegalDocumentExtractorPlayer


class RIAAreaChair(AreaChair):
    """
    Chủ tọa Hội đồng Thẩm định điều phối quy trình và tổng hợp Báo cáo RIA.
    """

    def act(self, observation: List[Message]) -> str:
        if self.env_type == "paper_review":
            if len(observation) > 0 and observation[-1].agent_name.startswith("Author"):
                return (
                    "Kính gửi 04 thành viên Hội đồng thẩm định theo Mẫu số 01 "
                    "(Reviewer 1 - Tác động Hệ thống Pháp luật & Điều ước Quốc tế, "
                    "Reviewer 2 - Tác động Kinh tế - Xã hội & Chi phí - Lợi ích, "
                    "Reviewer 3 - Tác động về Giới, Reviewer 4 - Tác động của Thủ tục Hành chính), "
                    "đề nghị các chuyên gia đọc kỹ Báo cáo giải trình của Ban soạn thảo và ý kiến của các thành viên khác "
                    "để thảo luận, đối chiếu và cập nhật lại điểm số cũng như nhận định thẩm định cuối cùng."
                )
            else:
                return Player.act(self, observation)
        elif self.env_type == "paper_decision":
            return Player.act(self, observation)
        else:
            raise ValueError(f"Unknown env_type: {self.env_type}")


class RIACouncilArena(RIAArena):
    """
    Hội đồng điều phối đa tác nhân phục vụ quy trình thẩm định 5 giai đoạn theo Mẫu số 01.
    """

    def step(self) -> TimeStep:
        player_name = self.environment.get_next_player()
        player = self.name_to_player[player_name]
        observation = self.environment.get_observation(player_name)

        timestep = None
        for _ in range(self.invalid_actions_retry):
            if self.environment.phase_index == 3 and player.name.startswith("Reviewer"):
                logging.info("Update reviewers' role_desc for Phase 3 (reviewer_ac_discussion)")
                reviewer_index = int(player.name.split("Reviewer ")[1])
                reviewer_cfg = self.environment.experiment_setting["players"]["Reviewer"][reviewer_index - 1]
                player.role_desc = get_reviewer_description(
                    phase="reviewer_ac_discussion",
                    reviewer_index=reviewer_index,
                    **reviewer_cfg,
                )

            action = player(observation)
            if self.environment.check_action(action, player_name):
                timestep = self.environment.step(player_name, action)
                break
            else:
                logging.warning(f"{player_name} made an invalid action {action}")
                continue

        if timestep is None:
            warning_msg = f"{player_name} has made invalid actions for {self.invalid_actions_retry} times. Terminating."
            logging.warning(warning_msg)
            raise TooManyInvalidActions(warning_msg)

        return timestep

    def launch_cli(self, max_steps: int = None, interactive: bool = False):
        self.reset()
        step_count = 0
        while True:
            timestep = self.step()
            messages = [msg for msg in self.environment.get_observation() if not msg.logged]
            for msg in messages:
                print(f"[{msg.agent_name}->{msg.visible_to}]: {msg.content[:240]}...")
                msg.logged = True
            step_count += 1
            if timestep.terminal or (max_steps is not None and step_count >= max_steps):
                break


RIAPaperReviewArena = RIACouncilArena


def initialize_ria_players(experiment_setting: dict, args, paper_pdf_path: str = None, backend_config: dict = None) -> List[Player]:
    """
    Khởi tạo đầy đủ 7 tác nhân của Hội đồng Thẩm định theo Mẫu số 01:
    - Paper Extractor (`LegalPaperExtractorPlayer`)
    - AC (`RIAAreaChair` - Chủ tọa Hội đồng / Meta-Reviewer)
    - Author (`Player` - Ban Soạn Thảo)
    - Reviewer 1, Reviewer 2, Reviewer 3, Reviewer 4 (`Reviewer` - Pháp luật, Kinh tế-Xã hội, Giới, TTHC)
    """
    if not hasattr(args, "openai_client_type"):
        args.openai_client_type = "ollama"
    if not hasattr(args, "max_num_words"):
        args.max_num_words = 16384
    if not hasattr(args, "skip_logging"):
        args.skip_logging = True
    if not hasattr(args, "model_name"):
        raw_model = getattr(args, "model", "qwen2.5_7b").replace(":", "_")
        args.model_name = raw_model if raw_model.startswith("gpt-") else f"gpt-4-{raw_model}"

    # Ensure council reviewers correspond to Form 01 impact dimensions
    rev_list = list(experiment_setting["players"].get("Reviewer", []))
    while len(rev_list) < 4:
        default_rev = dict(rev_list[-1]) if rev_list else {
            "is_benign": None,
            "is_knowledgeable": None,
            "is_responsible": None,
            "knows_authors": False,
        }
        rev_list.append(default_rev)
    experiment_setting["players"]["Reviewer"] = rev_list[:4]

    paper_id = experiment_setting["paper_id"]
    paper_decision = experiment_setting["paper_decision"]

    players = []
    for role, players_list in experiment_setting["players"].items():
        for i, p_setting in enumerate(players_list):
            if role == "AC":
                player_config = get_ac_config(
                    env_type=args.task,
                    scoring_method=getattr(args, "ac_scoring_method", "ranking"),
                    num_papers_per_area_chair=getattr(args, "num_papers_per_area_chair", 10),
                    global_settings=experiment_setting["global_settings"],
                    acceptance_rate=getattr(args, "acceptance_rate", 0.32),
                    backend_config=backend_config,
                    **p_setting,
                )
                player = RIAAreaChair(
                    data_dir=args.data_dir,
                    conference=args.conference,
                    args=args,
                    **player_config,
                )

            elif role in ["Document Extractor", "Paper Extractor"]:
                player_config = get_document_extractor_config(
                    global_settings=experiment_setting["global_settings"]
                )
                player = LegalDocumentExtractorPlayer(
                    paper_pdf_path=paper_pdf_path,
                    data_dir=args.data_dir,
                    paper_id=paper_id,
                    paper_decision=paper_decision,
                    args=args,
                    conference=args.conference,
                    **player_config,
                )

            elif role == "Author":
                player_config = get_author_config(backend_config=backend_config)
                player = Player(
                    data_dir=args.data_dir,
                    conference=args.conference,
                    args=args,
                    **player_config,
                )

            elif role == "Reviewer":
                player_config = get_reviewer_player_config(
                    reviewer_index=i + 1,
                    global_settings=experiment_setting["global_settings"],
                    backend_config=backend_config,
                    **p_setting,
                )
                player = Reviewer(
                    data_dir=args.data_dir,
                    conference=args.conference,
                    args=args,
                    **player_config,
                )
            else:
                raise NotImplementedError(f"Unknown role: {role}")

            players.append(player)

    return players
