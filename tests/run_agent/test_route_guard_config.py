from agent.route_guard import RouteGuardConfig
from hermes_cli.config import DEFAULT_CONFIG


def test_route_guard_self_improvement_defaults_off_and_token_safe():
    config = DEFAULT_CONFIG["route_guard"]

    assert config["enabled"] is False
    assert config["judge"]["enabled"] is False
    assert config["judge"]["self_improvement_enabled"] is False
    assert config["judge"]["self_improvement_llm_on_success"] is False
    assert config["metrics"]["dashboard_llm_summary"] is False
    assert config["self_improvement"]["enabled"] is False
    assert config["self_improvement"]["ordinary_success_llm_calls_allowed"] == 0


def test_route_guard_runtime_config_parses_self_improvement_budget():
    cfg = RouteGuardConfig.from_mapping({
        "enabled": True,
        "mode": "warn",
        "self_improvement": {
            "enabled": True,
            "ordinary_success_llm_calls_allowed": 0,
            "max_synthesis_per_routed_turn_window": 1,
            "synthesis_window_routed_turns": 25,
        },
    })

    assert cfg.self_improvement.enabled is True
    assert cfg.self_improvement.ordinary_success_llm_calls_allowed == 0
    assert cfg.self_improvement.max_synthesis_per_routed_turn_window == 1
    assert cfg.self_improvement.synthesis_window_routed_turns == 25
