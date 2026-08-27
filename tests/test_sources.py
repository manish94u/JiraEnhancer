from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.config import AppConfig
from jira_enhancer.sources import (
    CodexBrokerConfluenceSource,
    HttpMcpToolClient,
    LiveBitbucketSource,
    LiveConfluenceSource,
    LiveJiraSource,
    build_sources,
)


class LiveSourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.mcp_calls: list[tuple[str, list[str], str, dict]] = []
        self.config = AppConfig(
            storage_root=Path("/tmp/jira-enhancer-test"),
            connector_mode="live",
            jira_base_url="https://jira.example.com",
            jira_username="jira-user",
            jira_token="jira-token",
            jira_acceptance_criteria_field="customfield_11111",
            jira_dependencies_field="customfield_11112",
            jira_edge_cases_field="customfield_11113",
            jira_epic_field="customfield_11114",
            jira_epic_jql_field="cf[11114]",
            jira_release_field="fixVersions",
            jira_release_jql_field="fixVersion",
            jira_sprint_field="customfield_11115",
            jira_write_fields={
                "ai_enrichment_summary": "customfield_22221",
                "ai_implementation_brief": "customfield_22222",
            },
            confluence_base_url="https://confluence.example.com",
            confluence_username="conf-user",
            confluence_token="conf-token",
            confluence_mcp_server="central-confluence",
            confluence_mcp_command="/opt/homebrew/bin/uvx",
            confluence_mcp_args=("mcp-atlassian", "--confluence-url", "https://confluence.example.com", "--confluence-use-web-session"),
            bitbucket_base_url="https://bitbucket.example.com",
            bitbucket_username="bb-user",
            bitbucket_token="bb-token",
        )

    def fake_request_json(self, method: str, url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
        self.calls.append((method, url, headers, payload))
        if url == "http://127.0.0.1:9000/api/v1/confluence/get-page":
            return {
                "id": "20026531262",
                "title": "Architecture",
                "summary": "Architecture body",
                "version": 15,
                "url": "https://confluence.example.com/pages/viewpage.action?pageId=20026531262",
            }
        if "rest/api/2/issue/DEMO-18324/remotelink" in url:
            return [
                {"object": {"url": "https://confluence.example.com/pages/viewpage.action?pageId=20026531262"}},
                {"object": {"url": "https://bitbucket.example.com/projects/DEMO/repos/platform/pull-requests/18821"}},
            ]
        if "rest/api/2/issue/DEMO-123/remotelink" in url:
            return []
        if "rest/api/2/issue/DEMO-123" in url and "remotelink" not in url:
            return {
                "key": "DEMO-123",
                "fields": {
                    "summary": "Created story",
                    "description": "Created story description",
                    "comment": {"comments": []},
                    "worklog": {"worklogs": []},
                    "customfield_11114": "DEMO-999",
                },
            }
        if "rest/api/2/issue/DEMO-18324" in url and "remotelink" not in url:
            return {
                "key": "DEMO-18324",
                "fields": {
                    "summary": "Enable bounded AI writeback",
                    "description": "Use AI-owned fields only.",
                    "comment": {"comments": [{"body": "Need approval"}]},
                    "worklog": {"worklogs": [{"comment": "Defined contract"}]},
                    "customfield_11111": ["Only AI fields"],
                    "customfield_11112": ["Approval service"],
                    "customfield_11113": ["Stale issue"],
                    "customfield_11114": "AI Enrichment",
                    "fixVersions": ["2026.06"],
                    "customfield_11115": ["SPRINT-42"],
                },
            }
        if method == "POST" and url.endswith("/rest/api/2/issue"):
            return {"key": "DEMO-123"}
        if "rest/api/2/search" in url:
            return {"issues": [{"key": "DEMO-18324"}, {"key": "DEMO-18325"}]}
        if "rest/agile/1.0/sprint/SPRINT-42/issue" in url:
            return {"issues": [{"key": "DEMO-18324"}, {"key": "DEMO-18325"}]}
        if "pull-requests/18821/changes" in url:
            return {"values": [{"path": {"toString": "src/writeback.py"}}]}
        if "pull-requests/18821" in url:
            return {
                "id": 18821,
                "title": "Writeback constraints",
                "fromRef": {"displayId": "feature/DEMO-18324"},
                "reviewers": [{"user": {"name": "tech.lead"}}],
            }
        return {}

    def fake_mcp_call_tool(self, command: str, args: list[str], tool_name: str, arguments: dict) -> dict:
        self.mcp_calls.append((command, args, tool_name, arguments))
        self.assertEqual(command, "/opt/homebrew/bin/uvx")
        self.assertEqual(tool_name, "confluence_get_page")
        if arguments.get("page_id") == "20026531262":
            return {
                "metadata": {
                    "id": "20026531262",
                    "title": "Architecture",
                    "version": 15,
                    "url": "https://confluence.example.com/pages/viewpage.action?pageId=20026531262",
                },
                "content": {"value": "Architecture body", "format": "markdown"},
            }
        if arguments.get("title") == "DEMO Kafka Subscriptions":
            return {
                "metadata": {
                    "id": "30000000001",
                    "title": "DEMO Kafka Subscriptions",
                    "version": 3,
                    "url": "https://confluence.example.com/confluence/display/DEMO/DEMO+Kafka+Subscriptions",
                },
                "content": {"value": "Kafka subscriptions design", "format": "markdown"},
            }
        raise AssertionError(f"Unexpected MCP arguments: {arguments}")

    def test_live_jira_source_normalizes_issue_and_remote_links(self) -> None:
        source = LiveJiraSource(self.config, request_json=self.fake_request_json)
        issue = source.get_issue("DEMO-18324")
        self.assertEqual(issue["key"], "DEMO-18324")
        self.assertEqual(issue["links"]["confluence_pages"], ["20026531262"])
        self.assertEqual(issue["links"]["bitbucket_prs"], ["DEMO/platform/18821"])
        self.assertIn("Only AI fields", issue["acceptance_criteria"])

    def test_live_jira_source_resolves_scope_and_writes_description(self) -> None:
        source = LiveJiraSource(self.config, request_json=self.fake_request_json)
        self.assertEqual(source.resolve_scope("sprint", "SPRINT-42"), ["DEMO-18324", "DEMO-18325"])
        self.assertEqual(source.resolve_scope("epic", "AI Enrichment"), ["DEMO-18324", "DEMO-18325"])
        source.write_description("DEMO-18324", "[AI_UPDATE_BEGIN]\nversion: 1\nSummary:\nsummary\n[AI_UPDATE_END]")
        put_calls = [call for call in self.calls if call[0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(
            put_calls[0][3],
            {
                "fields": {
                    "description": "[AI_UPDATE_BEGIN]\nversion: 1\nSummary:\nsummary\n[AI_UPDATE_END]",
                }
            },
        )

    def test_live_jira_source_resolves_epic_issue_key_to_stories(self) -> None:
        source = LiveJiraSource(self.config, request_json=self.fake_request_json)
        self.assertEqual(source.resolve_scope("epic", "DEMO-18324"), ["DEMO-18324", "DEMO-18325"])

    def test_live_jira_source_formats_cf_jql_without_quotes(self) -> None:
        source = LiveJiraSource(replace(self.config, jira_epic_jql_field="cf[11114]"), request_json=self.fake_request_json)
        source.resolve_scope("epic", "AI Enrichment")
        post_calls = [call for call in self.calls if call[0] == "POST"]
        self.assertEqual(post_calls[-1][3]["jql"], 'cf[11114] = "AI Enrichment"')

    def test_live_jira_source_quotes_human_field_names(self) -> None:
        source = LiveJiraSource(replace(self.config, jira_epic_jql_field="Epic Name"), request_json=self.fake_request_json)
        source.resolve_scope("epic", "AI Enrichment")
        post_calls = [call for call in self.calls if call[0] == "POST"]
        self.assertEqual(post_calls[-1][3]["jql"], '"Epic Name" = "AI Enrichment"')

    def test_live_jira_source_creates_story_then_updates_story_points(self) -> None:
        source = LiveJiraSource(self.config, request_json=self.fake_request_json)
        source.create_issue(
            project_key="OIC",
            summary="Created story",
            description="Created story description",
            issue_type="Story",
            additional_fields={
                "epic": "DEMO-999",
                "assignee": "author@example.com",
                "story_points": 5,
                "parent": "DEMO-999",
                "customfield_epic": "DEMO-999",
                "customfield_story_points": 5,
                "customfield_10010": 5,
            },
        )

        create_calls = [call for call in self.calls if call[0] == "POST" and call[1].endswith("/rest/api/2/issue")]
        self.assertEqual(len(create_calls), 1)
        create_fields = create_calls[0][3]["fields"]
        self.assertEqual(create_fields["project"], {"key": "OIC"})
        self.assertEqual(create_fields["customfield_10014"], "DEMO-999")
        self.assertEqual(create_fields["assignee"], {"name": "author@example.com"})
        self.assertNotIn("parent", create_fields)
        self.assertNotIn("customfield_epic", create_fields)
        self.assertNotIn("customfield_story_points", create_fields)
        self.assertNotIn("customfield_10010", create_fields)

        story_point_calls = [call for call in self.calls if call[0] == "PUT" and call[1].endswith("/rest/api/2/issue/DEMO-123")]
        self.assertEqual(len(story_point_calls), 1)
        self.assertEqual(story_point_calls[0][3], {"fields": {"customfield_10010": 5}})

    def test_live_confluence_and_bitbucket_sources_parse_payloads(self) -> None:
        confluence = LiveConfluenceSource(self.config, mcp_call_tool=self.fake_mcp_call_tool)
        bitbucket = LiveBitbucketSource(self.config, request_json=self.fake_request_json)
        page = confluence.get_page("20026531262")
        pr = bitbucket.get_pr("DEMO/platform/18821")
        self.assertEqual(page["version"], 15)
        self.assertEqual(page["summary"], "Architecture body")
        self.assertEqual(pr["repository"], "platform")
        self.assertEqual(pr["changed_files"], ["src/writeback.py"])

    def test_live_confluence_source_resolves_display_url(self) -> None:
        confluence = LiveConfluenceSource(self.config, mcp_call_tool=self.fake_mcp_call_tool)
        page = confluence.get_page("https://confluence.example.com/confluence/display/DEMO/DEMO+Kafka+Subscriptions")
        self.assertEqual(page["id"], "30000000001")
        self.assertEqual(page["title"], "DEMO Kafka Subscriptions")

    def test_live_confluence_source_uses_http_mcp_client_when_endpoint_configured(self) -> None:
        config = replace(self.config, confluence_mcp_endpoint="http://127.0.0.1:8765/mcp")
        confluence = LiveConfluenceSource(config)
        self.assertIsInstance(confluence.mcp_call_tool, HttpMcpToolClient)

    def test_codex_broker_confluence_source_fetches_page(self) -> None:
        config = replace(self.config, codex_broker_base_url="http://127.0.0.1:9000")
        confluence = CodexBrokerConfluenceSource(config, request_json=self.fake_request_json)
        page = confluence.get_page("20026531262")
        self.assertEqual(page["id"], "20026531262")

    def test_build_sources_prefers_local_confluence_when_codex_exec_enabled(self) -> None:
        config = replace(
            self.config,
            codex_broker_base_url="http://127.0.0.1:9000",
            use_codex_exec=True,
        )
        _, confluence, _ = build_sources(config, request_json=self.fake_request_json)
        self.assertIsInstance(confluence, LiveConfluenceSource)


class ConfigFromRuntimeTest(unittest.TestCase):
    def test_from_runtime_builds_live_config_from_env(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ["CODEX_CONFIG_PATH"] = "/tmp/does-not-exist.toml"
            os.environ["JIRA_ENHANCER_CONNECTOR_MODE"] = "live"
            os.environ["JIRA_BASE_URL"] = "https://jira.example.com"
            os.environ["JIRA_USERNAME"] = "jira-user"
            os.environ["JIRA_TOKEN"] = "jira-token"
            os.environ["JIRA_STORY_POINTS_FIELD"] = "customfield_54321"
            config = AppConfig.from_runtime("/tmp/runtime")
            self.assertEqual(config.connector_mode, "live")
            self.assertEqual(config.jira_base_url, "https://jira.example.com")
            self.assertEqual(config.jira_story_points_field, "customfield_54321")
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def test_from_runtime_enables_model_reasoning_when_llm_is_configured(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ["CODEX_CONFIG_PATH"] = "/tmp/does-not-exist.toml"
            os.environ["JIRA_ENHANCER_LLM_BASE_URL"] = "https://llm.example.com"
            os.environ["JIRA_ENHANCER_LLM_API_KEY"] = "secret"
            os.environ["JIRA_ENHANCER_LLM_MODEL"] = "test-model"
            os.environ["CONFLUENCE_MCP_ENDPOINT"] = "http://127.0.0.1:8765/mcp"
            os.environ["CODEX_BROKER_BASE_URL"] = "http://127.0.0.1:9000"
            config = AppConfig.from_runtime("/tmp/runtime")
            self.assertEqual(config.reasoning_mode, "model")
            self.assertEqual(config.llm_base_url, "https://llm.example.com")
            self.assertEqual(config.llm_model, "test-model")
            self.assertEqual(config.confluence_mcp_endpoint, "http://127.0.0.1:8765/mcp")
            self.assertEqual(config.codex_broker_base_url, "http://127.0.0.1:9000")
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def test_from_runtime_defaults_to_codex_exec_for_live_runs_when_available(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ["CODEX_CONFIG_PATH"] = "/tmp/does-not-exist.toml"
            os.environ["JIRA_ENHANCER_CONNECTOR_MODE"] = "live"
            with patch("shutil.which", return_value="/usr/local/bin/codex"):
                config = AppConfig.from_runtime("/tmp/runtime")
            self.assertTrue(config.use_codex_exec)
            self.assertEqual(config.codex_exec_command, "/usr/local/bin/codex")
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def test_from_runtime_reads_codex_config_and_server_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            jira_env = tmp_path / "example-jira.env"
            jira_env.write_text("JIRA_URL=https://jira.example.com\nJIRA_PERSONAL_TOKEN=jira-token\n", encoding="utf-8")
            bitbucket_env = tmp_path / "bitbucket.env"
            bitbucket_env.write_text("BITBUCKET_URL=https://bitbucket.example.com\nBITBUCKET_TOKEN=bb-token\n", encoding="utf-8")
            codex_config = tmp_path / "config.toml"
            codex_config.write_text(
                "\n".join(
                    [
                        "[mcp_servers.example-jira]",
                        f'args = ["mcp-atlassian", "--env-file", "{jira_env}"]',
                        "",
                        "[mcp_servers.bitbucket-mcp-server]",
                        f'args = ["node", "--env-file", "{bitbucket_env}"]',
                        "",
                        "[mcp_servers.central-confluence]",
                        'args = ["mcp-atlassian", "--confluence-url", "https://confluence.example.com", "--confluence-use-web-session"]',
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_CONFIG_PATH": str(codex_config)}, clear=False):
                config = AppConfig.from_runtime(tmp_path / "runtime")
            self.assertEqual(config.connector_mode, "live")
            self.assertEqual(config.jira_base_url, "https://jira.example.com")
            self.assertEqual(config.jira_token, "jira-token")
            self.assertEqual(config.bitbucket_base_url, "https://bitbucket.example.com")
            self.assertEqual(config.bitbucket_token, "bb-token")
            self.assertEqual(config.confluence_base_url, "https://confluence.example.com")
            self.assertTrue(config.confluence_use_web_session)
            self.assertEqual(config.confluence_mcp_server, "central-confluence")
            self.assertEqual(config.confluence_mcp_command, "")
            self.assertEqual(config.confluence_mcp_startup_timeout_sec, 300)
            self.assertEqual(config.confluence_mcp_request_timeout_sec, 20)


if __name__ == "__main__":
    unittest.main()
