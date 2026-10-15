"""Domain models and alias mappings for upstream MCP integrations."""

UPSTREAM_ALIASES: dict[str, str] = {
    # UniFi / Network
    "network": "unifi",
    "networking": "unifi",
    "wifi": "unifi",
    "router": "unifi",
    "switch": "unifi",
    "ap": "unifi",
    # Kubernetes
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "cluster": "kubernetes",
    "pod": "kubernetes",
    "workload": "kubernetes",
    # Home Assistant
    "ha": "homeassistant",
    "hass": "homeassistant",
    "iot": "homeassistant",
    "smart_home": "homeassistant",
    "domotica": "homeassistant",
    # Grafana / Prometheus
    "grafana": "grafana",
    "prometheus": "grafana",
    "metrics": "grafana",
    "monitoring": "grafana",
    "alerts": "grafana",
    # GitHub
    "gh": "github",
    "git": "github",
    "repo": "github",
    # Telegram notifications
    "telegram": "telegram",
    "notify": "telegram",
    "notifications": "telegram",
    # Argo CD / GitOps live as Kubernetes CRDs (argoproj.io)
    "gitops": "kubernetes",
    "argocd": "kubernetes",
    "argo": "kubernetes",
    # Langfuse / LLM Observability
    "langfuse": "langfuse",
    "llm_observability": "langfuse",
    "traces": "langfuse",
}

# Common LLM tool hallucination / alias mappings
TOOL_ALIASES: dict[str, str] = {
    # UniFi
    "unifi_get_devices": "unifi_list_devices",
    "unifi_get_clients": "unifi_list_clients",
    "unifi_get_networks": "unifi_list_networks",
    "unifi_get_wlans": "unifi_list_wlans",
    "unifi_get_firewall_rules": "unifi_list_firewall_rules",
    "unifi_get_port_profiles": "unifi_list_port_profiles",
    # Kubernetes
    "kubectl": "k8s_kubectl",
    "kubernetes_kubectl": "k8s_kubectl",
    "k8s_run_kubectl": "k8s_kubectl",
    "k8s_get_resources": "k8s_resources_get",
    "k8s_get_resource": "k8s_resources_get",
    "k8s_list_resources": "k8s_resources_list",
    "k8s_get_application": "k8s_resources_get",
    "k8s_get_applications": "k8s_resources_list",
    "k8s_list_applications": "k8s_resources_list",
    "k8s_get_deployment": "k8s_resources_get",
    "k8s_get_deployments": "k8s_resources_list",
    "k8s_list_deployments": "k8s_resources_list",
    "argocd_get_application": "k8s_resources_get",
    "argocd_get_applications": "k8s_resources_list",
    "argocd_list_applications": "k8s_resources_list",
    "argocd_application_get": "k8s_resources_get",
    "argocd_application_list": "k8s_resources_list",
    "argo_get_application": "k8s_resources_get",
    "argo_get_applications": "k8s_resources_list",
    "argo_list_applications": "k8s_resources_list",
    "k8s_delete_resources": "k8s_resources_delete",
    "k8s_delete_resource": "k8s_resources_delete",
    "k8s_scale_resources": "k8s_resources_scale",
    "k8s_scale_resource": "k8s_resources_scale",
    "k8s_scale": "k8s_resources_scale",
    "k8s_apply_resources": "k8s_resources_create_or_update",
    "k8s_apply_resource": "k8s_resources_create_or_update",
    "k8s_create_or_update_resource": "k8s_resources_create_or_update",
    "k8s_create_or_update_resources": "k8s_resources_create_or_update",
    "k8s_patch_resource": "k8s_resources_create_or_update",
    "k8s_patch_resources": "k8s_resources_create_or_update",
    "k8s_patch_namespaced_custom_object": "k8s_resources_create_or_update",
    "k8s_create_namespaced_custom_object": "k8s_resources_create_or_update",
    "k8s_replace_namespaced_custom_object": "k8s_resources_create_or_update",
    "k8s_patch_custom_object": "k8s_resources_create_or_update",
    "k8s_create_custom_object": "k8s_resources_create_or_update",
    "k8s_get_pod": "k8s_pods_get",
    "k8s_get_pods": "k8s_pods_list",
    "k8s_list_pods": "k8s_pods_list",
    "k8s_get_logs": "k8s_pods_log",
    "k8s_get_events": "k8s_events_list",
    "k8s_list_events": "k8s_events_list",
    "k8s_get_namespaces": "k8s_namespaces_list",
    "k8s_list_namespaces": "k8s_namespaces_list",
    # Home Assistant
    "ha_list_entities": "ha_get_states",
    "ha_get_entities": "ha_get_states",
    # Grafana / Prometheus
    "grafana_query": "grafana_query_prometheus",
    "grafana_data_query": "grafana_query_prometheus",
    "grafana_query_data": "grafana_query_prometheus",
    "grafana_prometheus_query": "grafana_query_prometheus",
    "grafana_promql": "grafana_query_prometheus",
    "grafana_query_promql": "grafana_query_prometheus",
    "grafana_get_datasources": "grafana_list_datasources",
    "grafana_get_datasource_list": "grafana_list_datasources",
    "grafana_get_alerts": "grafana_alerting_rules_read",
    "grafana_list_alerts": "grafana_alerting_rules_read",
    "prometheus_query": "grafana_query_prometheus",
    "prometheus_data_query": "grafana_query_prometheus",
    "prometheus_promql": "grafana_query_prometheus",
    "promql_query": "grafana_query_prometheus",
    "query_prometheus": "grafana_query_prometheus",
}


def resolve_canonical_domain(domain: str) -> str:
    """Map a raw domain string or alias to its canonical upstream identifier."""
    raw = domain.strip().lower()
    return UPSTREAM_ALIASES.get(raw, raw)


def resolve_tool_alias(name: str) -> str:
    """Map common hallucinated tool names or variants to their canonical tool name."""
    raw = name.strip()
    if raw.startswith("kubernetes_"):
        raw = "k8s_" + raw.removeprefix("kubernetes_")
    return TOOL_ALIASES.get(raw, raw)
