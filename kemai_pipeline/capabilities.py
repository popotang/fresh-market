"""Source facts used by planners, independently of either CLI transport."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capability:
    stream: str
    table: str | None
    date_field: str | None
    source_key_fields: tuple[str, ...]
    source_key_version: str
    configured_from: str | None
    granularity: str = "day"
    export_role: str = "none"
    paused: bool = False

    def to_dict(self):
        return {**asdict(self), "baseline_verified": False,
                "max_source_rate": 0.5, "cooldown_seconds": 1800,
                "cost_observations": [], "cost_confidence": "unknown"}


CAPABILITIES = {
    "retail-sales": Capability("retail-sales", "retail_sale_flows", "source_created_at",
                               ("id",), "source-id-v1", "2026-08-01", export_role="count_only"),
    "retail-orders": Capability("retail-orders", "retail_orders", "source_created_at",
                                ("id",), "source-id-v1", "2026-08-01", export_role="count_only"),
}
for stream, baseline in (("purchase-orders", "2025-01-01"),
                         ("purchase-receipts", "2026-08-08"),
                         ("purchase-returns", "2026-01-01")):
    CAPABILITIES[stream] = Capability(stream, "purchase_documents", "source_created_at",
                                    ("document_kind", "id"), "purchase-id-v1", baseline)
CAPABILITIES["purchase-receive-history"] = Capability(
    "purchase-receive-history", "purchase_receipt_history_lines", "approved_at",
    ("sheetNo", "normalized_sequence", "itemCode_or_itemId"),
    "history-decimal-sequence-v2", "2025-01-01", export_role="data_with_validated_mapping")
CAPABILITIES["item_list"] = Capability("item_list", "products", None,
    ("id",), "source-id-v1", None, granularity="catalog", export_role="base_fields_candidate")
CAPABILITIES["item_detail"] = Capability("item_detail", "product_profile", None,
    ("item_id",), "item-object-v1", None, granularity="object")
for tab in ("supply", "retail_price", "delivery_price", "stock", "promotion", "barcode",
            "grading", "pack", "history_price", "scope", "combination", "mix", "split", "cut"):
    name = f"item_tab_{tab}"
    CAPABILITIES[name] = Capability(name, "product_child_records", None,
        ("item_id", "section", "source_child_key"), "section-specific-legacy-v1",
        None, granularity="object")
for name in ("retail-order-detail", "retail-payments"):
    CAPABILITIES[name] = Capability(name, None, None, ("order_id", "child_id"),
        "detail-id-v1", "2026-08-01", granularity="object", paused=True)


def capture_method(stream, *, provisional=False, export_mapping_verified=False):
    capability = CAPABILITIES[stream]
    if capability.paused:
        raise ValueError(f"{stream}: paused_by_user")
    if capability.export_role == "count_only":
        return "api_only_provisional" if provisional else "api_for_data+export_for_count"
    if capability.export_role == "data_with_validated_mapping" and export_mapping_verified:
        return "export_for_data"
    return "detail_api" if capability.granularity == "object" else "api_only"
