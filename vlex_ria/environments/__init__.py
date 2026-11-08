from vlex_ria.config import EnvironmentConfig
from vlex_ria.environments.base import Environment, TimeStep
from vlex_ria.environments.conversation import Conversation, ModeratedConversation
from vlex_ria.environments.ria_review import RIAReview, PaperReview

ALL_ENVIRONMENTS = [
    Conversation,
    ModeratedConversation,
    RIAReview,
]

ENV_REGISTRY = {
    "ria_review": RIAReview,
    "paper_review": RIAReview,
    "conversation": Conversation,
    "moderated_conversation": ModeratedConversation,
}


def load_environment(config: EnvironmentConfig):
    try:
        env_cls = ENV_REGISTRY[config["env_type"]]
    except KeyError:
        raise ValueError(f"Unknown environment type: {config['env_type']}")

    env = env_cls.from_config(config)
    return env
