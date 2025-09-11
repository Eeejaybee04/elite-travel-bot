from typing import List, Dict, Any

def compute_pricing(
    tickets: List[Dict[str, Any]],
    convenience_fee_pct: float = 0.088,
    commission_map: Dict[str, float] = None,
    airline_name_map: Dict[str, str] = None,
) -> List[Dict[str, Any]]:
    """
    Compute pricing details per ticket using agency business rules.
    - Commission applies to BASE FARE only (not taxes/fees).
    - BASE FARE = TOTAL_DOC - TAX - FEE (FEE is typically 0 in the report).
    - Convenience fee is charged to the customer on top of TOTAL_DOC.
    - Agency remits TOTAL_DOC minus commission to IATA (convenience fee is kept by agency).
    """

    if commission_map is None:
        commission_map = {
            "626": 0.025,  # Air Niugini (PX)
            "656": 0.05,   # PNG Air (CG)
        }
    if airline_name_map is None:
        airline_name_map = {
            "626": "Air Niugini (PX)",
            "656": "PNG Air (CG)",
        }

    enriched = []
    for t in tickets:
        airline_code = str(t.get("AIRLINE", "")).strip()
        total_doc = float(t.get("TOTAL_DOC") or 0.0)
        tax = float(t.get("TAX") or 0.0)
        fee = float(t.get("FEE") or 0.0)

        base_fare = max(total_doc - tax - fee, 0.0)
        commission_rate = float(commission_map.get(airline_code, 0.0))
        commission_amount = round(base_fare * commission_rate, 2)

        convenience_fee_amount = round(total_doc * convenience_fee_pct, 2)
        customer_total_with_fee = round(total_doc + convenience_fee_amount, 2)
        iata_settlement = round(total_doc - commission_amount, 2)

        agency_gross_revenue = round(commission_amount + convenience_fee_amount, 2)

        enriched.append({
            **t,
            "AIRLINE_NAME": airline_name_map.get(airline_code, airline_code),
            "BASE_FARE": round(base_fare, 2),
            "COMMISSION_RATE": commission_rate,
            "COMMISSION_AMOUNT": commission_amount,
            "CONVENIENCE_FEE_RATE": convenience_fee_pct,
            "CONVENIENCE_FEE_AMOUNT": convenience_fee_amount,
            "CUSTOMER_TOTAL_WITH_FEE": customer_total_with_fee,
            "IATA_SETTLEMENT": iata_settlement,
            "AGENCY_GROSS_REVENUE": agency_gross_revenue
        })

    return enriched


def summarize(enriched_tickets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate useful business metrics for reporting."""
    total_sales = sum(t.get("TOTAL_DOC", 0.0) for t in enriched_tickets)
    total_tax = sum(t.get("TAX", 0.0) for t in enriched_tickets)
    total_commission = sum(t.get("COMMISSION_AMOUNT", 0.0) for t in enriched_tickets)
    total_conv_fee = sum(t.get("CONVENIENCE_FEE_AMOUNT", 0.0) for t in enriched_tickets)
    total_agency_rev = sum(t.get("AGENCY_GROSS_REVENUE", 0.0) for t in enriched_tickets)
    total_iata_settlement = sum(t.get("IATA_SETTLEMENT", 0.0) for t in enriched_tickets)

    airline_breakdown = {}
    for t in enriched_tickets:
        code = str(t.get("AIRLINE", "")).strip()
        ab = airline_breakdown.setdefault(code, {
            "tickets": 0,
            "sales": 0.0,
            "tax": 0.0,
            "commission": 0.0,
            "conv_fee": 0.0,
            "agency_rev": 0.0,
            "iata_settlement": 0.0,
        })
        ab["tickets"] += 1
        ab["sales"] += t.get("TOTAL_DOC", 0.0)
        ab["tax"] += t.get("TAX", 0.0)
        ab["commission"] += t.get("COMMISSION_AMOUNT", 0.0)
        ab["conv_fee"] += t.get("CONVENIENCE_FEE_AMOUNT", 0.0)
        ab["agency_rev"] += t.get("AGENCY_GROSS_REVENUE", 0.0)
        ab["iata_settlement"] += t.get("IATA_SETTLEMENT", 0.0)

    return {
        "tickets": len(enriched_tickets),
        "total_sales": round(total_sales, 2),
        "total_tax": round(total_tax, 2),
        "total_commission": round(total_commission, 2),
        "total_convenience_fee": round(total_conv_fee, 2),
        "total_agency_revenue": round(total_agency_rev, 2),
        "total_iata_settlement": round(total_iata_settlement, 2),
        "by_airline": {
            code: {k: round(v, 2) if isinstance(v, float) else v for k, v in stats.items()}
            for code, stats in airline_breakdown.items()
        }
    }
