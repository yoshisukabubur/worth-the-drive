def calculate_net_savings(
    current_price: float,
    target_price: float,
    distance_miles: float,
    car_mpg: float,
    gallons_needed: float,
) -> float:
    """
    Calculate net savings (USD) when driving to a cheaper gas station.

    Args:
        current_price: Price at the nearest station ($/gallon).
        target_price: Price at the cheaper (target) station ($/gallon).
        distance_miles: One-way distance to the target station (miles).
        car_mpg: Vehicle fuel efficiency (miles per gallon).
        gallons_needed: Planned fuel purchase amount (gallons).

    Returns:
        Net savings in USD, rounded to 2 decimal places.
    """
    simple_savings = (current_price - target_price) * gallons_needed
    round_trip_gallons = (distance_miles * 2) / car_mpg
    round_trip_cost = round_trip_gallons * target_price
    net_savings = simple_savings - round_trip_cost
    return round(net_savings, 2)


def calculate_net_savings_one_way_detour(
    current_price: float,
    target_price: float,
    distance_miles: float,
    car_mpg: float,
    gallons_needed: float,
) -> float:
    """
    Net savings when you detour one-way to the station (e.g. continue onward),
    so extra fuel is only the one-way extra distance, not a full round trip.
    """
    simple_savings = (current_price - target_price) * gallons_needed
    extra_gallons = distance_miles / car_mpg
    trip_cost = extra_gallons * target_price
    return round(simple_savings - trip_cost, 2)


def break_even_distance_one_way(
    current_price: float,
    target_price: float,
    car_mpg: float,
    gallons_needed: float,
) -> float:
    """One-way detour distance where net savings becomes zero."""
    if target_price <= 0 or car_mpg <= 0 or gallons_needed <= 0:
        return 0.0
    simple = (current_price - target_price) * gallons_needed
    if simple <= 0:
        return 0.0
    return (simple * car_mpg) / target_price


if __name__ == "__main__":
    # Example / quick manual test:
    # nearest $3.50, target $3.30, 5 miles one-way, 25 MPG, buy 15 gallons
    result = calculate_net_savings(
        current_price=3.50,
        target_price=3.30,
        distance_miles=5.0,
        car_mpg=25.0,
        gallons_needed=15.0,
    )
    print(f"Net Savings: ${result}")

