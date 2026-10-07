#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import io

# Force print to use utf-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from flask import Blueprint, jsonify, request
try:
    import ee
    _EE_AVAILABLE = True
except ImportError:
    ee = None
    _EE_AVAILABLE = False
import json
from datetime import datetime, timedelta
import threading
import time
import wwfi_engine
import database

gee_bp = Blueprint('gee', __name__, url_prefix='/api/gee')

def initialize_gee():
    if not _EE_AVAILABLE or ee is None:
        print("[GEE] earthengine-api not installed. GEE features disabled.")
        return False
    try:
        import os
        try:
            ee.Initialize()
            print("GEE initialized with default credentials")
        except ee.ee_exception.EEException as gee_error:
            if "no project found" in str(gee_error):
                project_id = os.environ.get('GEE_PROJECT_ID', 'tayseer-agri-sat')
                ee.Initialize(project=project_id)
                print(f"GEE initialized with project: {project_id}")
            else:
                raise
        return True
    except Exception as e:
        print(f"GEE initialization error: {str(e)}")
        return False

GEE_READY = initialize_gee()

# STORE RAW COORDINATES INSTEAD OF EE GEOMETRIES TO PREVENT CRASH
FARMS = {
    'el_qazzaz': {
        'name': 'مزرعة القزاز - الإسماعيلية',
        'name_en': 'Al-Qazzaz Farm - Ismailia',
        'polygon_coords': [
            [
                [32.257488, 30.638237], 
                [32.255718, 30.637544], 
                [32.263368, 30.633748], 
                [32.263754, 30.634320], 
                [32.256297, 30.638262]
            ]
        ],
        'area_feddan': 15,
        'crop': 'أشجار الفاكهة',
        'center': [32.260, 30.635]
    },
    
    'wadi_natrun': {
        'name': 'مزرعة وادي النطرون',
        'name_en': 'Wadi Natrun Farm',
        'pivots': [
            {'id': 'Pivot_01', 'center': [30.058015, 30.205279], 'area': 70},
            {'id': 'Pivot_02_03', 'center': [30.053554, 30.199594], 'area': 140},
            {'id': 'Pivot_04', 'center': [30.063942, 30.201508], 'area': 70},
            {'id': 'Pivot_05', 'center': [30.059441, 30.195975], 'area': 70},
            {'id': 'Pivot_06', 'center': [30.054934, 30.190478], 'area': 70},
            {'id': 'Pivot_08', 'center': [30.065354, 30.192302], 'area': 70},
            {'id': 'Pivot_09', 'center': [30.060910, 30.186869], 'area': 70},
            {'id': 'Pivot_10', 'center': [30.066835, 30.183160], 'area': 70},
            {'id': 'Pivot_11', 'center': [30.082541, 30.185950], 'area': 70},
            {'id': 'Pivot_12_A', 'center': [30.081329, 30.179346], 'area': 70},
            {'id': 'Pivot_12_B', 'center': [30.088495, 30.182244], 'area': 70},
            {'id': 'Pivot_13', 'center': [30.087695, 30.175251], 'area': 70},
            {'id': 'Pivot_16', 'center': [30.100552, 30.208172], 'area': 70},
            {'id': 'Pivot_19', 'center': [30.096037, 30.202500], 'area': 70},
            {'id': 'Pivot_21', 'center': [30.081994, 30.206645], 'area': 70},
            {'id': 'Pivot_22', 'center': [30.073290, 30.208443], 'area': 70}
        ],
        'total_area_feddan': 1000,
        'crop': 'محاصيل حقلية',
        'center': [30.070, 30.200]
    }
}

def get_farm_geometry(farm_id):
    """Lazy initialization of ee.Geometry to prevent startup crash"""
    if not GEE_READY or farm_id not in FARMS:
        return None
    
    farm = FARMS[farm_id]
    
    if 'polygon_coords' in farm:
        return ee.Geometry.Polygon(farm['polygon_coords'])
    
    if 'pivots' in farm:
        # Create a single bounding box/polygon containing all points
        coords = [p['center'] for p in farm['pivots']]
        return ee.Geometry.MultiPoint(coords).buffer(500).bounds()
    
    return None

def get_pivot_halves(farm_id):
    """Generates AB and CD halves for pivots just like the user's Javascript logic"""
    if not GEE_READY or farm_id not in FARMS:
        return None
    
    farm = FARMS[farm_id]
    if 'pivots' not in farm:
        return None
        
    halves = []
    for p in farm['pivots']:
        pt = ee.Geometry.Point(p['center'])
        circle = pt.buffer(300) # 70-feddan pivot radius
        lon, lat = p['center']
        delta = 0.005
        
        west_box = ee.Geometry.Rectangle([lon - delta, lat - delta, lon, lat + delta])
        east_box = ee.Geometry.Rectangle([lon, lat - delta, lon + delta, lat + delta])
        
        half_ab = ee.Feature(circle.intersection(west_box, 1)).set('Pivot_Half_ID', f"{p['id']}_AB")
        half_cd = ee.Feature(circle.intersection(east_box, 1)).set('Pivot_Half_ID', f"{p['id']}_CD")
        
        halves.extend([half_ab, half_cd])
        
    return ee.FeatureCollection(halves)

def get_sentinel2_ndvi(polygon, start_date, end_date):
    try:
        if not GEE_READY:
            return None
        
        s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(polygon) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
            .map(lambda img: img.updateMask(
                img.select('QA60').bitwiseAnd(1<<10).eq(0).And(
                    img.select('QA60').bitwiseAnd(1<<11).eq(0)
                )
            )).first().clip(polygon)
        
        if s2 is None:
            return None
        
        ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
        ndwi = s2.normalizedDifference(['B8', 'B11']).rename('NDWI')
        biomass = ndvi.multiply(2500).rename('Biomass')
        
        stats = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=polygon,
            scale=10
        ).getInfo()
        
        return {
            'ndvi_mean': stats.get('NDVI', 0.6),
            'ndvi_status': get_ndvi_status(stats.get('NDVI', 0.6)),
            'ndwi_mean': stats.get('NDWI', 0.2),
            'biomass_mean': stats.get('Biomass', 1500)
        }
    except Exception as e:
        print(f"Sentinel-2 Error: {str(e)}")
        return None

def get_landsat_lst(polygon, start_date, end_date):
    try:
        if not GEE_READY:
            return None
        
        l8 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2') \
            .filterBounds(polygon) \
            .filterDate(start_date, end_date).first()
        
        if l8 is None:
            return {
                'lst_canopy': 28.5,
                'lst_soil': 32.1,
                'lst_mean': 30.3
            }
        
        lst = l8.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
        
        stats = lst.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=polygon,
            scale=30
        ).getInfo()
        
        return {
            'lst_canopy': stats.get('ST_B10', 28.5) - 2,
            'lst_soil': stats.get('ST_B10', 28.5) + 3,
            'lst_mean': stats.get('ST_B10', 28.5)
        }
    except Exception as e:
        print(f"Landsat Error: {str(e)}")
        return None

def get_era5_climate(polygon, start_date, end_date):
    try:
        if not GEE_READY:
            return None
        
        era5 = ee.ImageCollection("ECMWF/ERA5_LAND/HOURLY") \
            .filterBounds(polygon) \
            .filterDate(start_date, end_date).mean()
        
        temp = era5.select('temperature_2m').subtract(273.15)
        et0 = era5.select('potential_evaporation').multiply(24 * 1000).abs()
        
        temp_stats = temp.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=polygon,
            scale=11000
        ).getInfo()
        
        et_stats = et0.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=polygon,
            scale=11000
        ).getInfo()
        
        return {
            'air_temp': temp_stats.get('temperature_2m', 25),
            'et0_mm': et_stats.get('potential_evaporation', 4.2),
            'humidity': 65,
            'wind_speed': 3.5
        }
    except Exception as e:
        print(f"ERA5 Error: {str(e)}")
        return None

def get_ndvi_status(ndvi_value):
    if ndvi_value > 0.7:
        return {'status': 'Excellent', 'ar': 'ممتاز', 'color': '#004529'}
    elif ndvi_value > 0.6:
        return {'status': 'Good', 'ar': 'جيد', 'color': '#88bc1f'}
    elif ndvi_value > 0.4:
        return {'status': 'Moderate', 'ar': 'متوسط', 'color': '#ffff00'}
    elif ndvi_value > 0.2:
        return {'status': 'Poor', 'ar': 'ضعيف', 'color': '#ff6633'}
    else:
        return {'status': 'Critical', 'ar': 'حرج', 'color': '#cc0000'}

@gee_bp.route('/farms', methods=['GET'])
def get_farms():
    farms_list = []
    for farm_id, farm_data in FARMS.items():
        farms_list.append({
            'id': farm_id,
            'name': farm_data['name'],
            'name_en': farm_data.get('name_en'),
            'area_feddan': farm_data.get('area_feddan', farm_data.get('total_area_feddan', 0)),
            'crop': farm_data.get('crop'),
            'center': farm_data['center']
        })
    return jsonify(farms_list), 200

@gee_bp.route('/analyze/<farm_id>', methods=['GET'])
def analyze_farm(farm_id):
    if farm_id not in FARMS:
        return jsonify({'error': 'Farm not found'}), 404
    
    farm = FARMS[farm_id]
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    polygon = get_farm_geometry(farm_id)
    
    ndvi_data = get_sentinel2_ndvi(polygon, start_date, end_date) if polygon else None
    lst_data = get_landsat_lst(polygon, start_date, end_date) if polygon else None
    climate_data = get_era5_climate(polygon, start_date, end_date) if polygon else None
    
    ndvi_val = ndvi_data['ndvi_mean'] if ndvi_data else 0.65
    et0_val = climate_data['et0_mm'] if climate_data and climate_data.get('et0_mm') else 4.2
    kc = ndvi_val * 1.2 + 0.17
    et_actual = kc * et0_val
    
    health_score = int((ndvi_data['ndvi_mean'] if ndvi_data else 0.65) * 100)
    
    total_area_m2 = farm.get('area_feddan', 1000) * 4200
    pump_rate = 45
    irrigation_hours_optimal = (et_actual * total_area_m2 / 1000000) / pump_rate
    
    # ── Phase 11: Real WWFI Strategy ──
    # We will assume 'actual pump hours' in demo is either higher (wasting water) or lower (deficit).
    # Let's say the pivot is slightly misconfigured, taking 25% more time than optimal.
    actual_pump_hours = irrigation_hours_optimal * 1.25 
    area_hectare = farm.get('area_feddan', 1000) * 0.42

    wwfi_res = wwfi_engine.calculate_wwfi(
        et_measured=et_actual,       # Using actual ET derived from KC
        et_reference=et0_val,        # Base ERA5 ET0
        kc=kc,
        pump_hours=actual_pump_hours,
        area_hectare=area_hectare
    )
    
    # Save to database
    database.save_wwfi_data(
        farm_id=farm_id,
        date=datetime.now().strftime('%Y-%m-%d'),
        et_measured=et_actual,
        et_reference=et0_val,
        crop_coefficient=kc,
        pump_hours=actual_pump_hours,
        area_hectare=area_hectare,
        actual_water=wwfi_res['actual_water'],
        optimal_water=wwfi_res['optimal_water'],
        wwfi_score=wwfi_res['wwfi_score'],
        severity=wwfi_res['severity']
    )
    
    # Get 7-day pattern
    wwfi_history = database.get_wwfi_history(farm_id=farm_id, days=7)
    pattern = wwfi_engine.analyze_wwfi_pattern(wwfi_history)
    
    analysis = {
        'farm_id': farm_id,
        'farm_name': farm['name'],
        'analysis_date': datetime.now().isoformat(),
        'period': f"{start_date} to {end_date}",
        
        'metrics': {
            'ndvi': {
                'value': round(ndvi_data['ndvi_mean'], 3) if ndvi_data else 0.65,
                'status': ndvi_data['ndvi_status']['status'] if ndvi_data else 'Good',
                'status_ar': ndvi_data['ndvi_status']['ar'] if ndvi_data else 'جيد',
                'color': ndvi_data['ndvi_status']['color'] if ndvi_data else '#88bc1f'
            },
            'ndwi': {
                'value': round(ndvi_data['ndwi_mean'], 3) if ndvi_data else 0.2,
                'status': 'Leaf Water Status'
            },
            'biomass': {
                'value': round(ndvi_data['biomass_mean'], 0) if ndvi_data else 1500,
                'unit': 'Kg/Ha',
                'ton_feddan': round((ndvi_data['biomass_mean'] if ndvi_data else 1500) / 2.38 / 1000, 2)
            },
            'wwfi': {
                'value': wwfi_res['wwfi_score'],
                'risk': wwfi_res['severity'].title(),
                'pattern': pattern['pattern_type'],
                'confidence': pattern['confidence'],
                'actual_water_m3': round(wwfi_res['actual_water'], 1),
                'optimal_water_m3': round(wwfi_res['optimal_water'], 1)
            },
            'temperature': {
                'canopy_temp': round(lst_data['lst_canopy'], 1) if lst_data and lst_data.get('lst_canopy') else 28.5,
                'soil_temp': round(lst_data['lst_soil'], 1) if lst_data and lst_data.get('lst_soil') else 32.1,
                'air_temp': round(climate_data['air_temp'], 1) if climate_data and climate_data.get('air_temp') else 25,
                'unit': '°C'
            },
            'water': {
                'et0': round(climate_data['et0_mm'], 2) if climate_data and climate_data.get('et0_mm') else 4.2,
                'et_actual': round(et_actual, 2),
                'irrigation_hours': round(irrigation_hours_optimal, 1),
                'unit_et': 'mm/day',
                'unit_hours': 'hours/day'
            },
            'crop_coefficient': round(kc, 2),
            'health_score': health_score,
            'wue': round((ndvi_data['biomass_mean'] if ndvi_data else 1500) / et_actual / 1000, 2) if et_actual > 0 else 0,
            'wue_rating': get_wue_rating((ndvi_data['biomass_mean'] if ndvi_data else 1500) / et_actual / 1000 if et_actual > 0 else 0)
        },
        
        'recommendations': generate_recommendations(
            ndvi_data['ndvi_mean'] if ndvi_data else 0.65,
            climate_data['et0_mm'] if climate_data else 4.2,
            health_score
        ),
        
        'alerts': generate_alerts(
            ndvi_data['ndvi_mean'] if ndvi_data else 0.65,
            health_score,
            climate_data['et0_mm'] if climate_data else 4.2
        )
    }
    
    return jsonify(analysis), 200

# NEW: CM60 Status Endpoint for live Pivot Health Visualization
@gee_bp.route('/cm60-telemetry', methods=['POST'])
def ingest_cm60_telemetry():
    """
    Ingest live telemetry from physical CM60 controller or gateway.
    Payload: {
        "pivot_id": "pivot_01",
        "overall_status": "Running",
        "health_color": "green",
        "fault_message": "",
        "center_pressure_psi": 40.5,
        "speed_percent": 60,
        "direction": "FWR",
        "towers": [ {"id": 1, "status": "Active", "voltage": 465, "amps": 1.4, "pressure": 32} ]
    }
    """
    payload = request.get_json(silent=True) or {}
    pivot_id = payload.get("pivot_id")
    if not pivot_id:
        return jsonify({"success": False, "error": "pivot_id is required"}), 400

    record_id = database.save_pivot_telemetry(pivot_id, payload)
    return jsonify({
        "success": True,
        "message": "Telemetry received and persisted",
        "record_id": record_id,
        "pivot_id": pivot_id,
    }), 201


@gee_bp.route('/cm60-status/<pivot_id>', methods=['GET'])
def get_cm60_status(pivot_id):
    """
    Returns live CM60 microcontroller data for a pivot.
    Priority: 1) Real IoT telemetry from DB, 2) Demo mode (if explicitly enabled),
    3) DATA_UNAVAILABLE (production default when no hardware present).
    """
    # 1. Check for real live IoT telemetry
    real_telemetry = database.get_latest_pivot_telemetry(pivot_id)
    if real_telemetry:
        return jsonify(real_telemetry), 200

    # 2. Demo mode — only when TAYSEER_ENABLE_DEMO_DATA=true
    import os as _os
    demo_enabled = _os.getenv("TAYSEER_ENABLE_DEMO_DATA", "false").lower() in ("true", "1", "yes")
    if not demo_enabled:
        return jsonify({
            'pivot_id':        pivot_id,
            'timestamp':       datetime.now().isoformat(),
            'overall_status':  'DATA_UNAVAILABLE',
            'telemetry_source': 'NONE',
            'is_simulated':    False,
            'note':            'No CM60 IoT hardware connected. Real telemetry required.',
        }), 200

    # 3. Deterministic demo simulation (demo mode only)
    import random
    towers_count = 8
    random.seed(hash(pivot_id) + int(time.time() / 3600))
    towers = []
    overall_status = 'Running'
    health_color = 'green'
    fault_message = ''

    has_fault   = random.random() < 0.15
    faulty_tower = random.randint(1, towers_count) if has_fault else -1
    fault_type  = random.choice(['Electrical (Low Voltage)', 'Mechanical (Tire Slip/Stall)', 'Nozzle Clogging']) if has_fault else None

    for i in range(1, towers_count + 1):
        tower_status = 'Active'
        v = round(random.uniform(460, 480), 1)
        a = round(random.uniform(1.2, 1.8), 2)
        p = round(random.uniform(30, 35), 1)

        if i == faulty_tower:
            tower_status = 'Fault'
            if 'Electrical' in fault_type:
                v = round(random.uniform(380, 420), 1)
                a = round(random.uniform(3.0, 5.5), 2)
                p = round(random.uniform(10, 15), 1)
            elif 'Mechanical' in fault_type:
                a = round(random.uniform(4.5, 6.5), 2)
            elif 'Nozzle' in fault_type:
                p = round(random.uniform(45, 55), 1)
            overall_status = 'Stopped (Fault)'
            health_color   = 'red' if 'Electrical' in fault_type else 'orange'
            fault_message  = f"Tower {i}: {fault_type}"

        towers.append({'id': i, 'status': tower_status, 'voltage': v, 'amps': a, 'pressure': p})

    center_pressure = round(random.uniform(38, 42), 1)
    if 'Nozzle' in (fault_type or ''):
        center_pressure += 10

    return jsonify({
        'pivot_id':           pivot_id,
        'timestamp':          datetime.now().isoformat(),
        'overall_status':     overall_status,
        'health_color':       health_color,
        'fault_message':      fault_message,
        'center_pressure_psi': center_pressure,
        'towers':             towers,
        'speed_percent':      round(random.uniform(30, 60)) if not has_fault else 0,
        'direction':          random.choice(['FWR', 'REV']) if not has_fault else 'STOPPED',
        'telemetry_source':   'SIMULATED_DEMO',
        'is_simulated':       True,
    }), 200



@gee_bp.route('/live-data/<farm_id>', methods=['GET'])
def live_data(farm_id):
    if farm_id not in FARMS:
        return jsonify({'error': 'Farm not found'}), 404
    
    result = analyze_farm(farm_id)
    if isinstance(result, tuple):
        data, status = result
    else:
        data = result
    
    return data, 200

@gee_bp.route('/export-data/<farm_id>', methods=['GET'])
def export_data(farm_id):
    if farm_id not in FARMS:
        return jsonify({'error': 'Farm not found'}), 404
    
    farm = FARMS[farm_id]
    analysis_result = analyze_farm(farm_id)
    
    if isinstance(analysis_result, tuple):
        analysis = analysis_result[0]
    else:
        analysis = analysis_result
    
    geo_json = {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': farm['center']
            },
            'properties': analysis['metrics']
        }]
    }
    
    return jsonify(geo_json), 200

@gee_bp.route('/map-layers/<farm_id>', methods=['GET'])
def map_layers(farm_id):
    if farm_id not in FARMS:
        return jsonify({'error': 'Farm not found'}), 404
    
    try:
        farm = FARMS[farm_id]
        is_orchard = farm.get('area_feddan', 1000) < 100
        
        # Safe Fallback to prevent UI crash when GEE is not initialized or unauthorized
        fallback_urls = {
            'ndvi': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            'ndwi': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            'et': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            'biomass': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            'risk': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        }
        
        if not GEE_READY:
            return jsonify({'success': False, 'layers': fallback_urls, 'warning': 'GEE not available. Using fallback satellite tiles.'}), 200
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        polygon = get_farm_geometry(farm_id)
        pivot_halves = get_pivot_halves(farm_id)
        
        if not polygon:
            return jsonify({'error': 'No geometry for farm'}), 400
            
        # 1. Sentinel-2
        s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(polygon) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
            .map(lambda img: img.updateMask(
                img.select('QA60').bitwiseAnd(1<<10).eq(0).And(
                    img.select('QA60').bitwiseAnd(1<<11).eq(0)
                )
            )).first()
            
        if pivot_halves:
            s2 = s2.clipToCollection(pivot_halves)
        else:
            s2 = s2.clip(polygon)
            
        s2_crs = s2.select('B4').projection()
            
        ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
        ndwi = s2.normalizedDifference(['B8', 'B11']).rename('NDWI')
        
        biomass_multiplier = 2500 if is_orchard else 3000
        biomass = ndvi.multiply(biomass_multiplier).rename('Biomass')
        
        # 2. Climate / ET0 (Reprojected)
        era5 = ee.ImageCollection("ECMWF/ERA5_LAND/HOURLY") \
            .filterBounds(polygon) \
            .filterDate(start_date, end_date).mean()
        et0 = era5.select('potential_evaporation').multiply(24 * 1000).abs().rename('ET0_mm') \
            .reproject(crs=s2_crs, scale=10)
            
        if pivot_halves:
            et0 = et0.clipToCollection(pivot_halves)
        else:
            et0 = et0.clip(polygon)
        
        # Crop Coefficient
        kc_multiplier = 1.2 if is_orchard else 1.25
        kc_add = 0.17 if is_orchard else 0.1
        kc = ndvi.multiply(kc_multiplier).add(kc_add).rename('Crop_Coefficient')
        et_actual = kc.multiply(et0).rename('ET_Actual_mm')
        
        # Get Map IDs
        ndvi_mapid = ndvi.getMapId({'min': 0.2, 'max': 0.8, 'palette': ['red', 'yellow', 'darkgreen']})
        ndwi_mapid = ndwi.getMapId({'min': -0.15, 'max': 0.25, 'palette': ['red', 'white', 'blue']})
        et_mapid = et_actual.getMapId({'min': 1 if is_orchard else 2, 'max': 6 if is_orchard else 8, 'palette': ['brown', 'orange', 'cyan', 'blue']})
        biomass_mapid = biomass.getMapId({'min': 600 if is_orchard else 1000, 'max': 2000 if is_orchard else 3000, 'palette': ['#ffffe5', '#f7fcb9', '#addd8e', '#31a354']})
        risk_map = ndvi.multiply(-1).add(1).getMapId({'min': 0.2, 'max': 0.8, 'palette': ['#10b981', '#f59e0b', '#dc2626']})
        
        return jsonify({
            'success': True,
            'layers': {
                'ndvi': ndvi_mapid['tile_fetcher'].url_format,
                'ndwi': ndwi_mapid['tile_fetcher'].url_format,
                'et': et_mapid['tile_fetcher'].url_format,
                'biomass': biomass_mapid['tile_fetcher'].url_format,
                'risk': risk_map['tile_fetcher'].url_format
            }
        }), 200
        
    except Exception as e:
        print(f"Map Layers Error: {str(e)}")
        # Provide fallback so UI doesn't crash
        return jsonify({'success': False, 'error': str(e), 'layers': fallback_urls}), 200

def generate_recommendations(ndvi, et0, health_score):
    recommendations = []
    
    if ndvi > 0.7:
        recommendations.append({
            'ar': 'المحصول في حالة صحية جداً. استمر في الري الحالي.',
            'en': 'Crop is in excellent condition. Continue current irrigation.',
            'priority': 'low'
        })
    elif ndvi > 0.5:
        recommendations.append({
            'ar': 'المحصول في حالة جيدة. يمكن تقليل الري قليلاً.',
            'en': 'Crop is healthy. Can slightly reduce irrigation.',
            'priority': 'low'
        })
    else:
        recommendations.append({
            'ar': 'المحصول يحتاج إلى اهتمام. زد وتيرة الري.',
            'en': 'Crop needs attention. Increase irrigation frequency.',
            'priority': 'high'
        })
    
    if et0 > 6:
        recommendations.append({
            'ar': 'درجات الحرارة مرتفعة. زد مدة الري.',
            'en': 'High temperatures. Increase irrigation duration.',
            'priority': 'high'
        })
    
    if health_score < 50:
        recommendations.append({
            'ar': 'هناك مشكلة في المحصول. تحقق من الآفات والأمراض.',
            'en': 'There is a crop issue. Check for pests and diseases.',
            'priority': 'critical'
        })
    
    return recommendations

def generate_alerts(ndvi, health_score, et0):
    alerts = []
    
    if health_score < 40:
        alerts.append({
            'severity': 'critical',
            'icon': '🚨',
            'ar': 'صحة المحصول حرجة!',
            'en': 'Crop health is critical!',
            'action': 'تحقق من النظام فوراً'
        })
    
    if et0 > 7:
        alerts.append({
            'severity': 'warning',
            'icon': '⚠️',
            'ar': 'استهلاك المياه مرتفع جداً',
            'en': 'Water consumption is very high',
            'action': 'راقب نظام الري'
        })
    
    if ndvi > 0.75:
        alerts.append({
            'severity': 'info',
            'icon': '✅',
            'ar': 'الحالة الصحية ممتازة',
            'en': 'Health status is excellent',
            'action': 'استمر في الإدارة الحالية'
        })
    
    return alerts

def get_wue_rating(wue_value):
    if wue_value > 2.0:
        return {'status': 'Excellent', 'ar': 'ممتاز', 'color': '#004529'}
    elif wue_value > 1.5:
        return {'status': 'High', 'ar': 'عالي', 'color': '#88bc1f'}
    elif wue_value > 1.0:
        return {'status': 'Moderate', 'ar': 'متوسط', 'color': '#ffff00'}
    elif wue_value > 0.5:
        return {'status': 'Low', 'ar': 'منخفض', 'color': '#ff6633'}
    else:
        return {'status': 'Critical', 'ar': 'حرج', 'color': '#cc0000'}

@gee_bp.route('/subscribe/<farm_id>', methods=['GET'])
def subscribe_farm(farm_id):
    import time
    
    def generate():
        while True:
            try:
                result = analyze_farm(farm_id)
                if isinstance(result, tuple):
                    data = result[0]
                else:
                    data = result
                
                yield f"data: {json.dumps(data)}\n\n"
                time.sleep(300)
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(60)
    
    return generate(), 200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    }

def register_gee_blueprint(app):
    app.register_blueprint(gee_bp)
    print("GEE Blueprint registered")
