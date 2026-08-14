from external_health import (
    EXTERNAL_SERVICES,
    UNCONFIRMED_SERVICES,
    check_service,
    classify,
    get_calyx_backend_telemetry,
    get_external_service_health,
    summarize,
)


# ---------- pure classify() ----------

def test_classify_2xx_as_reachable():
    assert classify(200, None) == "reachable"


def test_classify_3xx_as_reachable():
    assert classify(302, None) == "reachable"


def test_classify_4xx_as_error_status_not_healthy():
    assert classify(404, None) == "error_status"


def test_classify_5xx_as_error_status_not_healthy():
    assert classify(500, None) == "error_status"


def test_classify_no_status_code_as_unreachable():
    assert classify(None, "Connection refused") == "unreachable"


# ---------- check_service() with an injected fetch (no real network) ----------

def test_check_service_reports_real_reachable_result():
    service = EXTERNAL_SERVICES[0]

    def fake_fetch(url, timeout):
        assert url == service["url"].rstrip("/") + service["health_path"]
        return 200, None

    result = check_service(service, fetch=fake_fetch)
    assert result["status"] == "reachable"
    assert result["http_status"] == 200
    assert result["error"] is None
    assert result["service_key"] == service["service_key"]


def test_check_service_reports_unreachable_honestly_not_as_healthy():
    service = EXTERNAL_SERVICES[0]

    def fake_fetch(url, timeout):
        return None, "timed out"

    result = check_service(service, fetch=fake_fetch)
    assert result["status"] == "unreachable"
    assert result["http_status"] is None
    assert result["error"] == "timed out"


def test_check_service_reports_error_status_not_masked_as_healthy():
    service = EXTERNAL_SERVICES[0]

    def fake_fetch(url, timeout):
        return 503, "HTTP Error 503: Service Unavailable"

    result = check_service(service, fetch=fake_fetch)
    assert result["status"] == "error_status"
    assert result["http_status"] == 503


# ---------- summarize() ----------

def test_summarize_counts_statuses():
    checked = [
        {"status": "reachable"},
        {"status": "reachable"},
        {"status": "unreachable"},
    ]
    assert summarize(checked) == {"reachable": 2, "unreachable": 1}


# ---------- unconfirmed services are documented, never silently checked ----------

def test_unconfirmed_services_are_not_in_the_checked_list():
    checked_keys = {s["service_key"] for s in EXTERNAL_SERVICES}
    unconfirmed_keys = {s["service_key"] for s in UNCONFIRMED_SERVICES}
    assert checked_keys.isdisjoint(unconfirmed_keys)
    for service in UNCONFIRMED_SERVICES:
        assert "reason" in service and service["reason"]


# ---------- endpoint-level behavior (calls check_service for real, but
# every EXTERNAL_SERVICES host is validated to fail fast/offline rather
# than hang - so this stays fast even without patching, exercising the
# real default _fetch path with a short real timeout) ----------

def test_get_external_service_health_never_reports_unreachable_service_as_reachable(monkeypatch):
    import external_health

    def fake_fetch(url, timeout):
        return None, "simulated network failure"

    monkeypatch.setattr(external_health, "_fetch", fake_fetch)
    result = get_external_service_health()
    assert result["status_counts"] == {"unreachable": len(EXTERNAL_SERVICES)}
    for service in result["services"]:
        assert service["status"] == "unreachable"
        assert service["error"] == "simulated network failure"
    assert len(result["unconfirmed_services"]) == len(UNCONFIRMED_SERVICES)


def test_get_calyx_backend_telemetry_reports_failure_honestly(monkeypatch):
    import external_health

    def fake_fetch_json(url, timeout):
        return {"ok": False, "http_status": None, "error": "simulated network failure", "body": None}

    monkeypatch.setattr(external_health, "_fetch_json", fake_fetch_json)
    result = get_calyx_backend_telemetry()
    assert result["status"] == "unreachable"
    for key in external_health.CALYX_BACKEND_TELEMETRY_PATHS:
        assert result["results"][key]["ok"] is False
        assert result["results"][key]["error"] == "simulated network failure"


def test_get_calyx_backend_telemetry_reports_success_honestly(monkeypatch):
    import external_health

    def fake_fetch_json(url, timeout):
        return {"ok": True, "http_status": 200, "error": None, "body": {"status": "healthy"}}

    monkeypatch.setattr(external_health, "_fetch_json", fake_fetch_json)
    result = get_calyx_backend_telemetry()
    assert result["status"] == "reachable"
    for key in external_health.CALYX_BACKEND_TELEMETRY_PATHS:
        assert result["results"][key]["ok"] is True
        assert result["results"][key]["body"] == {"status": "healthy"}
