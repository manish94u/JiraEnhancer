from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .config import AppConfig
from .rl.service import RlPromptService
from .services import (
    ApprovalService,
    CodexExecSemanticReasoner,
    CodexBrokerSemanticReasoner,
    EvidenceResolver,
    HeuristicSemanticReasoner,
    JiraEnricher,
    JiraScanner,
    ModelSemanticReasoner,
    MvpOrchestrator,
    PolicyService,
    ReadinessScorer,
    ReplayWorker,
    ScopeResolver,
    WritebackService,
)
from .sources import build_sources
from .store import FilesystemStore


def build_orchestrator(storage_root: str | Path, broker_base_url: str | None = None) -> MvpOrchestrator:
    config = AppConfig.from_runtime(Path(storage_root))
    if broker_base_url and not config.use_codex_exec and not config.codex_broker_base_url and config.connector_mode == "live":
        config = replace(config, codex_broker_base_url=broker_base_url)
    store = FilesystemStore(config)
    jira, confluence, bitbucket = build_sources(config)
    semantic_reasoner = HeuristicSemanticReasoner()
    if config.use_codex_exec:
        semantic_reasoner = CodexExecSemanticReasoner(
            codex_command=config.codex_exec_command,
            timeout_sec=config.codex_exec_timeout_sec,
            workdir=str(Path(storage_root)),
            fallback=semantic_reasoner,
        )
    elif config.codex_broker_base_url:
        semantic_reasoner = CodexBrokerSemanticReasoner(
            base_url=config.codex_broker_base_url,
            fallback=semantic_reasoner,
        )
    elif config.reasoning_mode == "model" and config.llm_base_url and config.llm_api_key:
        semantic_reasoner = ModelSemanticReasoner(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
            fallback=semantic_reasoner,
        )
    rl_prompt_service = None
    if config.rl_enabled:
        library_path = Path(config.rl_prompt_library_path) if config.rl_prompt_library_path else None
        rl_prompt_service = RlPromptService(
            config.mvp_root / "rl",
            prompt_library_path=library_path,
            policy_name=config.rl_policy,
        )

    return MvpOrchestrator(
        store=store,
        scope_resolver=ScopeResolver(jira),
        scanner=JiraScanner(jira),
        scorer=ReadinessScorer(),
        evidence_resolver=EvidenceResolver(
            confluence,
            bitbucket,
            confluence_base_url=config.confluence_base_url or "",
            confluence_use_web_session=config.confluence_use_web_session,
            skip_external_fetch=config.use_codex_exec,
        ),
        semantic_reasoner=semantic_reasoner,
        enricher=JiraEnricher(),
        policy_service=PolicyService(),
        approval_service=ApprovalService(),
        writeback_service=WritebackService(store, jira),
        replay_worker=ReplayWorker(store),
        rl_prompt_service=rl_prompt_service,
        rl_target_confidence=config.rl_target_confidence,
        rl_max_prompt_retries=config.rl_max_prompt_retries,
        rl_force_codex_for_selected_prompt=config.rl_force_codex_for_selected_prompt,
    )
