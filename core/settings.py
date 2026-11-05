"""The sign-in settings, read from environment variables.

Environment variables are the one channel every web server in front of QGIS
Server offers, so the plugin reads its settings from them alone.
"""

from dataclasses import dataclass, field

REQUIRED = ("CASTLEGATE_ISSUER", "CASTLEGATE_AUDIENCE", "CASTLEGATE_SCOPE")


@dataclass(frozen=True)
class Settings:
    issuer: str = ""
    audience: str = ""
    scope: str = ""
    # Optional. When set, the token's `tid` claim must equal it.
    tenant: str = ""
    id_claim: str = "oid"
    email_claim: str = "preferred_username"
    groups_claim: str = "groups"
    # Names of the required settings that are missing. A token is refused while any is.
    missing: tuple = field(default=())

    @classmethod
    def from_environment(cls, environ):
        def get(name, default=""):
            return (environ.get(name) or default).strip()

        missing = tuple(name for name in REQUIRED if not get(name))
        return cls(
            issuer=get("CASTLEGATE_ISSUER"),
            audience=get("CASTLEGATE_AUDIENCE"),
            scope=get("CASTLEGATE_SCOPE"),
            tenant=get("CASTLEGATE_TENANT"),
            id_claim=get("CASTLEGATE_ID_CLAIM", "oid"),
            email_claim=get("CASTLEGATE_EMAIL_CLAIM", "preferred_username"),
            groups_claim=get("CASTLEGATE_GROUPS_CLAIM", "groups"),
            missing=missing,
        )
