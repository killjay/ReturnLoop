"""Aerospike client for real-time geospatial matching and state management."""
import json
from typing import List, Optional
from backend.config import get_settings

settings = get_settings()

# Try to import aerospike, fall back to in-memory mock
try:
    import aerospike
    from aerospike import GeoJSON
    HAS_AEROSPIKE = True
except ImportError:
    HAS_AEROSPIKE = False


class AerospikeClient:
    """Manages geospatial queries for the Loop Matcher agent.

    Falls back to in-memory storage if Aerospike is not available,
    so the demo works without the Aerospike container.
    """

    def __init__(self):
        self._client = None
        self._memory_store = {}  # Fallback in-memory store
        self._connected = False

    def connect(self):
        if not HAS_AEROSPIKE:
            print("Aerospike SDK not available, using in-memory fallback")
            return

        try:
            config = {
                "hosts": [(settings.aerospike_host, settings.aerospike_port)],
            }
            self._client = aerospike.client(config).connect()
            self._connected = True
            print("Connected to Aerospike")
        except Exception as e:
            print(f"Aerospike connection failed, using in-memory fallback: {e}")
            self._connected = False

    async def store_active_order(self, order_id: str, data: dict):
        """Store an active order for geospatial matching."""
        key = ("returnloop", "active_orders", order_id)

        if self._connected and self._client:
            try:
                geo_data = GeoJSON({
                    "type": "Point",
                    "coordinates": [data["longitude"], data["latitude"]]
                })
                bins = {
                    "customer_id": data["customer_id"],
                    "product_sku": data["product_sku"],
                    "size": data["size"],
                    "latitude": data["latitude"],
                    "longitude": data["longitude"],
                    "status": data["status"],
                    "geo_bin": geo_data,
                    "order_id": order_id,
                }
                self._client.put(key, bins)
            except Exception as e:
                print(f"Aerospike store error: {e}")
                self._store_in_memory("active_orders", order_id, data)
        else:
            self._store_in_memory("active_orders", order_id, data)

    async def find_nearby_orders(
        self,
        product_sku: str,
        size: str,
        latitude: float,
        longitude: float,
        radius_miles: float = 2500.0,
        exclude_customer_id: str = None,
    ) -> List[dict]:
        """Find nearby orders for the same product SKU within radius.

        This is the core geospatial query for the Loop Matcher.
        """
        radius_meters = radius_miles * 1609.34  # Convert miles to meters

        if self._connected and self._client:
            try:
                query = self._client.query("returnloop", "active_orders")
                query.where(
                    aerospike.predicates.geo_within_radius(
                        "geo_bin", longitude, latitude, radius_meters
                    )
                )
                results = []
                def callback(record):
                    _, _, bins = record
                    if (bins.get("product_sku") == product_sku and
                        bins.get("size") == size and
                        bins.get("customer_id") != exclude_customer_id and
                        bins.get("status") in ("pending", "shipped")):
                        results.append(bins)

                query.foreach(callback)
                return results
            except Exception as e:
                print(f"Aerospike query error: {e}")
                return self._find_in_memory(product_sku, size, latitude, longitude, radius_miles, exclude_customer_id)
        else:
            return self._find_in_memory(product_sku, size, latitude, longitude, radius_miles, exclude_customer_id)

    async def store_customer_risk(self, customer_id: str, data: dict):
        """Cache customer risk score for fast lookups."""
        if self._connected and self._client:
            try:
                key = ("returnloop", "customer_risk", customer_id)
                self._client.put(key, data)
            except Exception:
                self._store_in_memory("customer_risk", customer_id, data)
        else:
            self._store_in_memory("customer_risk", customer_id, data)

    async def get_customer_risk(self, customer_id: str) -> Optional[dict]:
        """Get cached customer risk score."""
        if self._connected and self._client:
            try:
                key = ("returnloop", "customer_risk", customer_id)
                _, _, bins = self._client.get(key)
                return bins
            except Exception:
                return self._get_from_memory("customer_risk", customer_id)
        return self._get_from_memory("customer_risk", customer_id)

    # In-memory fallback methods
    def _store_in_memory(self, set_name: str, key: str, data: dict):
        if set_name not in self._memory_store:
            self._memory_store[set_name] = {}
        self._memory_store[set_name][key] = data

    def _get_from_memory(self, set_name: str, key: str) -> Optional[dict]:
        return self._memory_store.get(set_name, {}).get(key)

    def _find_in_memory(
        self, product_sku: str, size: str,
        lat: float, lon: float, radius_miles: float,
        exclude_customer_id: str = None,
    ) -> List[dict]:
        from backend.utils.geo import haversine_distance

        results = []
        orders = self._memory_store.get("active_orders", {})
        for order_id, data in orders.items():
            if (data.get("product_sku") == product_sku and
                data.get("size") == size and
                data.get("customer_id") != exclude_customer_id and
                data.get("status") in ("pending", "shipped")):
                dist = haversine_distance(lat, lon, data["latitude"], data["longitude"])
                if dist <= radius_miles:
                    data_copy = dict(data)
                    data_copy["distance_miles"] = round(dist, 1)
                    data_copy["order_id"] = order_id
                    results.append(data_copy)

        results.sort(key=lambda x: x.get("distance_miles", 999))
        return results


# Singleton
aerospike_client = AerospikeClient()
