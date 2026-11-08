import logging
from typing import List

from vlex_ria.message import MessagePool, Message


class RIAMessagePool(MessagePool):
    """
    Message pool managing message visibility across multi-agent RIA review phases.
    """

    def __init__(self, experiment_setting: dict):
        super().__init__()
        self.experiment_setting = experiment_setting

    def get_visible_messages_for_ria(
        self, agent_name: str, phase_index: int, next_player_idx: int, player_names: List[str]
    ) -> List[Message]:
        reviewer_names = sorted([name for name in player_names if name.startswith("Reviewer")])
        prev_messages = self._messages

        def is_extractor(name: str) -> bool:
            return "Extractor" in name

        if phase_index in [0, 1]:
            visible_messages = [m for m in prev_messages if is_extractor(m.agent_name)]

        elif phase_index == 2:
            visible_messages = []
            target_rev = reviewer_names[next_player_idx] if next_player_idx < len(reviewer_names) else ""
            for message in prev_messages:
                if is_extractor(message.agent_name) or message.agent_name == target_rev:
                    visible_messages.append(message)

        elif phase_index == 3:
            if any(agent_name.startswith(p) for p in ["AC", "Reviewer", "Extractor"]):
                visible_messages = prev_messages
            elif agent_name.startswith("Author"):
                visible_messages = []
            else:
                visible_messages = prev_messages

        elif phase_index == 4:
            if agent_name.startswith("AC"):
                players_cfg = self.experiment_setting.get("players", {})
                ac_list = players_cfg.get("AC", [{}])
                ac_type = ac_list[0].get("area_chair_type", "inclusive") if ac_list else "inclusive"

                if ac_type in ["inclusive", "BASELINE", None]:
                    visible_messages = prev_messages
                elif ac_type == "conformist":
                    visible_messages = [
                        m for m in prev_messages
                        if m.agent_name.startswith("Author") or m.agent_name.startswith("Reviewer")
                    ]
                elif ac_type == "authoritarian":
                    visible_messages = [
                        m for m in prev_messages
                        if not (m.agent_name.startswith("Author") or m.agent_name.startswith("Reviewer"))
                    ]
                else:
                    visible_messages = prev_messages
            else:
                visible_messages = prev_messages

        else:
            visible_messages = []
            for message in prev_messages:
                if (
                    message.visible_to == "all"
                    or agent_name in message.visible_to
                    or agent_name == "Moderator"
                ):
                    visible_messages.append(message)

        logging.info(
            f"Phase {phase_index}: {agent_name} sees {len(visible_messages)} messages from "
            f"{','.join([agent.agent_name for agent in visible_messages]) if visible_messages else 'None'}"
        )

        return visible_messages

    get_visible_messages_for_paper_review = get_visible_messages_for_ria


# Backward compatibility alias
PaperReviewMessagePool = RIAMessagePool
