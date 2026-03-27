import math

# Average CO2 per mile for ground shipping (kg)
CO2_PER_MILE_KG = 0.00041 * 2000  # ~0.82 kg per mile for a delivery truck segment
# Simplified: ~0.06 kg CO2 per mile per package
CO2_PER_PACKAGE_MILE_KG = 0.06


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in miles using Haversine formula."""
    R = 3959  # Earth's radius in miles

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_co2_saved(direct_miles: float, warehouse_miles: float) -> float:
    """Calculate CO2 saved in kg by rerouting instead of going through warehouse."""
    return (warehouse_miles - direct_miles) * CO2_PER_PACKAGE_MILE_KG


def calculate_distance_saved(
    source_lat: float, source_lon: float,
    target_lat: float, target_lon: float,
    warehouse_lat: float = 39.8283, warehouse_lon: float = -98.5795
) -> dict:
    """Calculate distance savings comparing direct reroute vs warehouse route.

    Default warehouse is central US (geographic center of contiguous US).
    Returns dict with direct_miles, warehouse_miles, miles_saved, co2_saved_kg.
    """
    direct_miles = haversine_distance(source_lat, source_lon, target_lat, target_lon)
    # Warehouse route: source → warehouse + warehouse → target
    to_warehouse = haversine_distance(source_lat, source_lon, warehouse_lat, warehouse_lon)
    from_warehouse = haversine_distance(warehouse_lat, warehouse_lon, target_lat, target_lon)
    warehouse_miles = to_warehouse + from_warehouse

    miles_saved = warehouse_miles - direct_miles
    co2_saved = calculate_co2_saved(direct_miles, warehouse_miles)

    return {
        "direct_miles": round(direct_miles, 1),
        "warehouse_miles": round(warehouse_miles, 1),
        "miles_saved": round(miles_saved, 1),
        "co2_saved_kg": round(co2_saved, 2),
    }
