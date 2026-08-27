from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    storage_root: Path
    connector_mode: str = "mock"
    reasoning_mode: str = "heuristic"
    codex_broker_base_url: str | None = None
    codex_broker_timeout_sec: int = 60
    use_codex_exec: bool = False
    codex_exec_command: str = "codex"
    codex_exec_timeout_sec: int = 180
    codex_config_path: Path | None = None
    jira_base_url: str | None = None
    jira_username: str | None = None
    jira_token: str | None = None
    jira_acceptance_criteria_field: str = "customfield_acceptance_criteria"
    jira_dependencies_field: str = "customfield_dependencies"
    jira_edge_cases_field: str = "customfield_edge_cases"
    jira_epic_field: str = "customfield_epic"
    jira_epic_jql_field: str = '"Epic Name"'
    jira_story_points_field: str = "customfield_10010"
    jira_release_field: str = "fixVersions"
    jira_release_jql_field: str = "fixVersion"
    jira_sprint_field: str = "customfield_sprint"
    jira_write_fields: dict[str, str] = field(
        default_factory=lambda: {
            "ai_enrichment_summary": "customfield_ai_enrichment_summary",
            "ai_implementation_brief": "customfield_ai_implementation_brief",
        }
    )
    confluence_base_url: str | None = None
    confluence_username: str | None = None
    confluence_token: str | None = None
    confluence_use_web_session: bool = False
    confluence_mcp_server: str = "central-confluence"
    confluence_mcp_command: str | None = None
    confluence_mcp_args: tuple[str, ...] = ()
    confluence_mcp_endpoint: str | None = None
    confluence_mcp_startup_timeout_sec: int = 300
    confluence_mcp_request_timeout_sec: int = 20
    bitbucket_base_url: str | None = None
    bitbucket_username: str | None = None
    bitbucket_token: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    rl_enabled: bool = False
    rl_prompt_library_path: str = ""
    rl_policy: str = "thompson"
    rl_target_confidence: int = 9
    rl_max_prompt_retries: int = 10
    rl_force_codex_for_selected_prompt: bool = True

    @classmethod
    def from_runtime(cls, storage_root: str | Path) -> "AppConfig":
        codex_config_path = Path(os.getenv("CODEX_CONFIG_PATH", "~/.codex/config.toml")).expanduser()
        codex_values = cls._load_codex_values(codex_config_path) if codex_config_path.exists() else {}
        connector_mode = os.getenv("JIRA_ENHANCER_CONNECTOR_MODE", codex_values.get("connector_mode", "mock")).strip().lower()
        llm_base_url = os.getenv("JIRA_ENHANCER_LLM_BASE_URL", "")
        llm_api_key = os.getenv("JIRA_ENHANCER_LLM_API_KEY", "")
        reasoning_mode = os.getenv(
            "JIRA_ENHANCER_REASONING_MODE",
            "model" if llm_base_url and llm_api_key else "heuristic",
        ).strip().lower()
        codex_exec_command = os.getenv("CODEX_EXEC_COMMAND", shutil.which("codex") or "codex")
        use_codex_exec_env = os.getenv("JIRA_ENHANCER_USE_CODEX_EXEC")
        default_use_codex_exec = connector_mode == "live" and bool(shutil.which(codex_exec_command))
        return cls(
            storage_root=Path(storage_root),
            connector_mode=connector_mode,
            reasoning_mode=reasoning_mode,
            codex_broker_base_url=os.getenv("CODEX_BROKER_BASE_URL", ""),
            codex_broker_timeout_sec=int(os.getenv("CODEX_BROKER_TIMEOUT_SEC", "60")),
            use_codex_exec=(
                use_codex_exec_env.strip().lower() == "true" if use_codex_exec_env is not None else default_use_codex_exec
            ),
            codex_exec_command=codex_exec_command,
            codex_exec_timeout_sec=int(os.getenv("CODEX_EXEC_TIMEOUT_SEC", "180")),
            codex_config_path=codex_config_path if codex_config_path.exists() else None,
            jira_base_url=os.getenv("JIRA_BASE_URL", codex_values.get("jira_base_url") or ""),
            jira_username=os.getenv("JIRA_USERNAME", codex_values.get("jira_username") or ""),
            jira_token=os.getenv("JIRA_TOKEN", codex_values.get("jira_token") or ""),
            jira_acceptance_criteria_field=os.getenv("JIRA_ACCEPTANCE_CRITERIA_FIELD", "customfield_acceptance_criteria"),
            jira_dependencies_field=os.getenv("JIRA_DEPENDENCIES_FIELD", "customfield_dependencies"),
            jira_edge_cases_field=os.getenv("JIRA_EDGE_CASES_FIELD", "customfield_edge_cases"),
            jira_epic_field=os.getenv("JIRA_EPIC_FIELD", os.getenv("JIRA_FEATURE_FIELD", "customfield_epic")),
            jira_epic_jql_field=os.getenv("JIRA_EPIC_JQL_FIELD", os.getenv("JIRA_FEATURE_JQL_FIELD", '"Epic Name"')),
            jira_story_points_field=os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10010"),
            jira_release_field=os.getenv("JIRA_RELEASE_FIELD", "fixVersions"),
            jira_release_jql_field=os.getenv("JIRA_RELEASE_JQL_FIELD", "fixVersion"),
            jira_sprint_field=os.getenv("JIRA_SPRINT_FIELD", "customfield_sprint"),
            jira_write_fields={
                "ai_enrichment_summary": os.getenv("JIRA_AI_SUMMARY_FIELD_ID", "customfield_ai_enrichment_summary"),
                "ai_implementation_brief": os.getenv("JIRA_AI_BRIEF_FIELD_ID", "customfield_ai_implementation_brief"),
            },
            confluence_base_url=os.getenv("CONFLUENCE_BASE_URL", codex_values.get("confluence_base_url") or ""),
            confluence_username=os.getenv("CONFLUENCE_USERNAME", codex_values.get("confluence_username") or ""),
            confluence_token=os.getenv("CONFLUENCE_TOKEN", codex_values.get("confluence_token") or ""),
            confluence_use_web_session=os.getenv(
                "CONFLUENCE_USE_WEB_SESSION",
                "true" if codex_values.get("confluence_use_web_session") else "false",
            ).strip().lower()
            == "true",
            confluence_mcp_server=str(codex_values.get("confluence_mcp_server") or "central-confluence"),
            confluence_mcp_command=os.getenv("CONFLUENCE_MCP_COMMAND", codex_values.get("confluence_mcp_command") or ""),
            confluence_mcp_args=tuple(codex_values.get("confluence_mcp_args") or ()),
            confluence_mcp_endpoint=os.getenv("CONFLUENCE_MCP_ENDPOINT", codex_values.get("confluence_mcp_endpoint") or ""),
            confluence_mcp_startup_timeout_sec=int(
                os.getenv(
                    "CONFLUENCE_MCP_STARTUP_TIMEOUT_SEC",
                    str(codex_values.get("confluence_mcp_startup_timeout_sec") or 300),
                )
            ),
            confluence_mcp_request_timeout_sec=int(
                os.getenv(
                    "CONFLUENCE_MCP_REQUEST_TIMEOUT_SEC",
                    str(codex_values.get("confluence_mcp_request_timeout_sec") or 20),
                )
            ),
            bitbucket_base_url=os.getenv("BITBUCKET_BASE_URL", codex_values.get("bitbucket_base_url") or ""),
            bitbucket_username=os.getenv("BITBUCKET_USERNAME", codex_values.get("bitbucket_username") or ""),
            bitbucket_token=os.getenv("BITBUCKET_TOKEN", codex_values.get("bitbucket_token") or ""),
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=os.getenv("JIRA_ENHANCER_LLM_MODEL", "gpt-4.1-mini"),
            rl_enabled=os.getenv("JIRA_ENHANCER_RL_ENABLED", "false").strip().lower() == "true",
            rl_prompt_library_path=os.getenv("JIRA_ENHANCER_RL_PROMPT_LIBRARY_PATH", ""),
            rl_policy=os.getenv("JIRA_ENHANCER_RL_POLICY", "thompson").strip().lower(),
            rl_target_confidence=int(os.getenv("JIRA_ENHANCER_RL_TARGET_CONFIDENCE", "9")),
            rl_max_prompt_retries=int(os.getenv("JIRA_ENHANCER_RL_MAX_PROMPT_RETRIES", "10")),
            rl_force_codex_for_selected_prompt=os.getenv("JIRA_ENHANCER_RL_FORCE_CODEX", "true").strip().lower() == "true",
        )

    @staticmethod
    def _load_codex_values(config_path: Path) -> dict[str, str | bool]:
        with config_path.open("rb") as handle:
            parsed = tomllib.load(handle)

        mcp_servers = parsed.get("mcp_servers", {})
        jira_server = mcp_servers.get("example-jira", {})
        if not jira_server:
            jira_server = next(
                (
                    server
                    for name, server in mcp_servers.items()
                    if "jira" in str(name).lower()
                    and "service-desk" not in str(name).lower()
                    and not str(name).lower().endswith("-sd")
                ),
                {},
            )
        jira_sd_server = mcp_servers.get("mcp-atlassian-jira-sd", {})
        bitbucket_server = mcp_servers.get("bitbucket-mcp-server", {})
        confluence_server_name, confluence_server = AppConfig._select_mcp_server(
            mcp_servers,
            ["central-confluence", "oci-confluence"],
        )

        jira_env = AppConfig._load_env_file_from_args(jira_server.get("args", []))
        jira_sd_env = AppConfig._load_env_file_from_args(jira_sd_server.get("args", []))
        bitbucket_env = AppConfig._load_env_file_from_args(bitbucket_server.get("args", []))

        confluence_url = AppConfig._extract_arg_value(confluence_server.get("args", []), "--confluence-url")
        use_web_session = "--confluence-use-web-session" in confluence_server.get("args", [])

        has_live_values = bool(jira_env or bitbucket_env or confluence_url)
        return {
            "connector_mode": "live" if has_live_values else "mock",
            "jira_base_url": jira_env.get("JIRA_URL") or jira_sd_env.get("JIRA_URL") or "",
            "jira_token": jira_env.get("JIRA_PERSONAL_TOKEN") or jira_sd_env.get("JIRA_PERSONAL_TOKEN") or "",
            "jira_username": jira_env.get("JIRA_USERNAME", ""),
            "bitbucket_base_url": bitbucket_env.get("BITBUCKET_URL", ""),
            "bitbucket_token": bitbucket_env.get("BITBUCKET_TOKEN", ""),
            "bitbucket_username": bitbucket_env.get("BITBUCKET_USERNAME", ""),
            "confluence_base_url": confluence_url or "",
            "confluence_username": "",
            "confluence_token": "",
            "confluence_use_web_session": use_web_session,
            "confluence_mcp_server": confluence_server_name,
            "confluence_mcp_command": confluence_server.get("command") or "",
            "confluence_mcp_args": list(confluence_server.get("args", [])),
            "confluence_mcp_endpoint": "",
            "confluence_mcp_startup_timeout_sec": int(confluence_server.get("startup_timeout_sec") or 300),
            "confluence_mcp_request_timeout_sec": 20,
        }

    @staticmethod
    def _select_mcp_server(mcp_servers: dict[str, object], names: list[str]) -> tuple[str, dict[str, object]]:
        for name in names:
            server = mcp_servers.get(name, {})
            if server:
                return name, server
        return names[0], {}

    @staticmethod
    def _extract_arg_value(args: list[str], flag: str) -> str | None:
        for index, value in enumerate(args):
            if value == flag and index + 1 < len(args):
                return args[index + 1]
        return None

    @staticmethod
    def _load_env_file_from_args(args: list[str]) -> dict[str, str]:
        env_file = AppConfig._extract_arg_value(args, "--env-file")
        if not env_file:
            return {}
        return AppConfig._parse_env_file(Path(env_file))

    @staticmethod
    def _parse_env_file(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    @property
    def mvp_root(self) -> Path:
        return self.storage_root / "storage" / "mvp"

    @property
    def mock_jira_root(self) -> Path:
        return self.mvp_root / "mock_jira"
