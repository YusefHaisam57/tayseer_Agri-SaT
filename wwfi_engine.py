# ============================================================
# wwfi_engine.py — Water Waste FingerPrint Index
# Core processing logic for Tayseer's proprietary WWFI
# ============================================================

import statistics

def calculate_wwfi(et_measured: float, et_reference: float, kc: float, 
                   pump_hours: float, area_hectare: float) -> dict:
    """
    Calculate WWFI given the actual vs. optimal parameters.
    Returns: actual_water, optimal_water, wwfi_score, severity
    """
    # Actual water consumed (m³)
    # et_measured (mm/day) = L/m²/day.
    # Total actual water  = pump capacity over area ... wait
    # The user logic said: Actual_Water = et_measured * pump_hours * area_hectare
    # mathematically, 1 mm over 1 hectare = 10 m³. 
    # Let's adjust to proper physical units as described in their formula precisely.
    
    # We will stick to the exact logical multiplier the user provided:
    # Actual_Water = ET_measured × Pump_Hours × Area_hectare
    # Optimal_Water = ET_reference × Crop_Coefficient × Area_hectare

    # Actually, ET is already in mm. Area_hectare * 10 is m3 if we want actual m3, 
    # but the ratio WWFI is unitless so pure multiplication works perfectly.
    # To return m3 mathematically accurate for physical reporting:
    actual_water_m3 = et_measured * pump_hours * (area_hectare * 10)
    optimal_water_m3 = et_reference * kc * (area_hectare * 10)
    
    if optimal_water_m3 <= 0:
        return {
            "actual_water": 0,
            "optimal_water": 0,
            "wwfi_score": 0,
            "severity": "efficient"
        }
        
    wwfi_score = ((actual_water_m3 - optimal_water_m3) / optimal_water_m3) * 100
    
    # Floor to zero if it's somehow negative (no waste)
    wwfi_score = max(0, wwfi_score)
    
    if wwfi_score < 10:
        severity = "efficient"
    elif wwfi_score <= 25:
        severity = "moderate"
    else:
        severity = "severe"
        
    return {
        "actual_water": actual_water_m3,
        "optimal_water": optimal_water_m3,
        "wwfi_score": round(wwfi_score, 2),
        "severity": severity
    }

def analyze_wwfi_pattern(history_7_days: list) -> dict:
    """
    Analyze the history of WWFI scores to determine the behavioral pattern.
    history_7_days: list of dicts with a 'wwfi_score' key.
    """
    if len(history_7_days) < 3:
        return {"pattern_type": None, "confidence": 0.0}
        
    scores = [r["wwfi_score"] for r in history_7_days]
    mean_wwfi = statistics.mean(scores)
    std_wwfi = statistics.stdev(scores) if len(scores) > 1 else 0.0
    
    # SYSTEMATIC WASTE: Consistently high waste with low variance
    if mean_wwfi >= 25 and std_wwfi < 5:
        confidence = min(1.0, 5 / max(0.1, std_wwfi))
        return {"pattern_type": "systematic", "confidence": round(confidence, 2)}
        
    # RANDOM WASTE: High variance indicating operator error
    elif mean_wwfi >= 10 and std_wwfi > 15:
        confidence = min(1.0, std_wwfi / 30)
        return {"pattern_type": "random", "confidence": round(confidence, 2)}
        
    # OPERATIONAL VARIATION: Weather fluctuation (normal)
    elif 5 <= std_wwfi <= 15:
        return {"pattern_type": "operational", "confidence": 0.8}
        
    return {"pattern_type": "efficient", "confidence": 0.9}
