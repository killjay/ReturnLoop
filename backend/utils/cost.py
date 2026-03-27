def estimate_shipping_cost(distance_miles: float) -> float:
    """Estimate shipping cost based on distance.

    Simple model: base cost + per-mile rate.
    Real shipping is more complex (weight, zones, carrier), but this
    gives realistic enough numbers for the demo.
    """
    base_cost = 3.50  # Base handling/pickup cost
    per_mile_rate = 0.035  # ~$0.035 per mile

    cost = base_cost + (distance_miles * per_mile_rate)
    return round(min(cost, 25.0), 2)  # Cap at $25 for ground shipping


def calculate_cost_savings(
    direct_miles: float,
    warehouse_miles: float
) -> dict:
    """Calculate cost savings from rerouting vs warehouse route."""
    direct_cost = estimate_shipping_cost(direct_miles)
    warehouse_cost = estimate_shipping_cost(warehouse_miles)
    saved = warehouse_cost - direct_cost

    return {
        "direct_cost": direct_cost,
        "warehouse_cost": warehouse_cost,
        "cost_saved": round(max(saved, 0), 2),
    }
