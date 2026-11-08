import json
import logging
import os.path as osp
from typing import List

from vlex_ria.environments.conversation import Conversation
from vlex_ria.utility.utils import get_rebuttal_dir
from vlex_ria.environments.base import TimeStep
from vlex_ria.message import Message
from vlex_ria.ria_message import RIAMessagePool, PaperReviewMessagePool


logger = logging.getLogger(__name__)


class RIAReview(Conversation):
    """
    Multi-agent deliberation environment for Regulatory Impact Assessment (RIA).
    
    Phases:
      Phase 0: Document extraction (extract legal draft text)
      Phase 1: Individual RIA reviews (Reviewers analyze policy options)
      Phase 2: Author consultation / drafting agency response
      Phase 3: Multi-agent debate & synthesis
      Phase 4: Synthesis & final RIA report generation
    """

    type_name = "ria_review"

    def __init__(self, player_names: List[str], paper_id: int = None, paper_decision: str = None,
                 experiment_setting: dict = None, args = None, parallel: bool = False, **kwargs):
        super(Conversation, self).__init__(player_names=player_names, parallel=parallel, **kwargs)
        self.args = args
        self.paper_id = paper_id
        self.doc_id = paper_id
        self.paper_decision = paper_decision
        self.doc_type = paper_decision
        self.parallel = parallel
        self.experiment_setting = experiment_setting or {}
        self.player_to_test = self.experiment_setting.get('player_to_test', None)
        self.task = kwargs.get("task", "ria_review")
        self.experiment_name = getattr(args, "experiment_name", "RIA")

        self.message_pool = RIAMessagePool(self.experiment_setting)
        self.phase_index = 0
        self._phases = None

    @property
    def phases(self):
        if self._phases is not None:
            return self._phases

        reviewer_names = [name for name in self.player_names if name.startswith("Reviewer")]
        num_reviewers = len(reviewer_names)
        reviewer_names = [f"Reviewer {i}" for i in range(1, num_reviewers + 1)]

        extractor_name = "Document Extractor"
        for p in self.player_names:
            if "Extractor" in p:
                extractor_name = p
                break

        self._phases = {
            0: {
                "name": "document_extraction",
                "speaking_order": [extractor_name],
            },
            1: {
                "name": "reviewer_write_reviews",
                "speaking_order": reviewer_names,
            },
            2: {
                "name": "author_reviewer_discussion",
                "speaking_order": ["Author" for _ in reviewer_names],
            },
            3: {
                "name": "reviewer_ac_discussion",
                "speaking_order": ["AC"] + reviewer_names,
            },
            4: {
                "name": "ac_write_metareviews",
                "speaking_order": ["AC"],
            },
            5: {
                "name": "ac_makes_decisions",
                "speaking_order": ["AC"],
            },
        }
        return self._phases

    @phases.setter
    def phases(self, value):
        self._phases = value

    def reset(self):
        self._current_phase = "review"
        self.phase_index = 0
        return super().reset()

    def load_message_history_from_cache(self):
        if self._phase_index == 0:
            full_path = get_rebuttal_dir(
                paper_id=self.paper_id,
                experiment_name="BASELINE",
                model_name=self.args.model_name,
                conference=self.args.conference,
            )
            messages = json.load(open(osp.join(full_path, f"{self.paper_id}.json"), 'r', encoding='utf-8'))['messages']
            num_messages_from_AC = 0

            for msg in messages:
                if "Extractor" in msg.get('agent_name', ''):
                    continue
                if msg['agent_name'] == "AC" and num_messages_from_AC == 1:
                    break
                if msg['agent_name'] == "AC":
                    num_messages_from_AC += 1

                message = Message(**msg)
                self.message_pool.append_message(message)

            self._phase_index = 4

    def step(self, player_name: str, action: str) -> TimeStep:
        message = Message(
            agent_name=player_name, content=action, turn=self._current_turn
        )
        self.message_pool.append_message(message)

        speaking_order = self.phases[self.phase_index]["speaking_order"]
        logging.info(
            f"Phase {self.phase_index}: {self.phases[self.phase_index]['name']} "
            f"| Player {self._next_player_index}: {speaking_order[self._next_player_index]}"
        )

        terminal = self.is_terminal()

        if self._next_player_index == len(speaking_order) - 1:
            self._next_player_index = 0
            if self.phase_index == 4:
                terminal = True
                logger.info("Hoàn tất quy trình thẩm định RIA (Phase I - IV).")
            else:
                self.phase_index += 1
                self._current_turn += 1
        else:
            self._next_player_index += 1

        return TimeStep(
            observation=self.get_observation(),
            reward=self.get_zero_rewards(),
            terminal=terminal,
        )

    def get_next_player(self) -> str:
        speaking_order = self.phases[self.phase_index]["speaking_order"]
        return speaking_order[self._next_player_index]

    def get_observation(self, player_name=None) -> List[Message]:
        if player_name is None:
            return self.message_pool.get_all_messages()
        return self.message_pool.get_visible_messages_for_ria(
            player_name,
            phase_index=self.phase_index,
            next_player_idx=self._next_player_index,
            player_names=self.player_names,
        )

    def get_messages_from_player(self, player_name: str) -> List[str]:
        return self.message_pool.get_messages_from_player(player_name)


# Backward compatibility alias
PaperReview = RIAReview
