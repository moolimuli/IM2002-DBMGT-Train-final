import sys
sys.path.insert(0, '.')
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    query_user_profile,
    query_user_bookings,
    query_payment_info,
)
import json

def test(name, result):
    ok = result is not None and result != [] and result != {}
    print(f"{'✅' if ok else '❌'}  {name}")
    if ok:
        if isinstance(result, list):
            print(f"    → {len(result)} 筆結果")
            print(f"    → 第一筆: {json.dumps(dict(result[0]), default=str)[:120]}...")
        else:
            print(f"    → {json.dumps(dict(result), default=str)[:120]}...")
    print()

print("=" * 50)
print("TransitFlow Query Functions Test")
print("=" * 50 + "\n")

test("query_national_rail_availability (無日期)",
     query_national_rail_availability("NR01", "NR05"))

test("query_national_rail_availability (有日期)",
     query_national_rail_availability("NR01", "NR05", "2026-06-01"))

test("query_national_rail_fare",
     query_national_rail_fare("NR_SCH01", "standard", 4))

test("query_metro_schedules",
     query_metro_schedules("MS01", "MS09"))

test("query_metro_fare",
     query_metro_fare("MS_SCH03", 3))

test("query_available_seats",
     query_available_seats("NR_SCH01", "2026-06-01", "standard"))

test("query_user_profile",
     query_user_profile("alice.tan@email.com"))

test("query_user_bookings",
     query_user_bookings("alice.tan@email.com"))

test("query_payment_info",
     query_payment_info("BK001"))