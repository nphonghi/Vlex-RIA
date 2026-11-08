import logging
import os
from pathlib import Path
from typing import List, Union

from llama_index.readers.file.docs import PDFReader

from vlex_ria.agent import Player
from vlex_ria.backends import IntelligenceBackend
from vlex_ria.config import BackendConfig
from vlex_ria.message import Message


class AreaChair(Player):
    def __init__(
        self,
        name: str,
        role_desc: str,
        env_type: str,
        backend: Union[BackendConfig, IntelligenceBackend],
        global_prompt: str = None,
        **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)
        self.env_type = env_type
        self.role_desc = role_desc

    def act(self, observation: List[Message]) -> str:
        if self.env_type in ["ria_review", "paper_review"]:
            if len(observation) > 0 and observation[-1].agent_name.startswith("Author"):
                return "Kính đề nghị các chuyên gia đánh giá cập nhật ý kiến phản biện dựa trên giải trình của cơ quan soạn thảo."
            return super().act(observation)
        elif self.env_type == "paper_decision":
            return super().act(observation)
        else:
            return super().act(observation)


class Reviewer(Player):
    def __init__(
        self,
        name: str,
        role_desc: str,
        backend: Union[BackendConfig, IntelligenceBackend],
        global_prompt: str = None,
        **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)

    def act(self, observation: List[Message]) -> str:
        return super().act(observation)


class DocumentExtractorPlayer(Player):
    """
    Player responsible for reading legal draft documents and presenting text to the council.
    """

    def __init__(
        self,
        name: str,
        role_desc: str,
        paper_id: int = None,
        paper_decision: str = None,
        conference: str = "RIA",
        backend: Union[BackendConfig, IntelligenceBackend] = None,
        paper_pdf_path: str = None,
        doc_path: str = None,
        global_prompt: str = None,
        **kwargs,
    ):
        super().__init__(name, role_desc, backend, global_prompt, **kwargs)
        self.paper_id = paper_id
        self.doc_id = paper_id
        self.paper_decision = paper_decision
        self.doc_type = paper_decision
        self.conference: str = conference
        self.paper_pdf_path = doc_path or paper_pdf_path
        self.doc_path = self.paper_pdf_path

    def act(self, observation: List[Message]) -> str:
        if self.paper_pdf_path is not None:
            logging.info(f"Đọc văn bản quy phạm pháp luật từ: {self.paper_pdf_path}")
            document_path = Path(self.paper_pdf_path)
        else:
            document_path = Path(
                os.path.join(getattr(self.args, "data_dir", "data"), self.conference, f"{self.paper_id}.pdf")
            )

        loader = PDFReader()
        documents = loader.load_data(file=document_path)

        num_words = 0
        main_contents = "Nội dung hồ sơ đề nghị xây dựng / dự thảo văn bản:\n\n"
        max_words = getattr(getattr(self, "args", None), "max_num_words", 8000)

        for doc in documents:
            words = doc.text.split(" ")
            if len(words) + num_words > max_words:
                words = words[: max_words - num_words]
                num_words += len(words)
                main_contents += " ".join(words) + " "
                break
            num_words += len(words)
            main_contents += " ".join(words) + " "

        return main_contents


# Backward compatibility aliases
PaperExtractorPlayer = DocumentExtractorPlayer
