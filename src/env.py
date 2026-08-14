from dataclasses import dataclass, field


@dataclass
class Env:
    # secrets
    BOT_TOKEN: str
    BOT_SECRET: str
    ACCESS_ENDPOINT: str
    CF_TOKEN: str

    # variables
    WEBHOOK_URL: str
    USER_CHANNEL_ID: int
    NODE_LIST: str
    NODE_CONFIG_VERSION: int
    CF_ACCOUNT_ID: str
    CF_SCRIPT_NAME: str
    CF_USER_DURABLE_OBJECT_NAMESPACE_ID: str

    # bindings
    USER_DURABLE_OBJECT: object

    # other
    DYNAMIC: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def wrap(env):
        dyn_env = {}

        for name in dir(env._env):  # HACK
            attr = getattr(env._env, name)
            if len(name) >= 2 and name[0] == '_' and name[1].isupper() and type(attr) is str:
                dyn_env[name] = attr

        return Env(
            env.BOT_TOKEN,
            env.BOT_SECRET,
            env.ACCESS_ENDPOINT,
            env.CF_TOKEN,
            env.WEBHOOK_URL,
            int(env.USER_CHANNEL_ID),
            env.NODE_LIST,
            int(env.NODE_CONFIG_VERSION),
            env.CF_ACCOUNT_ID,
            env.CF_SCRIPT_NAME,
            env.CF_USER_DURABLE_OBJECT_NAMESPACE_ID,
            env.USER_DURABLE_OBJECT,
            dyn_env,
        )
