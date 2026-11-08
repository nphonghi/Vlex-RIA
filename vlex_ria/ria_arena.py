import csv
import json
import logging
from typing import Union

from vlex_ria.arena import Arena, TooManyInvalidActions
from ria_role_descriptions import get_reviewer_description
from vlex_ria.utility.utils import format_metareviews
from vlex_ria.agent import Player
from vlex_ria.config import ArenaConfig
from vlex_ria.environments import TimeStep, load_environment
from vlex_ria.ria_player import DocumentExtractorPlayer, PaperExtractorPlayer, AreaChair, Reviewer


logger = logging.getLogger(__name__)


class RIAArena(Arena):
    """
    Council Arena orchestrating the multi-agent RIA review rounds.
    """

    @classmethod
    def from_config(cls, config: Union[str, ArenaConfig]):
        if isinstance(config, str):
            config = ArenaConfig.load(config)

        global_prompt = config.get("global_prompt", None)

        players = []
        for player_config in config.players:
            if global_prompt is not None:
                player_config["global_prompt"] = global_prompt

            if "Extractor" in player_config['name']:
                player = DocumentExtractorPlayer.from_config(player_config)
            elif player_config['name'].startswith("AC"):
                player = AreaChair.from_config(player_config)
            elif player_config['name'].startswith("Reviewer"):
                player = Reviewer.from_config(player_config)
            else:
                player = Player.from_config(player_config)
            players.append(player)

        player_names = [player.name for player in players]
        assert len(player_names) == len(
            set(player_names)
        ), f"Player names must be unique: {[','.join(player_names)]}"

        config.environment["player_names"] = player_names
        env = load_environment(config.environment)

        return cls(players, env, global_prompt=global_prompt)

    def step(self) -> TimeStep:
        player_name = self.environment.get_next_player()
        player = self.name_to_player[player_name]
        observation = self.environment.get_observation(player_name)

        timestep = None

        for i in range(self.invalid_actions_retry):
            if self.environment.phase_index == 3 and player.name.startswith("Reviewer"):
                reviewer_index = int(player.name.split("Reviewer ")[1])
                player.role_desc = get_reviewer_description(
                    phase="reviewer_ac_discussion",
                    **self.environment.experiment_setting["players"]['Reviewer'][reviewer_index - 1]
                )

            elif self.environment.phase_index == 5:
                player.role_desc += format_metareviews(
                    getattr(self.environment, "metareviews", []),
                    getattr(self.environment, "paper_ids", [])
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

    def save_history(self, path: str):
        messages = self.environment.get_observation()
        message_rows = []

        if path.endswith(".csv"):
            header = [
                "agent_name",
                "content",
                "turn",
                "timestamp",
                "visible_to",
                "msg_type",
            ]
            for message in messages:
                message_rows.append([
                    message.agent_name,
                    message.content,
                    message.turn,
                    str(message.timestamp),
                    message.visible_to,
                    message.msg_type,
                ])

            with open(path, "w") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(message_rows)
        elif path.endswith(".json"):
            for message in messages:
                message_rows.append({
                    "agent_name": message.agent_name,
                    "content": message.content,
                    "turn": message.turn,
                    "timestamp": str(message.timestamp),
                    "visible_to": message.visible_to,
                    "msg_type": message.msg_type,
                })

            with open(path, "w") as f:
                json.dump({
                    "experiment_setting": self.environment.experiment_setting,
                    "messages": message_rows,
                }, f, indent=2)
        else:
            raise ValueError("Invalid file format")


# Backward compatibility alias
PaperReviewArena = RIAArena
