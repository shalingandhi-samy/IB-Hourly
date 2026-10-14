"""Drax building-overview field mappings for the inbound hourly report."""

# These labels/codes intentionally match the authenticated Drax page.
DRAX_KPI_MAPPINGS = {
    "IB Volume": {"source": "area_summary", "area": "Inbound", "field": "Volume"},
    "Drax Hours": {"source": "area_summary", "area": "Inbound", "field": "Hours"},
    "IB UPH": {"source": "area_summary", "area": "Inbound", "field": "UPH"},
    "Decant UPH": {
        "source": "department_summary",
        "department": "Decant",
        "department_id": "018156157",
        "field": "UPH",
    },
}
