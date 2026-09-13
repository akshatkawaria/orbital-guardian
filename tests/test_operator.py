def test_fuel_command():
    from app.integration.adapters import explanation
    result=explanation("parse_command",text="limit fuel to 1%")
    assert result["constraints"]["max_fuel_budget_pct_remaining"]==1
