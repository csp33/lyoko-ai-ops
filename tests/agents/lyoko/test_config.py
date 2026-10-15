"""Unit tests for LYOKO agent configuration and database URI generation."""

from lyoko.config import AgentSettings


def test_postgres_uri_not_set():
    settings = AgentSettings(postgres_password="")
    assert settings.get_postgres_uri() is None


def test_postgres_uri_constructed_from_components():
    settings = AgentSettings(
        postgres_host="pg.internal",
        postgres_port=5432,
        postgres_user="testuser",
        postgres_password="secretpassword",
        postgres_db="testdb",
    )
    assert (
        settings.get_postgres_uri()
        == "postgresql://testuser:secretpassword@pg.internal:5432/testdb"
    )


def test_postgres_uri_explicit_override():
    settings = AgentSettings(
        postgres_uri="postgresql://custom:custom@localhost:5433/customdb",
        postgres_password="ignoredpassword",
    )
    assert settings.get_postgres_uri() == "postgresql://custom:custom@localhost:5433/customdb"


def test_postgres_uri_with_special_characters():
    settings = AgentSettings(
        postgres_host="pg.internal",
        postgres_port=5432,
        postgres_user="user/name",
        postgres_password="p+w/d=123",
        postgres_db="testdb",
    )
    assert (
        settings.get_postgres_uri()
        == "postgresql://user%2Fname:p%2Bw%2Fd%3D123@pg.internal:5432/testdb"
    )


def test_alert_storm_settings_defaults():
    settings = AgentSettings(postgres_password="")
    assert settings.alert_debounce_seconds == 10.0
    assert settings.alert_storm_threshold == 8
    assert settings.alert_storm_window_seconds == 60
    assert settings.alert_storm_cooldown_seconds == 120
    assert settings.alert_dedup_cooldown_seconds == 300
    assert settings.max_concurrent_incidents == 2


def test_tool_budget_defaults():
    settings = AgentSettings(postgres_password="")
    assert settings.max_tool_calls_per_run == 25


def test_hermes_budget_settings_defaults():
    default_settings = AgentSettings(postgres_password="")
    assert default_settings.max_turns_diagnose == 15
    assert default_settings.max_turns_chat == 15
    assert default_settings.max_budget_usd_per_event == 0.10
    assert default_settings.cycle_detection_window == 2
